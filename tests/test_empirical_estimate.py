import csv
import json
import yaml
import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

from ion_CSP.empirical_estimate import EmpiricalEstimation


@pytest.fixture
def estimator(tmp_path: Path):
    """每个测试都获得一个全新的、干净的 estimator 实例"""
    return EmpiricalEstimation(
        work_dir=tmp_path / "test_work_dir",
        folders=["cation_1", "anion_1"],
        ratios=[1, 1],
        sort_by="density",
    )


# ==================== 测试初始化参数校验 ====================
def test_init_invalid_sort_by():
    with pytest.raises(
        ValueError, match="must be either 'density' 'nitrogen' or 'NC_ratio'"
    ):
        EmpiricalEstimation(
            work_dir=Path("/tmp"), folders=["a"], ratios=[1], sort_by="invalid"
        )


def test_init_mismatched_folders_ratios():
    with pytest.raises(
        ValueError, match="The number of folders must match the number of ratios"
    ):
        EmpiricalEstimation(
            work_dir=Path("/tmp"), folders=["a", "b"], ratios=[1], sort_by="density"
        )


# ==================== 测试 _check_multiwfn_executable ====================
def test_check_multiwfn_executable_found():
    with patch("shutil.which", return_value="/usr/local/bin/Multiwfn"):
        est = EmpiricalEstimation(
            work_dir=Path("/tmp"), folders=["a"], ratios=[1], sort_by="density"
        )
        assert est.multiwfn_path == "/usr/local/bin/Multiwfn"


def test_check_multiwfn_executable_not_found():
    with patch("shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="No detected Multiwfn executable"):
            EmpiricalEstimation(
                work_dir=Path("/tmp"), folders=["a"], ratios=[1], sort_by="density"
            )


# ==================== 测试 _multiwfn_cmd_build ====================
def test_multiwfn_cmd_build_success(estimator: EmpiricalEstimation):
    # 创建真实文件
    input_path = estimator.gaussian_dir / "input.txt"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text("input\n12\n0\nq\n", encoding="utf-8")
    output_path = estimator.gaussian_dir / "output.txt"
    with (
        patch("subprocess.run") as mock_run,
        patch("pathlib.Path.unlink") as mock_unlink,
    ):
        estimator._multiwfn_cmd_build("input\n12\n0\nq\n", output_path=output_path)
        # 检查 subprocess 调用
        mock_run.assert_called_once()
        stdin = mock_run.call_args[1]["stdin"]
        stdout = mock_run.call_args[1]["stdout"]
        # 检查 stdin 是文件对象，且 name 是 input.txt
        assert hasattr(stdin, "name") and stdin.name == str(input_path)
        assert hasattr(stdout, "name") and stdout.name == str(output_path)
        # 检查 input.txt 被删除
        mock_unlink.assert_called_once_with(missing_ok=True)


def test_multiwfn_cmd_build_failure(estimator: EmpiricalEstimation):
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmd")):
        with pytest.raises(subprocess.CalledProcessError):
            estimator._multiwfn_cmd_build("input\n12\n0\nq\n")


# ==================== 测试 _single_multiwfn_fchk_to_json ====================
def test_single_multiwfn_fchk_to_json_success(estimator: EmpiricalEstimation):
    # 1. 确保目录存在
    estimator.gaussian_dir.mkdir(parents=True, exist_ok=True)
    # 2. 创建 fchk 文件
    fchk_path = estimator.gaussian_dir / "cation_1" / "test.fchk"
    fchk_path.parent.mkdir(parents=True, exist_ok=True)
    fchk_path.touch()
    # 3. 定义 Multiwfn 输出内容（手动输入，无隐藏字符）
    output_content = """================= Summary of surface analysis =================
Volume:   504.45976 Bohr^3  (  74.75322 Angstrom^3)
Estimated density according to mass and volume (M/V):    1.5557 g/cm^3
Overall surface area:         320.06186 Bohr^2  (  89.62645 Angstrom^2)
Positive surface area:          0.00000 Bohr^2  (   0.00000 Angstrom^2)
Negative surface area:        320.06186 Bohr^2  (  89.62645 Angstrom^2)
Overall average value:   -0.19677551 a.u. (   -123.47860 kcal/mol)
Positive average value:          NaN a.u. (          NaN kcal/mol)
Negative average value:  -0.19677551 a.u. (   -123.47860 kcal/mol)
"""
    # 4. 创建 output.txt 文件（初始为空）
    output_path = estimator.gaussian_dir / "output.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("", encoding="utf-8")

    # 5. 模拟 subprocess.run，让它真实写入 output.txt
    def mock_subprocess_run(*args, **kwargs):
        stdout = kwargs.get("stdout")
        if stdout and hasattr(stdout, "write"):
            stdout.write(output_content)
        return MagicMock(returncode=0)

    with (
        patch("subprocess.run", side_effect=mock_subprocess_run),
        patch("pathlib.Path.unlink") as mock_unlink,
        patch("shutil.copyfile") as mock_copyfile,
    ):
        # 6. 执行被测方法
        result = estimator._single_multiwfn_fchk_to_json(fchk_path)
        # 7. 检查返回值
        assert result is True, "Expected _single_multiwfn_fchk_to_json to return True"
        # 8. 检查 output.txt 是否被正确写入
        assert output_path.read_text(encoding="utf-8") == output_content
        # 9. 检查 json 文件是否生成
        json_path = fchk_path.with_suffix(".json")
        assert json_path.exists()
        json_data = json.loads(json_path.read_text())
        assert json_data["refcode"] == "test"
        assert json_data["density"] == "1.5557"
        assert json_data["ion_type"] == "anion"
        # 10. 检查 copyfile 调用（关键字参数！）
        optimized_path = (
            estimator.gaussian_dir / "Optimized" / "cation_1" / "test.json"
        )
        mock_copyfile.assert_called_once_with(src=json_path, dst=optimized_path)
        # 11. 检查 unlink 调用
        mock_unlink.assert_any_call(missing_ok=True)
        assert mock_unlink.call_count == 2


def test_single_multiwfn_fchk_to_json_invalid_fchk(
    estimator: EmpiricalEstimation, tmp_path: Path
):
    fchk_path = tmp_path / "bad.fchk"
    fchk_path.touch()
    output_content = "Invalid output"  # 没有匹配的正则
    with (
        patch("subprocess.run"),
        patch("pathlib.Path.open", mock_open(read_data=output_content)),
    ):
        result = estimator._single_multiwfn_fchk_to_json(fchk_path)
        assert result is False


# ==================== 测试 _multiwfn_process_fchk_to_json ====================
def test_multiwfn_process_fchk_to_json_success(estimator: EmpiricalEstimation):
    # 1. 确保目录结构存在
    estimator.gaussian_dir.mkdir(parents=True, exist_ok=True)
    # 2. 创建一个文件夹和两个 .fchk 文件（模拟输入）
    folder = "cation_1"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    (folder_path / "test1.fchk").touch()
    (folder_path / "test2.fchk").touch()
    # 3. 模拟 _single_multiwfn_fchk_to_json，只关心它是否被调用
    with patch.object(estimator, "_single_multiwfn_fchk_to_json") as mock_single:
        # 4. 执行被测方法
        estimator._multiwfn_process_fchk_to_json(folder)
        # 5. 验证：它被调用了 2 次（每个 .fchk 一次）
        assert mock_single.call_count == 2, (
            f"Expected 2 calls, got {mock_single.call_count}"
        )
        # 6. 验证：调用的参数是两个正确的 .fchk 文件路径
        mock_single.assert_any_call(folder_path / "test1.fchk")
        mock_single.assert_any_call(folder_path / "test2.fchk")


def test_multiwfn_process_fchk_to_json_bad_files(estimator: EmpiricalEstimation):
    # 1. 确保目录存在（必须用 estimator.gaussian_dir！）
    estimator.gaussian_dir.mkdir(parents=True, exist_ok=True)
    # 2. 创建文件夹和文件在源码实际使用的路径下
    folder = "anion_1"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    # 3. 创建两个会失败的 .fchk 文件
    (folder_path / "bad1.fchk").touch()
    (folder_path / "bad2.fchk").touch()
    # 4. 模拟 _single_multiwfn_fchk_to_json 返回 False（表示处理失败）
    with patch.object(
        estimator, "_single_multiwfn_fchk_to_json", side_effect=[False, False]
    ) as mock_single:
        # 5. 执行被测方法
        estimator._multiwfn_process_fchk_to_json(folder)
        # 6. 验证：它被调用了两次（每个文件一次）
        assert mock_single.call_count == 2
        mock_single.assert_any_call(folder_path / "bad1.fchk")
        mock_single.assert_any_call(folder_path / "bad2.fchk")
        # 7. 验证：Bad 目录被创建了
        bad_dir = estimator.gaussian_dir / "Bad" / folder
        assert bad_dir.exists(), f"Bad directory not created: {bad_dir}"
        # 8. 验证：两个 .fchk 文件被移动到了 Bad 目录
        assert (bad_dir / "bad1.fchk").exists()
        assert (bad_dir / "bad2.fchk").exists()
        # 9. 验证：原始位置的文件被移走了
        assert not (folder_path / "bad1.fchk").exists()
        assert not (folder_path / "bad2.fchk").exists()


# ==================== 测试 _single_multiwfn_log_to_gjf ====================
def test_single_multiwfn_log_to_gjf_success(estimator: EmpiricalEstimation):
    # 1. 确保目录存在
    estimator.gaussian_dir.mkdir(parents=True, exist_ok=True)
    # 2. 创建 .log 文件
    log_path = estimator.gaussian_dir / "test.log"
    log_path.touch()

    # 3. 模拟 subprocess.run 成功执行，并创建 gjf 文件
    def mock_subprocess_run(*args, **kwargs):
        # 模拟 Multiwfn 成功执行
        gjf_path = estimator.gaussian_result_dir / "test" / "test.gjf"
        gjf_path.parent.mkdir(parents=True, exist_ok=True)
        gjf_path.write_text("! Mocked gjf content\n", encoding="utf-8")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=mock_subprocess_run) as mock_run:
        # 4. 执行方法
        result = estimator._single_multiwfn_log_to_gjf("test", log_path)
        # 5. 验证：返回 True
        assert result is True, "Expected True when conversion succeeds"
        # 6. 验证：subprocess.run 被调用一次
        mock_run.assert_called_once()
        # 7. 验证：gjf 文件被创建
        gjf_path = estimator.gaussian_result_dir / "test" / "test.gjf"
        assert gjf_path.exists()
        assert gjf_path.read_text() == "! Mocked gjf content\n"


def test_single_multiwfn_log_to_gjf_failure(estimator: EmpiricalEstimation, caplog):
    # 1. 确保目录存在
    estimator.gaussian_dir.mkdir(parents=True, exist_ok=True)
    # 2. 创建 .log 文件
    log_path = estimator.gaussian_dir / "test.log"
    log_path.touch()
    # 3. 模拟 subprocess.run 抛出异常
    with patch("subprocess.run", side_effect=Exception("Multiwfn failed")) as mock_run:
        # 4. 执行方法
        result = estimator._single_multiwfn_log_to_gjf("test", log_path)
        # 5. 验证：返回 False
        assert result is False, "Expected False when Multiwfn fails"
        # 6. 验证：subprocess.run 被调用一次
        mock_run.assert_called_once()
        # 7. 验证：日志中记录了错误
        assert "Error with processing" in caplog.text
        assert "Multiwfn failed" in caplog.text
        assert caplog.records[-1].levelname == "ERROR"


# ==================== 测试 _gaussian_log_to_optimized_gjf ====================
def test_gaussian_log_to_optimized_gjf_success(estimator: EmpiricalEstimation):
    # 1. 确保 gaussian_dir 存在
    estimator.gaussian_dir.mkdir(parents=True, exist_ok=True)

    # 2. 创建文件夹和 .log 文件在源码实际使用的路径下
    folder = "cation_1"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)

    # 3. 创建两个 .log 文件
    (folder_path / "test1.log").touch()
    (folder_path / "test2.log").touch()

    # 4. 模拟 _single_multiwfn_log_to_gjf 被调用两次
    with patch.object(
        estimator, "_single_multiwfn_log_to_gjf", return_value=True
    ) as mock_func:
        # 5. 执行被测方法
        estimator._gaussian_log_to_optimized_gjf(folder)

        # 6. 验证：它被调用了 2 次（每个 .log 文件一次）
        assert mock_func.call_count == 2, (
            f"Expected 2 calls, got {mock_func.call_count}"
        )

        # 7. 验证：调用的参数是正确的 (folder, log_path)
        mock_func.assert_any_call(folder, folder_path / "test1.log")
        mock_func.assert_any_call(folder, folder_path / "test2.log")


# ==================== 测试 _read_gjf_elements ====================
def test_read_gjf_elements_success(estimator: EmpiricalEstimation, tmp_path: Path):
    gjf_content = """# B3LYP/6-31G*
0 1
C  0.0 0.0 0.0
N  1.0 0.0 0.0
O  0.0 1.0 0.0
"""
    gjf_path = tmp_path / "test.gjf"
    gjf_path.write_text(gjf_content)
    result = estimator._read_gjf_elements(gjf_path)
    assert result == {"C": 1, "N": 1, "O": 1}


def test_read_gjf_elements_empty(estimator: EmpiricalEstimation, tmp_path: Path):
    gjf_path = tmp_path / "empty.gjf"
    gjf_path.write_text("")
    result = estimator._read_gjf_elements(gjf_path)
    assert result == {}


# ==================== 测试 _generate_combinations ====================
def test_generate_combinations_gjf(estimator: EmpiricalEstimation):
    # 模拟两个文件夹，各两个文件
    cation_dir = estimator.gaussian_result_dir / "cation_1"
    anion_dir = estimator.gaussian_result_dir / "anion_1"
    cation_dir.mkdir(parents=True)
    anion_dir.mkdir()
    (cation_dir / "c1.gjf").touch()
    (cation_dir / "c2.gjf").touch()
    (anion_dir / "a1.gjf").touch()
    (anion_dir / "a2.gjf").touch()
    combos = estimator._generate_combinations(".gjf")
    assert len(combos) == 4  # 2x2
    for combo in combos:
        assert len(combo) == 2
        assert all(f.suffix == ".gjf" for f in combo)


# ==================== 测试 nitrogen_content_estimate ====================
def test_nitrogen_content_estimate(estimator: EmpiricalEstimation):
    # 1. 创建测试 .gjf 文件（在 gaussian_result_dir 下）
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True, exist_ok=True)
    (opt_dir / "anion_1").mkdir(exist_ok=True)

    cation_gjf = opt_dir / "cation_1" / "c1.gjf"
    anion_gjf = opt_dir / "anion_1" / "a1.gjf"

    cation_gjf.write_text("""# B3LYP/6-31G*
0 1
N 0 0 0
N 1 0 0
C 0 1 0
""")

    anion_gjf.write_text("""# B3LYP/6-31G*
0 1
C 0 0 0
C 1 0 0
H 0 1 0
H 1 1 0
""")

    # 2. 执行方法（它会自动生成 CSV 在 gaussian_dir）
    estimator.nitrogen_content_estimate()

    # 3. 验证：CSV 被生成在正确位置（gaussian_dir）
    csv_path = estimator.gaussian_dir / "sorted_nitrogen.csv"
    assert csv_path.exists(), f"CSV file not generated: {csv_path}"

    # 4. 验证内容
    with open(csv_path, "r") as f:
        reader = csv.reader(f)
        rows = list(reader)
        assert rows[0] == ["Component 1", "Component 2", "Nitrogen_Content"]
        # 氮含量计算验证
        assert rows[1] == ["cation_1/c1", "anion_1/a1", "0.4241"]


# ==================== 测试 carbon_nitrogen_ratio_estimate ====================
def test_carbon_nitrogen_ratio_estimate(estimator: EmpiricalEstimation):
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True, exist_ok=True)
    (opt_dir / "anion_1").mkdir(exist_ok=True)

    cation_gjf = opt_dir / "cation_1" / "c1.gjf"
    anion_gjf = opt_dir / "anion_1" / "a1.gjf"

    cation_gjf.write_text("""# B3LYP/6-31G*
0 1
C 0 0 0
N 1 0 0
N 2 0 0
O 0 1 0
""")

    anion_gjf.write_text("""# B3LYP/6-31G*
0 1
C 0 0 0
C 1 0 0
N 0 1 0
""")

    estimator.carbon_nitrogen_ratio_estimate()

    csv_path = estimator.gaussian_dir / "specific_NC_ratio.csv"
    assert csv_path.exists(), f"CSV file not generated: {csv_path}"

    with open(csv_path, "r") as f:
        reader = csv.reader(f)
        rows = list(reader)
        assert rows[0] == ["Component 1", "Component 2", "N_C_Ratio", "O_Atoms"]
        assert rows[1] == ["cation_1/c1", "anion_1/a1", "1.0", "1"]


# ==================== 测试 empirical_estimate ====================
def test_empirical_estimate(estimator: EmpiricalEstimation):
    # 创建 .json 文件（在 gaussian_result_dir/Optimized 下）
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True, exist_ok=True)
    (opt_dir / "anion_1").mkdir(exist_ok=True)

    cation_json = opt_dir / "cation_1" / "c1.json"
    anion_json = opt_dir / "anion_1" / "a1.json"

    cation_json.write_text(
        json.dumps(
            {
                "refcode": "c1",
                "ion_type": "cation",
                "molecular_mass": 10.0,
                "volume": "50.0",
                "positive_surface_area": "100.0",
                "positive_average_value": "-10.0",
                "negative_surface_area": "0.0",
                "negative_average_value": "NaN",
            }
        )
    )

    anion_json.write_text(
        json.dumps(
            {
                "refcode": "a1",
                "ion_type": "anion",
                "molecular_mass": 20.0,
                "volume": "80.0",
                "positive_surface_area": "0.0",
                "positive_average_value": "NaN",
                "negative_surface_area": "120.0",
                "negative_average_value": "-20.0",
            }
        )
    )

    estimator.empirical_estimate()

    csv_path = estimator.gaussian_dir / "sorted_density.csv"
    assert csv_path.exists(), f"CSV file not generated: {csv_path}"

    with open(csv_path, "r") as f:
        reader = csv.reader(f)
        rows = list(reader)
        assert rows[0] == ["Component 1", "Component 2", "Pred_Density"]
        # 验证密度值在合理范围
        assert float(rows[1][-1]) > 0.05


# ==================== 测试 make_combo_dir ====================
def test_make_combo_dir_success(estimator: EmpiricalEstimation, tmp_path: Path):
    # 1. 创建 CSV 文件在正确位置：gaussian_dir
    csv_path = estimator.gaussian_dir / "sorted_density.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("""Component 1,Component 2,Pred_Density
cation_1/c1,anion_1/a1,1.2345
cation_1/c2,anion_1/a2,1.1234
""")

    # 2. 创建对应的 .gjf 和 .json 文件（在 gaussian_result_dir 下）
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True, exist_ok=True)
    (opt_dir / "anion_1").mkdir(exist_ok=True)
    (opt_dir / "cation_1" / "c1.gjf").touch()
    (opt_dir / "cation_1" / "c1.json").touch()
    (opt_dir / "anion_1" / "a1.gjf").touch()
    (opt_dir / "anion_1" / "a1.json").touch()

    # 3. 创建 config.yaml 文件在 base_dir 下
    config_path = estimator.base_dir / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
gen_opt:
  species: ["N2O.json", "H2O.json"]
  ion_numbers: [2, 1]
""",
        encoding="utf-8",
    )

    # 4. 执行方法
    target_dir = tmp_path / "test_combos"
    estimator.make_combo_dir(target_dir, num_combos=1, ion_numbers=[1, 1])

    # 5. 验证
    combo_dir = target_dir / "combo_1"
    assert combo_dir.exists()
    assert (combo_dir / "c1.gjf").exists()
    assert (combo_dir / "c1.json").exists()
    assert (combo_dir / "config.yaml").exists()

    # 6. 验证生成的 config.yaml 内容正确（可选）
    generated_config = yaml.safe_load((combo_dir / "config.yaml").read_text())
    assert generated_config["gen_opt"]["species"] == ["c1.gjf", "a1.gjf"]
    assert generated_config["gen_opt"]["ion_numbers"] == [1, 1]


def test_make_combo_dir_no_csv(estimator: EmpiricalEstimation, tmp_path: Path):
    # 不创建任何 CSV 文件，触发 FileNotFoundError
    with pytest.raises(
        FileNotFoundError,
        match=r"CSV file .*/sorted_density\.csv does not exist in the Gaussian optimized directory\.",
    ):
        estimator.make_combo_dir(tmp_path, num_combos=1, ion_numbers=[1, 1])


# ==================== 测试 _copy_combo_file ====================
def test_copy_combo_file_success(estimator: EmpiricalEstimation, tmp_path: Path):
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True)
    (opt_dir / "cation_1" / "c1.gjf").touch()
    combo_folder = tmp_path / "combo_1"
    combo_folder.mkdir()
    estimator._copy_combo_file(combo_folder, "cation_1/c1", ".gjf")
    assert (combo_folder / "c1.gjf").exists()


def test_copy_combo_file_source_missing(estimator: EmpiricalEstimation, tmp_path: Path):
    combo_folder = tmp_path / "combo_1"
    combo_folder.mkdir()
    with pytest.raises(FileNotFoundError, match="Source file .* does not exist"):
        estimator._copy_combo_file(combo_folder, "cation_1/c1", ".gjf")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=ion_CSP.empirical_estimate"])