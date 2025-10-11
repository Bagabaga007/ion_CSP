import os
import csv
import json
import yaml
import pytest
import logging
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from ion_CSP.empirical_estimate import EmpiricalEstimation

# 修改后的夹具，确保正确创建文件和目录结构
@pytest.fixture(scope="module")
def setup_test_environment(tmp_path_factory):
    base_dir = tmp_path_factory.mktemp("test_empirical")

    # 创建必需的Gaussian优化目录
    gaussian_result_dir = base_dir / "1_2_Gaussian_optimized"
    gaussian_result_dir.mkdir()
    
    # 创建Optimized目录结构
    optimized_dir = gaussian_result_dir / "Optimized"
    optimized_dir.mkdir()

    # 创建测试文件夹和样本文件
    folders = ["charge_1", "charge_-1"]
    folder_files = [[1, 2], [3, 4]]
    ratios = [1, 1]

    for folder, files in zip(folders, folder_files):
        # 在优化目录下创建子文件夹
        folder_path = gaussian_result_dir / folder
        folder_path.mkdir()
        # 在Optimized下创建子目录
        optimized_folder_dir = optimized_dir / folder
        optimized_folder_dir.mkdir()

        # 创建样本文件
        for i in files:
            # 原始目录文件
            fchk_file = folder_path / f"REF{i}.fchk"
            log_file = folder_path / f"REF{i}.log"
            gjf_file = folder_path / f"REF{i}.gjf"
            json_file = folder_path / f"REF{i}.json"
            optimized_gjf_file = optimized_folder_dir / f"REF{i}.gjf"

            fchk_file.write_text("Sample FCHK content")
            log_file.write_text("Sample LOG content")
            gjf_file.write_text(
                "%nprocshared=8\n#p B3LYP/6-31G** opt\n\nTitle\n\n0 1\n"
                "C 0.0 0.0 0.0\nN 1.0 0.0 0.0\nO 0.0 1.0 0.0\nH 0.0 0.0 1.0\n\n"
            )
            optimized_gjf_file.write_text(
                "%nprocshared=8\n#p B3LYP/6-31G** opt\n\nTitle\n\n0 1\n"
                "C 0.0 0.0 0.0\nN 1.0 0.0 0.0\nO 0.0 1.0 0.0\nH 0.0 0.0 1.0\n\n"
            )
            json_file.write_text(
                json.dumps(
                    {
                        "refcode": f"REF{i}",
                        "ion_type": "anion" if "-1" in folder else "cation",
                        "molecular_mass": 100 + i,
                        "volume": "100.0",
                        "density": "1.5",
                        "positive_surface_area": "50.0" if "1" in folder else "0.0",
                        "positive_average_value": "10.0" if "1" in folder else "NaN",
                        "negative_surface_area": "0.0" if "1" in folder else "50.0",
                        "negative_average_value": "NaN" if "1" in folder else "-10.0",
                    }
                )
            )

            print(f"Created FCHK file at: {fchk_file}")
            print(f"Created LOG file at: {log_file}")
            print(f"Created GJF file at: {gjf_file}")
            print(f"Created JSON file at: {json_file}")

            # Optimized目录文件
            (optimized_folder_dir / f"REF{i}.gjf").write_text(
                "%nprocshared=8\n#p B3LYP/6-31G** opt\n\nTitle\n\n0 1\n"
                "C 0.0 0.0 0.0\nN 1.0 0.0 0.0\nO 0.0 1.0 0.0\nH 0.0 0.0 1.0\n\n"
            )

    # 创建config.yaml文件
    config = {
        "gen_opt": {
            "species": ["REF1.gjf", "REF2.gjf"],
            "ion_numbers": [1, 1],
        }
    }
    (base_dir / "config.yaml").write_text(yaml.dump(config))
    
    return base_dir, folders, ratios


# 添加在夹具中
def test_directory_structure(setup_test_environment):
    base_dir, folders, ratios = setup_test_environment
    gaussian_result_dir = base_dir / "1_2_Gaussian_optimized"

    # 验证原始目录
    for folder in folders:
        folder_path = gaussian_result_dir / folder
        assert folder_path.exists()
        assert len(list(folder_path.glob("*.fchk"))) == 2
        assert len(list(folder_path.glob("*.log"))) == 2
        assert len(list(folder_path.glob("*.gjf"))) == 2
        assert len(list(folder_path.glob("*.json"))) == 2
    
    # 验证Optimized目录
    optimized_dir = gaussian_result_dir / "Optimized"
    for folder in folders:
        opt_folder = optimized_dir / folder
        assert opt_folder.exists()
        assert len(list(opt_folder.glob("*.gjf"))) == 2


# 测试初始化
def test_initialization(setup_test_environment):
    base_dir, folders, ratios = setup_test_environment
    ee = EmpiricalEstimation(
        work_dir=base_dir, folders=folders, ratios=ratios, sort_by="density"
    )

    assert ee.base_dir == base_dir
    assert ee.folders == folders
    assert ee.ratios == ratios
    assert ee.sort_by == "density"
    assert ee.density_csv == "sorted_density.csv"
    assert ee.nitrogen_csv == "sorted_nitrogen.csv"
    assert ee.NC_ratio_csv == "specific_NC_ratio.csv"

    # 测试无效的sort_by参数
    with pytest.raises(ValueError):
        EmpiricalEstimation(
            work_dir=base_dir, folders=folders, ratios=ratios, sort_by="invalid"
        )

    # 测试文件夹/比例不匹配
    with pytest.raises(ValueError):
        EmpiricalEstimation(
            work_dir=base_dir, folders=folders, ratios=[1], sort_by="density"
        )


# 测试Multiwfn可执行文件检查
def test_multiwfn_check(setup_test_environment):
    base_dir, folders, ratios = setup_test_environment

    # 测试Multiwfn可用时
    with patch("shutil.which", return_value="/usr/bin/Multiwfn"):
        ee = EmpiricalEstimation(
            work_dir=base_dir, folders=folders, ratios=ratios, sort_by="density"
        )
        assert ee.multiwfn_path == "/usr/bin/Multiwfn"

    # 测试Multiwfn不可用时
    with patch("shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError):
            EmpiricalEstimation(
                work_dir=base_dir,
                folders=folders,
                ratios=ratios,
                sort_by="density",
            )


# 测试FCHK转JSON处理
@patch("subprocess.run")
def test_fchk_to_json(mock_run, setup_test_environment, caplog):
    base_dir, folders, ratios = setup_test_environment
    ee = EmpiricalEstimation(
        work_dir=base_dir, folders=folders, ratios=ratios, sort_by="density"
    )
    caplog.set_level(logging.INFO)

    # 创建模拟的Multiwfn输出
    mock_output = """
    Volume:   100.00 Bohr^3  (  100.00 Angstrom^3)
    Estimated density according to mass and volume (M/V):    1.50 g/cm^3
    Overall surface area:         200.00 Bohr^2  (  200.00 Angstrom^2)
    Positive surface area:          50.00 Bohr^2  (  50.00 Angstrom^2)
    Negative surface area:          150.00 Bohr^2  (  150.00 Angstrom^2)
    Overall average value:   -0.10 a.u. (   -100.00 kcal/mol)
    Positive average value:  0.05 a.u. (  50.00 kcal/mol)
    Negative average value:  -0.15 a.u. (   -150.00 kcal/mol)
    """

    # 模拟subprocess.run调用
    mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)

    # 处理FCHK文件
    ee.multiwfn_process_fchk_to_json()

    # 验证JSON文件已创建
    gaussian_result_dir = base_dir / "1_2_Gaussian_optimized"
    for folder in folders:
        folder_path = gaussian_result_dir / folder
        json_files = list(folder_path.glob("*.json"))
        assert len(json_files) == 2  # 每个文件夹应有2个文件

        # 检查一个JSON文件的内容
        with open(json_files[0], "r") as f:
            data = json.load(f)
            assert "refcode" in data
            assert "density" in data
            assert "volume" in data
            assert "positive_surface_area" in data
            assert "negative_surface_area" in data

    # 测试指定目录
    folder_path = gaussian_result_dir / folders[0]
    ee.multiwfn_process_fchk_to_json(specific_directory=folder_path)

    # 测试损坏的FCHK文件
    with patch("subprocess.run", side_effect=Exception("Test error")):
        ee.multiwfn_process_fchk_to_json()
        bad_dir = gaussian_result_dir / "Bad"
        assert bad_dir.exists()


# 测试LOG转GJF转换
@patch("subprocess.run")
def test_log_to_gjf(mock_run, setup_test_environment, caplog):
    base_dir, folders, ratios = setup_test_environment
    ee = EmpiricalEstimation(
        work_dir=base_dir, folders=folders, ratios=ratios, sort_by="density"
    )

    # 模拟subprocess.run调用
    mock_run.return_value = MagicMock(returncode=0)

    # 处理LOG文件
    ee.gaussian_log_to_optimized_gjf()

    # 验证GJF文件已创建在Optimized目录
    optimized_dir = base_dir / "1_2_Gaussian_optimized"
    optimized_opt_dir = optimized_dir / "Optimized"
    assert optimized_opt_dir.exists()

    for folder in folders:
        gjf_files = list((optimized_opt_dir / folder).glob("*.gjf"))
        assert len(gjf_files) == 2  # 每个文件夹应有2个文件

    # 测试指定目录
    ee.gaussian_log_to_optimized_gjf(specific_directory=folders[0])

    # 测试错误处理
    with patch("subprocess.run", side_effect=Exception("Test error")):
        ee.gaussian_log_to_optimized_gjf()
        assert "Error with processing" in caplog.text


# 测试从GJF文件中读取元素
def test_read_gjf_elements(setup_test_environment):
    base_dir, folders, ratios = setup_test_environment
    ee = EmpiricalEstimation(
        work_dir=base_dir, folders=folders, ratios=ratios, sort_by="density"
    )
    # 测试有效的GJF文件
    gaussian_result_dir = base_dir / "1_2_Gaussian_optimized"
    gjf_file = gaussian_result_dir / folders[0] / "REF1.gjf"
    elements = ee._read_gjf_elements(gjf_file)
    assert elements == {"C": 1, "N": 1, "O": 1, "H": 1}

    # 测试无效的GJF文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".gjf", delete=False) as tmp:
        tmp.write("Invalid content\n")
        tmp_path = Path(tmp.name)

    elements = ee._read_gjf_elements(tmp_path)
    assert elements == {}
    os.remove(tmp_path)


# 测试生成组合
def test_generate_combinations(setup_test_environment):
    base_dir, folders, ratios = setup_test_environment
    ee = EmpiricalEstimation(
        work_dir=base_dir, folders=folders, ratios=ratios, sort_by="density"
    )

    # 测试.json后缀
    combinations = ee._generate_combinations(suffix=".json")
    assert len(combinations) == 4  # 2个文件夹 * 2个文件 = 4个组合

    # 检查组合结构
    combo = combinations[0]
    assert isinstance(combo, dict)
    assert len(combo) == 2  # 2个文件夹
    for path, count in combo.items():
        assert Path(path).suffix == ".json"
        assert count == 1

    # 测试.gjf后缀
    combinations = ee._generate_combinations(suffix=".gjf")
    assert len(combinations) == 4

    # 测试文件夹中没有文件的情况
    with pytest.raises(FileNotFoundError):
        ee._generate_combinations(suffix=".invalid")


# 测试氮含量估算
def test_nitrogen_content_estimate(setup_test_environment):
    base_dir, folders, ratios = setup_test_environment
    ee = EmpiricalEstimation(
        work_dir=base_dir, folders=folders, ratios=ratios, sort_by="nitrogen"
    )

    ee.nitrogen_content_estimate()

    # 验证CSV文件已创建
    gaussian_result_dir = base_dir / "1_2_Gaussian_optimized"
    csv_path = gaussian_result_dir / ee.nitrogen_csv
    assert csv_path.exists()

    # 验证CSV内容
    with open(csv_path, "r") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header == ["Component 1", "Component 2", "Nitrogen_Content"]

        rows = list(reader)
        assert len(rows) == 4
        assert all(float(row[2]) > 0 for row in rows)  # 氮含量应为正数

        # 验证排序
        contents = [float(row[2]) for row in rows]
        assert contents == sorted(contents, reverse=True)


# 测试碳氮比估算
def test_carbon_nitrogen_ratio_estimate(setup_test_environment):
    base_dir, folders, ratios = setup_test_environment
    ee = EmpiricalEstimation(
        work_dir=base_dir, folders=folders, ratios=ratios, sort_by="NC_ratio"
    )

    ee.carbon_nitrogen_ratio_estimate()

    # 验证CSV文件已创建
    optimized_dir = base_dir / "1_2_Gaussian_optimized"
    csv_path = optimized_dir / ee.NC_ratio_csv
    assert csv_path.exists()

    # 验证CSV内容
    with open(csv_path, "r") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header == ["Component 1", "Component 2", "N_C_Ratio", "O_Atoms"]

        rows = list(reader)
        assert len(rows) == 4

        # 验证按N:C比率和氧含量排序
        ratios = [float(row[2]) for row in rows]
        assert ratios == sorted(ratios, reverse=True)


# 测试经验密度估算
def test_empirical_estimate(setup_test_environment):
    base_dir, folders, ratios = setup_test_environment
    ee = EmpiricalEstimation(
        work_dir=base_dir, folders=folders, ratios=ratios, sort_by="density"
    )

    ee.empirical_estimate()

    # 验证CSV文件已创建
    optimized_dir = base_dir / "1_2_Gaussian_optimized"
    csv_path = optimized_dir / ee.density_csv
    assert csv_path.exists()

    # 验证CSV内容
    with open(csv_path, "r") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header == ["Component 1", "Component 2", "Pred_Density"]

        rows = list(reader)
        assert len(rows) == 4
        assert all(float(row[2]) > 0 for row in rows)  # 密度应为正数

        # 验证排序
        densities = [float(row[2]) for row in rows]
        assert densities == sorted(densities, reverse=True)


# 测试创建组合目录
def test_make_combo_dir(setup_test_environment):
    base_dir, folders, ratios = setup_test_environment
    ee = EmpiricalEstimation(
        work_dir=base_dir, folders=folders, ratios=ratios, sort_by="density"
    )

    # 先运行密度估算以创建CSV
    ee.empirical_estimate()

    # 创建组合目录
    target_dir = base_dir / "combos"
    ee.make_combo_dir(target_dir=target_dir, num_combos=2, ion_numbers=[1, 1])

    # 验证目录已创建
    assert (target_dir / "combo_1").exists()
    assert (target_dir / "combo_2").exists()

    # 验证文件已复制
    combo1_dir = target_dir / "combo_1"
    assert len(list(combo1_dir.glob("*.gjf"))) == 2
    assert len(list(combo1_dir.glob("*.json"))) == 2

    # 验证config.yaml已创建
    config_path = combo1_dir / "config.yaml"
    assert config_path.exists()

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
        assert "gen_opt" in config
        assert "species" in config["gen_opt"]
        assert "ion_numbers" in config["gen_opt"]

    # 测试使用氮含量排序
    ee.sort_by = "nitrogen"
    ee.nitrogen_content_estimate()
    ee.make_combo_dir(
        target_dir=target_dir / "nitrogen", num_combos=1, ion_numbers=[1, 1]
    )
    assert (target_dir / "nitrogen" / "combo_1").exists()

    # 测试使用NC比率排序
    ee.sort_by = "NC_ratio"
    ee.carbon_nitrogen_ratio_estimate()
    ee.make_combo_dir(
        target_dir=target_dir / "nc_ratio", num_combos=1, ion_numbers=[1, 1]
    )
    assert (target_dir / "nc_ratio" / "combo_1").exists()

    # 测试找不到config.yaml的情况
    (base_dir / "config.yaml").unlink()
    with pytest.raises(FileNotFoundError):
        ee.make_combo_dir(target_dir=target_dir, num_combos=1, ion_numbers=[1, 1])