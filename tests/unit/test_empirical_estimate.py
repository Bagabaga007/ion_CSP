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


# ==================== 测试 _check_multiwfn_executable 私有函数 ====================
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


# ==================== 测试 _multiwfn_cmd_build 私有函数 ====================
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


def test_multiwfn_cmd_build_failure(estimator: EmpiricalEstimation, caplog):
    """测试当 Multiwfn 命令执行失败时，返回 False 并记录错误日志"""
    # 模拟 subprocess.run 抛出 CalledProcessError
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmd")):
        result = estimator._multiwfn_cmd_build("input\n12\n0\nq\n")

    # 验证：函数返回 False
    assert result is False

    # 验证：日志中记录了错误
    assert (
        "Error executing Multiwfn command with input input\n12\n0\nq\n: Command 'cmd' returned non-zero exit status 1."
        in caplog.text
    )
    assert caplog.records[-1].levelname == "ERROR"


# ==================== 测试 multiwfn_process_fchk_to_json 函数 ====================
def test_multiwfn_process_fchk_to_json_specific_directory(
    estimator: EmpiricalEstimation, caplog
):
    """测试当传入 specific_directory 时，仅处理指定文件夹"""
    # 1. 创建一个测试文件夹和 .fchk 文件
    folder = "cation_1"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    (folder_path / "test.fchk").touch()

    # 2. 模拟 _multiwfn_process_fchk_to_json 被调用
    with patch.object(estimator, "_multiwfn_process_fchk_to_json") as mock_process:
        # 3. 调用被测方法：传入 specific_directory
        estimator.multiwfn_process_fchk_to_json(specific_directory=folder)

        # 4. 验证：仅调用一次，且参数正确
        mock_process.assert_called_once_with(folder)
        # 验证未创建目标目录（因为该目录由 _multiwfn_process_fchk_to_json 内部处理）
        assert not (estimator.gaussian_result_dir / folder).exists()  # 该方法不创建它

    # 5. 验证日志未打印“已创建目录”信息（因为不是遍历模式）
    assert "mkdir" not in caplog.text


def test_multiwfn_process_fchk_to_json_all_folders(
    estimator: EmpiricalEstimation, caplog
):
    """测试当 specific_directory 为 None 时，遍历所有初始化的文件夹并创建结果目录"""
    # 1. 初始化两个文件夹
    folder1 = "cation_1"
    folder2 = "anion_1"
    estimator.folders = [folder1, folder2]  # 确保有多个文件夹

    # 2. 模拟 _multiwfn_process_fchk_to_json 被调用
    with patch.object(estimator, "_multiwfn_process_fchk_to_json") as mock_process:
        # 3. 调用被测方法：不传入 specific_directory
        estimator.multiwfn_process_fchk_to_json()

        # 4. 验证：被调用两次，分别对应两个文件夹
        assert mock_process.call_count == 2
        mock_process.assert_any_call(folder1)
        mock_process.assert_any_call(folder2)

        # 5. 验证：为每个文件夹创建了 gaussian_result_dir 下的目录
        assert (estimator.gaussian_result_dir / folder1).exists()
        assert (estimator.gaussian_result_dir / folder2).exists()


# ==================== 测试 _multiwfn_process_fchk_to_json 私有函数 =============
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


def test_multiwfn_process_fchk_to_json_no_fchk_files(estimator: EmpiricalEstimation):
    """测试当文件夹中无 .fchk 文件时，抛出 FileNotFoundError"""
    folder = "empty_folder"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)  # 创建空文件夹

    with pytest.raises(
        FileNotFoundError, match="No availible Gaussian .fchk file to process"
    ):
        estimator._multiwfn_process_fchk_to_json(folder)


def test_multiwfn_process_fchk_to_json_copy_json_file(
    estimator: EmpiricalEstimation, caplog
):
    """测试当源 .json 文件存在，但目标文件不存在时，执行 shutil.copy"""
    folder = "cation_1"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)

    # 关键：必须创建一个 .fchk 文件，否则会提前抛出 FileNotFoundError
    fchk_file = folder_path / "test.fchk"
    fchk_file.touch()

    # 创建源 .json 文件（由 _single_multiwfn_fchk_to_json 模拟生成）
    json_file = folder_path / "test.json"
    json_file.write_text('{"refcode": "test", "density": "1.5"}', encoding="utf-8")

    # 确保目标路径不存在
    optimized_json_path = estimator.gaussian_result_dir / folder / "test.json"
    assert not optimized_json_path.exists()

    # 模拟 _single_multiwfn_fchk_to_json 成功执行，返回 True
    with patch.object(estimator, "_single_multiwfn_fchk_to_json", return_value=True):
        with patch("shutil.copy") as mock_copy:
            estimator._multiwfn_process_fchk_to_json(folder)

            # 验证：shutil.copy 被调用一次，参数正确
            mock_copy.assert_called_once_with(str(json_file), str(optimized_json_path))

            # 验证：日志未打印“already exists”（因为目标文件不存在）
            assert f"{optimized_json_path} already exists" not in caplog.text


def test_multiwfn_process_fchk_to_json_skip_copy_if_target_exists(
    estimator: EmpiricalEstimation, caplog
):
    """测试当目标 .json 文件已存在时，跳过复制并记录日志"""
    caplog.set_level("INFO")
    folder = "cation_1"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)

    # 1. 创建 .fchk 文件 —— 必须存在，否则方法提前报错
    fchk_file = folder_path / "test.fchk"
    fchk_file.touch()

    # 2. 模拟 _single_multiwfn_fchk_to_json 成功执行，返回 True，从而生成源 .json 文件
    with patch.object(estimator, "_single_multiwfn_fchk_to_json", return_value=True):
        # 3. 创建源 .json 文件（由模拟函数生成）
        json_file = folder_path / "test.json"
        json_file.write_text('{"refcode": "test", "density": "1.5"}', encoding="utf-8")

        # 4. 创建目标 .json 文件（已存在，触发跳过复制）
        optimized_json_path = estimator.gaussian_result_dir / folder / "test.json"
        optimized_json_path.parent.mkdir(parents=True, exist_ok=True)
        optimized_json_path.write_text(
            '{"refcode": "test", "density": "1.5"}', encoding="utf-8"
        )

        # 5. 执行被测方法
        estimator._multiwfn_process_fchk_to_json(folder)

        # 6. 验证：日志中包含跳过复制的提示
        expected_log = f"{optimized_json_path} already exists, skipping copy to Optimized directory."
        assert expected_log in caplog.text, f"Expected log not found: {expected_log}"


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


# ==================== 测试 _single_multiwfn_fchk_to_json 私有函数 ====================
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
        optimized_path = estimator.gaussian_dir / "Optimized" / "cation_1" / "test.json"
        mock_copyfile.assert_called_once_with(str(json_path), str(optimized_path))
        # 11. 检查 unlink 调用
        mock_unlink.assert_any_call(missing_ok=True)
        assert mock_unlink.call_count == 2


def test_single_multiwfn_fchk_to_json_subprocess_failure(estimator: EmpiricalEstimation):
    fchk_path = estimator.gaussian_dir / "test.fchk"
    fchk_path.parent.mkdir(parents=True, exist_ok=True)
    fchk_path.touch()
    with patch(
        "subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmd")
    ):
        result = estimator._single_multiwfn_fchk_to_json(fchk_path)
        assert result is False


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


def test_single_multiwfn_fchk_to_json_output_txt_read_error(
    estimator: EmpiricalEstimation, caplog
):
    """测试当 output.txt 文件存在但读取失败时，记录错误并重新抛出异常"""
    folder = "cation_1"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)

    # 1. 创建 .fchk 文件（触发处理流程）
    fchk_file = folder_path / "test.fchk"
    fchk_file.touch()

    # 2. 模拟 subprocess.run 成功执行，生成 output.txt 文件
    output_path = folder_path / "output.txt"
    output_path.write_text("dummy output content", encoding="utf-8")

    # 3. 设置 caplog 级别为 ERROR，确保捕获错误日志
    caplog.set_level("INFO")

    # 4. 模拟 read_text() 读取 output.txt 时抛出 PermissionError
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        with patch(
            "pathlib.Path.read_text", side_effect=PermissionError("Permission denied")
        ):
            with pytest.raises(PermissionError, match="Permission denied"):
                estimator._single_multiwfn_fchk_to_json(fchk_file)

    # 5. 验证日志中包含错误信息
    assert "Error reading output.txt: Permission denied" in caplog.text


def test_single_multiwfn_fchk_to_json_detects_cation(
    estimator: EmpiricalEstimation, caplog
):
    """测试当电势数据符合阳离子特征时，正确识别 ion_type 为 'cation'"""
    folder = "cation_1"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    result_folder_path = estimator.gaussian_result_dir / folder
    result_folder_path.mkdir(parents=True, exist_ok=True)
    # 1. 创建 .fchk 文件
    fchk_file = folder_path / "test.fchk"
    fchk_file.touch()

    # 2. 创建 output.txt，包含 ALL 必需字段（包括 Volume 和 Density）
    output_content = """\
Volume:   504.45976 Bohr^3  (  74.75322 Angstrom^3)
Estimated density according to mass and volume (M/V):    1.5557 g/cm^3
Overall surface area:         320.06186 Bohr^2  (  89.62645 Angstrom^2)
Overall average value:   -0.19677551 a.u. (   -123.47860 kcal/mol)
Positive surface area:        320.06186 Bohr^2  (  89.62645 Angstrom^2)
Positive average value:  -0.19677551 a.u. (   -123.47860 kcal/mol)
Negative surface area:          0.00000 Bohr^2  (   0.00000 Angstrom^2)
Negative average value:          NaN a.u. (          NaN kcal/mol)
"""
    output_path = folder_path / "output.txt"
    output_path.write_text(output_content, encoding="utf-8")

    # 3. 模拟 subprocess.run 成功执行
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        # 4. 模拟 Path.read_text() 返回构造的内容
        with patch("pathlib.Path.read_text", return_value=output_content):
            result = estimator._single_multiwfn_fchk_to_json(fchk_file)

            # 5. 验证返回 True（处理成功）
            assert result is True

            # 6. 验证生成的 JSON 文件中 ion_type 为 "cation"
            json_file = folder_path / "test.json"
            assert json_file.exists()
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                assert data["ion_type"] == "cation"


# ==================== 测试 gaussian_log_to_optimized_gjf 函数 ====================
def test_gaussian_log_to_optimized_gjf_specific_directory(
    estimator: EmpiricalEstimation, caplog
):
    """测试当传入 specific_directory 时，仅处理指定文件夹"""
    # 1. 创建测试文件夹和 .log 文件
    folder = "cation_1"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    (folder_path / "test.log").touch()

    # 2. 模拟 _gaussian_log_to_optimized_gjf 被调用
    with patch.object(estimator, "_gaussian_log_to_optimized_gjf") as mock_process:
        # 3. 调用被测方法：传入 specific_directory
        estimator.gaussian_log_to_optimized_gjf(specific_directory=folder)

        # 4. 验证：仅调用一次，且参数正确
        mock_process.assert_called_once_with(folder)
        # 验证未创建目标目录（该操作在 _gaussian_log_to_optimized_gjf 内部处理）
        assert not (
            estimator.gaussian_dir / "Optimized" / folder
        ).exists()  # 由内部方法创建


def test_gaussian_log_to_optimized_gjf_all_folders(
    estimator: EmpiricalEstimation, caplog
):
    """测试当 specific_directory 为 None 时，遍历所有初始化的文件夹并创建结果目录"""
    # 1. 初始化两个文件夹
    folder1 = "cation_1"
    folder2 = "anion_1"
    estimator.folders = [folder1, folder2]

    # 2. 模拟 _gaussian_log_to_optimized_gjf 被调用
    with patch.object(estimator, "_gaussian_log_to_optimized_gjf") as mock_process:
        # 3. 调用被测方法：不传入 specific_directory
        estimator.gaussian_log_to_optimized_gjf()

        # 4. 验证：被调用两次，分别对应两个文件夹
        assert mock_process.call_count == 2
        mock_process.assert_any_call(folder1)
        mock_process.assert_any_call(folder2)

        # 5. 验证：为每个文件夹创建了 gaussian_result_dir 下的目录
        assert (estimator.gaussian_result_dir / folder1).exists()
        assert (estimator.gaussian_result_dir / folder2).exists()

        # 6. 验证日志中没有错误
        assert "Error" not in caplog.text


# ==================== 测试 _gaussian_log_to_optimized_gjf 私有函数 ====================
def test_gaussian_log_to_optimized_gjf_success(estimator: EmpiricalEstimation, caplog):
    """测试所有 .log 文件成功转换后，输出完成日志"""
    caplog.set_level("INFO")
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
        # 8. 验证：最终完成日志被记录
        expected_log = f"\nThe .log to .gjf conversion by Multiwfn for {folder} folder has completed, and the optimized .gjf structures have been stored in the optimized directory.\n"
        assert expected_log in caplog.text


def test_gaussian_log_to_optimized_gjf_no_log_files(estimator: EmpiricalEstimation):
    """测试当文件夹中无 .log 文件时，抛出 FileNotFoundError"""
    folder = "empty_folder"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)

    with pytest.raises(
        FileNotFoundError,
        match=f"No availible Gaussian .log file to process in {folder}",
    ):
        estimator._gaussian_log_to_optimized_gjf(folder)


def test_gaussian_log_to_optimized_gjf_skip_if_target_exists(
    estimator: EmpiricalEstimation, caplog
):
    """测试当 optimized .gjf 文件已存在时，跳过处理并记录日志"""
    caplog.set_level("INFO")
    folder = "cation_1"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)

    # 创建 .log 文件
    log_file = folder_path / "test.log"
    log_file.touch()

    # 创建目标 .gjf 文件（已存在）
    optimized_gjf_path = estimator.gaussian_result_dir / folder / "test.gjf"
    optimized_gjf_path.parent.mkdir(parents=True, exist_ok=True)
    optimized_gjf_path.write_text("existing gjf content", encoding="utf-8")

    # 模拟 _single_multiwfn_log_to_gjf 不被调用
    with patch.object(estimator, "_single_multiwfn_log_to_gjf") as mock_func:
        estimator._gaussian_log_to_optimized_gjf(folder)

        # 验证：未调用转换函数
        mock_func.assert_not_called()

        # 验证：日志记录跳过信息
        assert (
            f"{optimized_gjf_path} already exists, skipping multiwfn log_to_gjf processing."
            in caplog.text
        )


def test_gaussian_log_to_optimized_gjf_conversion_failure(
    estimator: EmpiricalEstimation, caplog
):
    """测试当 Multiwfn 转换失败时，记录 bad_files 并继续处理其他文件"""
    caplog.set_level("INFO")
    folder = "anion_1"
    folder_path = estimator.gaussian_dir / folder
    folder_path.mkdir(parents=True, exist_ok=True)

    # 创建两个 .log 文件
    log1 = folder_path / "test1.log"
    log2 = folder_path / "test2.log"
    log1.touch()
    log2.touch()

    # 模拟第一个转换失败，第二个成功
    with patch.object(
        estimator, "_single_multiwfn_log_to_gjf", side_effect=[False, True]
    ) as mock_func:
        estimator._gaussian_log_to_optimized_gjf(folder)

        # 验证：调用两次，参数正确
        assert mock_func.call_count == 2
        mock_func.assert_any_call(folder, log1)
        mock_func.assert_any_call(folder, log2)

        # 验证：bad_files 中包含失败的文件名
    assert "test1" in caplog.text  # 失败的文件名被记录
    assert "Failed to convert the following .log files: ['test1']" in caplog.text
    assert caplog.records[-2].levelname == "ERROR"  # 确保是 ERROR 级别


# ==================== 测试 _single_multiwfn_log_to_gjf 私有函数 ====================
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


def test_single_multiwfn_log_to_gjf_missing_output_file(
    estimator: EmpiricalEstimation, caplog
):
    """测试当 Multiwfn 命令成功但未生成 .gjf 文件时，函数返回 False 并记录错误"""
    # 1. 确保目录存在
    estimator.gaussian_dir.mkdir(parents=True, exist_ok=True)
    # 2. 创建 .log 文件
    log_path = estimator.gaussian_dir / "test.log"
    log_path.touch()
    # 3. 模拟 _multiwfn_cmd_build 成功执行，返回 True
    with patch.object(estimator, "_multiwfn_cmd_build", return_value=True):
        # 4. 执行方法（不创建 .gjf 文件）
        result = estimator._single_multiwfn_log_to_gjf("test", log_path)
        # 5. 验证：返回 False（因为文件不存在）
        assert result is False, "Expected False when .gjf file is not generated"
        # 6. 验证：日志中记录了错误
        gjf_path = estimator.gaussian_result_dir / "test" / "test.gjf"
        assert f"Error converting {log_path} to {gjf_path}" in caplog.text
        assert caplog.records[-1].levelname == "ERROR"


# ==================== 测试 _read_gjf_elements 私有函数 ====================
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


def test_read_gjf_elements_invalid_format(
    estimator: EmpiricalEstimation, tmp_path: Path, caplog
):
    """测试当行格式不正确时，记录警告而不是抛出异常"""
    gjf_content = """# B3LYP/6-31G*
0 1
C  0.0 0.0 0.0
N  1.0 0.0
O  0.0 1.0 0.0 0.0
H  0.5 0.5 0.5
"""
    caplog.set_level("WARNING")
    gjf_path = tmp_path / "test_invalid_format.gjf"
    gjf_path.write_text(gjf_content, encoding="utf-8")

    result = estimator._read_gjf_elements(gjf_path)

    assert result == {"C": 1, "H": 1}  # 只有有效的原子行
    assert "Unexpected line format in gjf file: N  1.0 0.0" in caplog.text
    assert "Unexpected line format in gjf file: O  0.0 1.0 0.0 0.0" in caplog.text


# ==================== 测试 _generate_combinations 私有函数 ====================
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


def test_generate_combinations_missing_suffix_files(estimator: EmpiricalEstimation):
    """测试当某个文件夹中无指定后缀文件时，抛出 FileNotFoundError"""
    # 创建目录结构
    estimator.gaussian_result_dir.mkdir(parents=True, exist_ok=True)
    (estimator.gaussian_result_dir / "cation_1").mkdir(exist_ok=True)
    (estimator.gaussian_result_dir / "anion_1").mkdir(exist_ok=True)

    # 仅在 cation_1 中创建 .gjf 文件，anion_1 中不创建
    (estimator.gaussian_result_dir / "cation_1" / "c1.gjf").touch()

    # 调用 _generate_combinations，期望在 anion_1 中因无 .gjf 文件而抛出异常
    with pytest.raises(
        FileNotFoundError, match=r"No available \.gjf files in anion_1 folder"
    ):
        estimator._generate_combinations(".gjf")


# ==================== 测试 nitrogen_content_estimate 函数 ====================
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


def test_nitrogen_content_estimate_unknown_element(
    estimator: EmpiricalEstimation, caplog
):
    """测试当 .gjf 文件包含未定义元素时，抛出 ValueError"""
    # 1. 创建一个包含未知元素（如 "S"）的 .gjf 文件
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True, exist_ok=True)
    (opt_dir / "anion_1").mkdir(exist_ok=True)

    # 创建一个包含硫(S)的 .gjf 文件
    gjf_content = """# B3LYP/6-31G*
0 1
C 0 0 0
N 1 0 0
S 0 1 0  # 未知元素
"""
    gjf_path = opt_dir / "cation_1" / "c1.gjf"
    gjf_path.write_text(gjf_content, encoding="utf-8")

    # 2. 创建另一个正常 .gjf 文件
    gjf_path2 = opt_dir / "anion_1" / "a1.gjf"
    gjf_path2.write_text(
        """# B3LYP/6-31G*
0 1
H 0 0 0
""",
        encoding="utf-8",
    )

    # 3. 模拟 _generate_combinations 返回包含该文件的组合
    with patch.object(estimator, "_generate_combinations") as mock_gen:
        mock_gen.return_value = [
            {gjf_path: 1, gjf_path2: 1}  # 包含含 "S" 的文件
        ]

        # 4. 模拟 _read_gjf_elements 返回包含 "S" 的原子计数
        with patch.object(
            estimator, "_read_gjf_elements", return_value={"C": 1, "N": 1, "S": 1}
        ):
            with pytest.raises(ValueError, match="Contains element 'S' not included"):
                estimator.nitrogen_content_estimate()


# ==================== 测试 carbon_nitrogen_ratio_estimate 函数 ====================
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


def test_carbon_nitrogen_ratio_estimate_unknown_element(
    estimator: EmpiricalEstimation, caplog
):
    """测试当 .gjf 文件包含未定义元素时，抛出 ValueError"""
    # 1. 创建一个包含未知元素（如 "S"）的 .gjf 文件
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True, exist_ok=True)
    (opt_dir / "anion_1").mkdir(exist_ok=True)

    # 创建一个包含硫(S)的 .gjf 文件
    gjf_content = """# B3LYP/6-31G*
0 1
C 0 0 0
N 1 0 0
S 0 1 0  # 未知元素
"""
    gjf_path = opt_dir / "cation_1" / "c1.gjf"
    gjf_path.write_text(gjf_content, encoding="utf-8")

    # 2. 创建另一个正常 .gjf 文件
    gjf_path2 = opt_dir / "anion_1" / "a1.gjf"
    gjf_path2.write_text(
        """# B3LYP/6-31G*
0 1
H 0 0 0
""",
        encoding="utf-8",
    )

    # 3. 模拟 _generate_combinations 返回包含该文件的组合
    with patch.object(estimator, "_generate_combinations") as mock_gen:
        mock_gen.return_value = [
            {gjf_path: 1, gjf_path2: 1}  # 包含含 "S" 的文件
        ]

        # 4. 模拟 _read_gjf_elements 返回包含 "S" 的原子计数
        with patch.object(
            estimator, "_read_gjf_elements", return_value={"C": 1, "N": 1, "S": 1}
        ):
            with pytest.raises(ValueError, match="Contains element 'S' not included"):
                estimator.carbon_nitrogen_ratio_estimate()


def test_carbon_nitrogen_ratio_estimate_no_carbon(estimator: EmpiricalEstimation):
    """测试当组合中不含碳原子时，C:N 比率被设为 100.0"""
    # 验证生成的 CSV 文件
    csv_path = estimator.gaussian_dir / "specific_NC_ratio.csv"
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True, exist_ok=True)
    (opt_dir / "anion_1").mkdir(exist_ok=True)

    cation_gjf = opt_dir / "cation_1" / "c1.gjf"
    anion_gjf = opt_dir / "anion_1" / "a1.gjf"
    # 创建两个只含氮的分子
    cation_gjf.write_text("""# B3LYP/6-31G*
0 1
N 0 0 0
""")
    anion_gjf.write_text("""# B3LYP/6-31G*
0 1
H 0 0 0
H 1 0 0
""")

    # 重新执行
    estimator.carbon_nitrogen_ratio_estimate()

    with open(csv_path, "r") as f:
        reader = csv.reader(f)
        rows = list(reader)
        assert rows[1][2] == "100.0"  # 氮原子存在，碳原子为0 → 比率为100.0


# ==================== 测试 empirical_estimate 函数 ====================
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


def test_empirical_estimate_invalid_json_file(estimator: EmpiricalEstimation, caplog):
    """测试当 .json 文件为无效 JSON 时，跳过该文件并继续处理其他文件"""
    # 1. 创建目录结构
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True, exist_ok=True)
    (opt_dir / "anion_1").mkdir(exist_ok=True)

    # 2. 创建一个有效的 .json 文件
    valid_json = opt_dir / "cation_1" / "c1.json"
    valid_json.write_text(
        '{"refcode": "c1", "molecular_mass": 100, "volume": 500, "positive_surface_area": "100", "negative_surface_area": "0.0", "positive_average_value": "0.0", "negative_average_value": "NaN", "ion_type": "cation"}',
        encoding="utf-8",
    )

    # 3. 创建一个无效的 .json 文件（非法 JSON）
    invalid_json = opt_dir / "anion_1" / "a1.json"
    invalid_json.write_text("invalid json content", encoding="utf-8")

    # 4. 模拟 _generate_combinations 返回包含两个文件的组合
    with patch.object(estimator, "_generate_combinations") as mock_gen:
        mock_gen.return_value = [{valid_json: 1, invalid_json: 1}]

        # 5. 执行方法
        estimator.empirical_estimate()

        # 6. 验证：无效文件被跳过，不报错，且有效文件被处理
        # 由于只处理了一个有效文件，应生成一个组合
        csv_path = estimator.gaussian_dir / "sorted_density.csv"
        assert csv_path.exists()
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert len(rows) == 2  # 表头 + 1 行数据（仅 c1 被处理）
        assert rows[1][0] == "cation_1/c1"  # 仅有效文件被写入


def test_copy_combo_file_target_exists_skip_copy(
    estimator: EmpiricalEstimation, tmp_path: Path, caplog
):
    """测试当目标文件已存在时，跳过复制并记录日志"""
    caplog.set_level("INFO")
    # 1. 创建源文件
    source_folder = "cation_1"
    source_file_base = "c1"
    source_path = (
        estimator.gaussian_result_dir / source_folder / f"{source_file_base}.gjf"
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("source content", encoding="utf-8")

    # 2. 创建目标文件夹和目标文件（已存在）
    combo_path = tmp_path / "combo_1"
    combo_path.mkdir()
    target_path = combo_path / f"{source_file_base}.gjf"
    target_path.write_text("existing content", encoding="utf-8")

    # 3. 调用方法
    estimator._copy_combo_file(
        combo_path, f"{source_folder}/{source_file_base}", ".gjf"
    )

    # 4. 验证：未执行复制（文件内容未被覆盖）
    assert target_path.read_text() == "existing content"

    # 5. 验证：日志中记录了跳过信息
    expected_log = f"{source_file_base}.gjf of combo_1 already exists in {combo_path}. Skipping copy."
    assert expected_log in caplog.text
    assert caplog.records[-1].levelname == "INFO"


# ==================== 测试 _copy_combo_file 私有函数 ====================
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


# ==================== 测试 make_combo_dir 函数 ====================
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


def test_make_combo_dir_csv_not_exists(estimator: EmpiricalEstimation, tmp_path: Path):
    """测试当指定的 CSV 文件不存在时，抛出 FileNotFoundError"""
    estimator.sort_by = "density"
    estimator.density_csv = tmp_path / "nonexistent_density.csv"  # 不存在的文件

    with pytest.raises(
        FileNotFoundError,
        match=r"CSV file .* does not exist in the Gaussian optimized directory\.",
    ):
        estimator.make_combo_dir(tmp_path, num_combos=1, ion_numbers=[1, 1])


def test_make_combo_dir_csv_missing_value(estimator, tmp_path):
    """测试CSV文件中缺少值时抛出ValueError"""
    estimator.sort_by = "density"
    estimator.density_csv = tmp_path / "density.csv"
    estimator.base_dir = tmp_path

    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True)
    (opt_dir / "cation_1" / "c1.gjf").touch()
    (opt_dir / "cation_1" / "c1.json").touch()
    # 创建有缺失值的CSV
    with open(estimator.density_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Component1", "Component2"])
        writer.writerow(["cation_1/c1", ""])  # 缺失Component2的值

    with pytest.raises(ValueError, match=r"Missing value in CSV file"):
        estimator.make_combo_dir(tmp_path, num_combos=1, ion_numbers=[1])


def test_make_combo_dir_invalid_folder_basename_format(
    estimator: EmpiricalEstimation, tmp_path, caplog
):
    """测试当 folder_basename 格式无效（如无 '/'）时，记录错误并返回"""
    target_dir = tmp_path / "test_combos"
    estimator.base_dir = tmp_path
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True)
    (opt_dir / "cation_1" / "c1.gjf").touch()
    (opt_dir / "cation_1" / "c1.json").touch()
    # 创建 CSV 文件在正确位置：gaussian_dir
    csv_path = estimator.gaussian_dir / "sorted_density.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("""Component 1,Component 2,Pred_Density
cation_1/c1,invalid_2,1.2345
""")

    # 调用方法
    with pytest.raises(ValueError, match="Invalid folder_basename format: invalid_2."):
        estimator.make_combo_dir(target_dir, num_combos=1, ion_numbers=[1, 1])


def test_make_combo_dir_config_not_found(estimator, tmp_path, caplog):
    """测试当config.yaml在两个位置都不存在时抛出FileNotFoundError"""
    estimator.sort_by = "density"
    estimator.density_csv = tmp_path / "density.csv"
    estimator.base_dir = tmp_path
    estimator.gaussian_dir = tmp_path / "gaussian"
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "folder1").mkdir(parents=True)
    (opt_dir / "folder1/file1.gjf").touch()
    (opt_dir / "folder1/file1.json").touch()

    # 创建有效的CSV和源文件
    with open(estimator.density_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Component1"])
        writer.writerow(["folder1/file1"])

    # 不创建config.yaml文件
    with pytest.raises(FileNotFoundError, match=r"No available config.yaml file"):
        estimator.make_combo_dir(tmp_path, num_combos=1, ion_numbers=[1])

    # 验证错误日志
    assert "No available config.yaml file" in caplog.text


def test_make_combo_dir_yaml_parse_error(estimator, tmp_path, caplog):
    """测试YAML解析错误时的处理"""
    estimator.sort_by = "density"
    estimator.density_csv = tmp_path / "density.csv"
    estimator.base_dir = tmp_path
    estimator.gaussian_dir = tmp_path / "gaussian"
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "folder1").mkdir(parents=True)
    (opt_dir / "folder1/file1.gjf").touch()
    (opt_dir / "folder1/file1.json").touch()

    # 创建CSV和源文件
    with open(estimator.density_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Component1"])
        writer.writerow(["folder1/file1"])

    # 创建损坏的YAML文件
    config_path = tmp_path / "config.yaml"
    config_path.write_text("invalid: yaml: content: [unclosed bracket")

    # 执行并捕获异常
    with pytest.raises(Exception) as e:
        estimator.make_combo_dir(tmp_path, num_combos=1, ion_numbers=[1])

    caplog.set_level("ERROR")
    # 验证YAML解析错误被记录
    assert f"YAML configuration file parsing failed: {e.value}" in caplog.text


def test_make_combo_dir_write_config_exception(
    estimator: EmpiricalEstimation, tmp_path: Path, caplog
):
    """测试在写入 config.yaml 时发生异常（如权限错误），应记录错误但不崩溃"""
    # 1. 创建有效的 config.yaml
    (estimator.gaussian_dir / "config.yaml").write_text(
        "gen_opt:\n  species: []\n  ion_numbers: []", encoding="utf-8"
    )

    # 2. 创建 CSV 文件
    csv_content = """Component 1,Component 2,Nitrogen_Content
cation_1/c1,anion_1/a1,0.45
"""
    csv_file = tmp_path / "sorted_nitrogen.csv"
    csv_file.write_text(csv_content, encoding="utf-8")
    estimator.nitrogen_csv = csv_file
    estimator.sort_by = "nitrogen"
    opt_dir = estimator.gaussian_result_dir
    (opt_dir / "cation_1").mkdir(parents=True)
    (opt_dir / "cation_1/c1.gjf").touch()
    (opt_dir / "cation_1/c1.json").touch()
    (opt_dir / "anion_1").mkdir(parents=True)
    (opt_dir / "anion_1/a1.gjf").touch()
    (opt_dir / "anion_1/a1.json").touch()

    # 3. 创建目标 combo 文件夹
    combo_folder = tmp_path / "combo_1"
    combo_folder.mkdir()

    # 4. 模拟 yaml.dump 成功，但 write_text 抛出 PermissionError
    with patch("yaml.dump", return_value="valid yaml content"):
        with patch(
            "pathlib.Path.write_text", side_effect=PermissionError("Permission denied")
        ):
            estimator.make_combo_dir(tmp_path, num_combos=1, ion_numbers=[1, 1])

    # 5. 验证：日志中记录了错误
    assert "Unexpected error: Permission denied" in caplog.text
    assert caplog.records[-1].levelname == "ERROR"

    # 6. 验证：config.yaml 未被写入（文件不存在）
    assert not (combo_folder / "config.yaml").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=ion_CSP.empirical_estimate"])
