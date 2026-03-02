import pytest
import logging
from pathlib import Path
from unittest.mock import patch

from ion_CSP.vasp_processing import VaspProcessing  # 替换为你的模块名


@pytest.fixture
def vasp_processor(tmp_path: Path):
    """
    每个测试获得一个全新的、干净的 VaspProcessing 实例。
    自动创建所有必要目录，确保测试隔离。
    """
    base_dir = tmp_path / "test_work_dir"
    base_dir.mkdir(parents=True, exist_ok=True)

    # 创建模拟的 param 目录
    param_dir = base_dir / "param"
    param_dir.mkdir()
    for f in ["INCAR_1", "INCAR_2", "POTCAR_H", "POTCAR_C", "POTCAR_N", "POTCAR_O", "sub_ori.sh", "INCAR_3", "sub_supple.sh"]:
        (param_dir / f).write_text("dummy content", encoding="utf-8")

    # 创建 config.yaml
    config_path = base_dir / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("""
gen_opt:
  species: ["N2O.json", "H2O.json"]
  ion_numbers: [2, 1]
""", encoding="utf-8")

    # 创建 species JSON 文件
    species_dir = base_dir
    (species_dir / "N2O.json").write_text('{"volume": 50.0}', encoding="utf-8")
    (species_dir / "H2O.json").write_text('{"volume": 30.0}', encoding="utf-8")

    # 创建 VaspProcessing 实例
    vp = VaspProcessing(base_dir)
    vp.param_dir = param_dir

    # 确保目录存在（由 __init__ 创建）
    assert vp.for_vasp_opt_dir.exists()
    assert vp.vasp_optimized_dir.exists()
    assert vp.param_dir.exists()

    # 确保初始为空
    assert len(list(vp.vasp_optimized_dir.rglob("*"))) == 0

    yield vp


# ==================== 测试 dpdisp_vasp_optimization_tasks ====================
@patch("dpdispatcher.Submission.run_submission")
@patch("dpdispatcher.Submission.__init__", return_value=None)
@patch("dpdispatcher.Task.__init__", return_value=None)
def test_dpdisp_vasp_optimization_tasks_success(
    mock_task, mock_sub, mock_run, vasp_processor: VaspProcessing, tmp_path: Path, caplog
):
    caplog.set_level(logging.INFO)

    # 创建测试 CONTCAR_ 文件
    for i in range(3):
        contcar = vasp_processor.for_vasp_opt_dir / f"CONTCAR_{i:03d}"
        contcar.write_text("dummy", encoding="utf-8")
        outcar = vasp_processor.for_vasp_opt_dir / f"OUTCAR_{i:03d}"
        outcar.write_text("TOTEN = -10.123456\n", encoding="utf-8")

    # 创建 machine 和 resources
    machine_path = tmp_path / "machine.yaml"
    resources_path = tmp_path / "resources.yaml"

    machine_path.write_text(
        """
context_type: LocalContext
local_root: ./ 
remote_root: /your/remote/workplace
batch_type: Shell
""",
        encoding="utf-8",
    )

    resources_path.write_text(
        """
number_node: 2
cpu_per_node: 8
gpu_per_node: 0
group_size: 1
""",
        encoding="utf-8",
    )

    # 执行
    vasp_processor.dpdisp_vasp_optimization_tasks(
        machine_path=str(machine_path),
        resources_path=str(resources_path),
        nodes=2,
    )

    # 验证日志
    assert "Batch VASP optimization completed!!!" in caplog.text

    # 验证 vasp_optimized_dir 被创建
    assert vasp_processor.vasp_optimized_dir.exists()
    assert len(list(vasp_processor.vasp_optimized_dir.rglob("CONTCAR_*"))) == 3
    assert len(list(vasp_processor.vasp_optimized_dir.rglob("OUTCAR_*"))) == 3

    # 验证 dpdispatcher 被调用
    mock_sub.assert_called()
    mock_task.assert_called()
    mock_run.assert_called_once()


@patch("dpdispatcher.Submission.run_submission")
@patch("dpdispatcher.Submission.__init__", return_value=None)
@patch("dpdispatcher.Task.__init__", return_value=None)
def test_dpdisp_vasp_optimization_tasks_no_files(
    mock_task, mock_sub, mock_run, vasp_processor: VaspProcessing, tmp_path: Path, caplog
):
    caplog.set_level(logging.INFO)

    machine_path = tmp_path / "machine.yaml"
    resources_path = tmp_path / "resources.yaml"

    machine_path.write_text("""
context_type: LocalContext
local_root: ./ 
remote_root: /your/remote/workplace
batch_type: Shell
""", encoding="utf-8")

    resources_path.write_text("""
number_node: 2
cpu_per_node: 8
gpu_per_node: 0
group_size: 1
""", encoding="utf-8")

    # 不创建任何 CONTCAR 文件

    with pytest.raises(FileNotFoundError, match="No CONTCAR_ files found in"):
        vasp_processor.dpdisp_vasp_optimization_tasks(
            machine_path=str(machine_path),
            resources_path=str(resources_path),
            nodes=2,
        )

    mock_sub.assert_not_called()
    mock_task.assert_not_called()
    mock_run.assert_not_called()


# ==================== 测试 dpdisp_vasp_relaxation_tasks ====================
@patch("dpdispatcher.Submission.run_submission")
@patch("dpdispatcher.Submission.__init__", return_value=None)
@patch("dpdispatcher.Task.__init__", return_value=None)
def test_dpdisp_vasp_relaxation_tasks_success(
    mock_task, mock_sub, mock_run, vasp_processor: VaspProcessing, tmp_path: Path, caplog
):
    caplog.set_level(logging.INFO)

    # 创建 4_vasp_optimized 目录和子文件夹
    for i in range(2):
        folder = vasp_processor.vasp_optimized_dir / f"2.876_{i:03d}"
        folder.mkdir(parents=True)
        # 创建 OUTCAR
        (folder / "OUTCAR").write_text("TOTEN = -10.123456\n", encoding="utf-8")
        # 创建 fine/CONTCAR
        fine_dir = folder / "fine"
        fine_dir.mkdir()
        (fine_dir / "CONTCAR").write_text("dummy", encoding="utf-8")
        # 创建 fine/final/CONTCAR
        final_dir = fine_dir / "final"
        final_dir.mkdir()
        (final_dir / "CONTCAR").write_text("dummy", encoding="utf-8")

        machine_path = tmp_path / "machine.yaml"
    resources_path = tmp_path / "resources.yaml"

    machine_path.write_text(
        """
context_type: LocalContext
local_root: ./ 
remote_root: /your/remote/workplace
batch_type: Shell
""",
        encoding="utf-8",
    )

    resources_path.write_text(
        """
number_node: 2
cpu_per_node: 8
gpu_per_node: 0
group_size: 1
""",
        encoding="utf-8",
    )

    vasp_processor.dpdisp_vasp_relaxation_tasks(
        machine_path=str(machine_path),
        resources_path=str(resources_path),
        nodes=2,
    )

    assert "Batch VASP optimization completed!!!" in caplog.text
    mock_sub.assert_called()
    mock_run.assert_called_once()


@patch("dpdispatcher.Submission.run_submission")
@patch("dpdispatcher.Submission.__init__", return_value=None)
@patch("dpdispatcher.Task.__init__", return_value=None)
def test_dpdisp_vasp_relaxation_tasks_no_fine_contcar(
    mock_task, mock_sub, mock_run, vasp_processor: VaspProcessing, tmp_path: Path, caplog
):
    caplog.set_level(logging.INFO)

    # 创建一个 folder，但没有 fine/CONTCAR
    folder = vasp_processor.vasp_optimized_dir / "2.876_001"
    folder.mkdir(parents=True)
    (folder / "OUTCAR").write_text("TOTEN = -10.123456\n", encoding="utf-8")
    # 没有 fine/CONTCAR

    machine_path = tmp_path / "machine.yaml"
    resources_path = tmp_path / "resources.yaml"

    machine_path.write_text("""
context_type: LocalContext
local_root: ./ 
remote_root: /your/remote/workplace
batch_type: Shell
""", encoding="utf-8")

    resources_path.write_text("""
number_node: 2
cpu_per_node: 8
gpu_per_node: 0
group_size: 1
""", encoding="utf-8")

    with pytest.raises(Exception):
        vasp_processor.dpdisp_vasp_relaxation_tasks(
            machine_path=str(machine_path),
            resources_path=str(resources_path),
            nodes=2,
        )

    assert "File" in caplog.text and "does not exist" in caplog.text
    mock_sub.assert_not_called()
    mock_run.assert_not_called()


# ==================== 测试 _read_mlp_properties ====================
def test_read_mlp_properties_success(vasp_processor: VaspProcessing, tmp_path: Path):
    contcar = tmp_path / "CONTCAR"
    outcar = tmp_path / "OUTCAR"
    contcar.write_text("""System with 2 atoms
1.0
5.0 0.0 0.0
0.0 5.0 0.0
0.0 0.0 5.0
C N
1 1
Direct
0.0 0.0 0.0
1.0 0.0 0.0""")
    # 正确的 OUTCAR 格式（必须包含 "eV"）
    outcar.write_text("TOTEN =     -10.123456 eV\n", encoding="utf-8")

    density, energy = vasp_processor._read_mlp_properties(contcar, outcar)

    # 验证结果
    assert density is not None
    assert energy == -10.1  # 四舍五入到一位小数

    # 额外验证：密度是否合理？体积 = 125 Å³，质量 = 12 + 14 = 26 amu
    # 密度 = 1.66054 * 26 / 125 ≈ 0.345 g/cm³
    assert abs(density - 0.345) < 0.01  # 允许微小误差


def test_read_mlp_properties_contcar_parse_error(vasp_processor: VaspProcessing, tmp_path: Path, caplog):
    """测试 CONTCAR 文件解析错误"""
    caplog.set_level(logging.ERROR)

    contcar = tmp_path / "CONTCAR"
    outcar = tmp_path / "OUTCAR"
    # 创建格式错误的 CONTCAR
    contcar.write_text("Invalid CONTCAR format\n", encoding="utf-8")
    outcar.write_text("TOTEN =     -10.123456 eV\n", encoding="utf-8")

    density, energy = vasp_processor._read_mlp_properties(contcar, outcar)

    # 验证返回值
    assert density is None
    assert energy == -10.1
    # 验证日志（可能是 "Error reading" 或 "Unexpected error reading"）
    assert "reading CONTCAR file" in caplog.text


def test_read_mlp_properties_contcar_not_found(vasp_processor: VaspProcessing, tmp_path: Path, caplog):
    """测试 CONTCAR 文件不存在"""
    caplog.set_level(logging.ERROR)

    contcar = tmp_path / "CONTCAR_NOT_EXIST"
    outcar = tmp_path / "OUTCAR"
    outcar.write_text("TOTEN =     -10.123456 eV\n", encoding="utf-8")

    density, energy = vasp_processor._read_mlp_properties(contcar, outcar)

    assert density is None
    assert energy == -10.1
    assert "Error reading CONTCAR file" in caplog.text


def test_read_mlp_properties_outcar_not_found(vasp_processor: VaspProcessing, tmp_path: Path, caplog):
    """测试 OUTCAR 文件不存在"""
    caplog.set_level(logging.ERROR)

    contcar = tmp_path / "CONTCAR"
    outcar = tmp_path / "OUTCAR_NOT_EXIST"
    contcar.write_text("""System with 2 atoms
1.0
5.0 0.0 0.0
0.0 5.0 0.0
0.0 0.0 5.0
C N
1 1
Direct
0.0 0.0 0.0
1.0 0.0 0.0""")

    density, energy = vasp_processor._read_mlp_properties(contcar, outcar)

    assert density is not None
    assert energy is None
    assert "Error reading OUTCAR file" in caplog.text


# ==================== 测试 _read_vasp_outcar ====================
@patch("ion_CSP.vasp_processing.read_vasp_out")
def test_read_vasp_outcar_success(mock_read_vasp_out, vasp_processor: VaspProcessing, tmp_path: Path):
    """测试成功读取 VASP OUTCAR 文件"""
    from ase import Atoms
    from ase.calculators.singlepoint import SinglePointCalculator

    # Mock read_vasp_out 返回一个 Atoms 对象
    mock_atoms = Atoms("CN", positions=[[0, 0, 0], [1, 0, 0]], cell=[5, 5, 5], pbc=False)
    # 使用 SinglePointCalculator 设置能量
    calc = SinglePointCalculator(mock_atoms, energy=-10.12345678)
    mock_atoms.calc = calc
    mock_read_vasp_out.return_value = mock_atoms

    outcar = tmp_path / "OUTCAR"
    outcar.write_text("dummy content", encoding="utf-8")

    atoms, density, energy = vasp_processor._read_vasp_outcar(outcar)

    # 验证结果
    assert atoms is not None
    assert len(atoms) == 2
    assert density > 0
    assert energy == -10.1  # 四舍五入到一位小数
    mock_read_vasp_out.assert_called_once()


def test_read_vasp_outcar_parse_error(vasp_processor: VaspProcessing, tmp_path: Path):
    """测试 OUTCAR 解析错误"""
    outcar = tmp_path / "OUTCAR"
    outcar.write_text("Invalid OUTCAR format\n", encoding="utf-8")

    atoms, density, energy = vasp_processor._read_vasp_outcar(outcar)

    # 验证返回值
    assert atoms is None
    assert density == float("-inf")
    assert energy == float("inf")


def test_read_vasp_outcar_file_not_found(vasp_processor: VaspProcessing, tmp_path: Path):
    """测试 OUTCAR 文件不存在"""
    outcar = tmp_path / "OUTCAR_NOT_EXIST"

    atoms, density, energy = vasp_processor._read_vasp_outcar(outcar)

    assert atoms is None
    assert density == float("-inf")
    assert energy == float("inf")


# ==================== 测试 read_vaspout_save_csv ====================
@patch("ion_CSP.vasp_processing.identify_molecules")
def test_read_vaspout_save_csv_without_relaxation(
    mock_identify, vasp_processor: VaspProcessing, tmp_path: Path, caplog
):
    """测试无弛豫模式的 CSV 生成"""
    caplog.set_level(logging.INFO)

    # 模拟 identify_molecules 返回值
    mock_identify.return_value = (
        {frozenset([("C", 1), ("H", 4)]): 1},  # molecules
        True,  # molecules_flag
        [{"C": 1, "H": 4}],  # initial_info
    )

    # 创建测试结构文件夹
    folder = vasp_processor.vasp_optimized_dir / "2.876_001"
    folder.mkdir(parents=True)

    # 创建 MLP CONTCAR 和 OUTCAR
    mlp_contcar = vasp_processor.vasp_optimized_dir / "CONTCAR_2.876_001"
    mlp_contcar.write_text("""System with 2 atoms
1.0
5.0 0.0 0.0
0.0 5.0 0.0
0.0 0.0 5.0
C N
1 1
Direct
0.0 0.0 0.0
1.0 0.0 0.0""", encoding="utf-8")

    mlp_outcar = vasp_processor.vasp_optimized_dir / "OUTCAR_2.876_001"
    mlp_outcar.write_text("TOTEN =     -10.123456 eV\n", encoding="utf-8")

    # 创建 Rough OUTCAR
    rough_outcar = folder / "OUTCAR"
    rough_outcar.write_text("""
 POTCAR:    PAW_PBE C 08Apr2002
 FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
  free  energy   TOTEN  =       -11.5 eV
 VOLUME and BASIS-vectors are now :
  volume of cell :      125.00
      direct lattice vectors
     5.000000000  0.000000000  0.000000000
     0.000000000  5.000000000  0.000000000
     0.000000000  0.000000000  5.000000000
 POSITION                                       TOTAL-FORCE (eV/Angst)
      0.00000      0.00000      0.00000         0.000000      0.000000      0.000000
      1.00000      0.00000      0.00000         0.000000      0.000000      0.000000
""", encoding="utf-8")

    # 创建 Fine OUTCAR
    fine_dir = folder / "fine"
    fine_dir.mkdir()
    fine_outcar = fine_dir / "OUTCAR"
    fine_outcar.write_text("""
 POTCAR:    PAW_PBE C 08Apr2002
 FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
  free  energy   TOTEN  =       -12.3 eV
 VOLUME and BASIS-vectors are now :
  volume of cell :      125.00
      direct lattice vectors
     5.000000000  0.000000000  0.000000000
     0.000000000  5.000000000  0.000000000
     0.000000000  0.000000000  5.000000000
 POSITION                                       TOTAL-FORCE (eV/Angst)
      0.00000      0.00000      0.00000         0.000000      0.000000      0.000000
      1.00000      0.00000      0.00000         0.000000      0.000000      0.000000
""", encoding="utf-8")

    # 执行测试
    vasp_processor.read_vaspout_save_csv(molecules_prior=False, relaxation=False)

    # 验证 CSV 文件生成
    csv_file = vasp_processor.base_dir / "vasp_density_energy.csv"
    assert csv_file.exists()

    # 读取并验证 CSV 内容
    import csv
    with csv_file.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["Number"] == "001"
        assert "Fine_Energy" in rows[0]
        assert "Final_Energy" not in rows[0]  # 无弛豫模式

    # 验证日志
    assert "Maximum MLP Density" in caplog.text
    assert "Maximum Fine Density" in caplog.text


@patch("ion_CSP.vasp_processing.identify_molecules")
def test_read_vaspout_save_csv_with_relaxation(
    mock_identify, vasp_processor: VaspProcessing, tmp_path: Path, caplog
):
    """测试有弛豫模式的 CSV 生成"""
    caplog.set_level(logging.INFO)

    mock_identify.return_value = (
        {frozenset([("C", 1), ("H", 4)]): 1},
        True,
        [{"C": 1, "H": 4}],
    )

    # 创建测试结构
    folder = vasp_processor.vasp_optimized_dir / "2.876_001"
    folder.mkdir(parents=True)

    mlp_contcar = vasp_processor.vasp_optimized_dir / "CONTCAR_2.876_001"
    mlp_contcar.write_text("""System with 2 atoms
1.0
5.0 0.0 0.0
0.0 5.0 0.0
0.0 0.0 5.0
C N
1 1
Direct
0.0 0.0 0.0
1.0 0.0 0.0""", encoding="utf-8")

    mlp_outcar = vasp_processor.vasp_optimized_dir / "OUTCAR_2.876_001"
    mlp_outcar.write_text("TOTEN =     -10.123456 eV\n", encoding="utf-8")

    rough_outcar = folder / "OUTCAR"
    rough_outcar.write_text("""
 POTCAR:    PAW_PBE C 08Apr2002
 FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
  free  energy   TOTEN  =       -11.5 eV
 VOLUME and BASIS-vectors are now :
  volume of cell :      125.00
      direct lattice vectors
     5.000000000  0.000000000  0.000000000
     0.000000000  5.000000000  0.000000000
     0.000000000  0.000000000  5.000000000
 POSITION                                       TOTAL-FORCE (eV/Angst)
      0.00000      0.00000      0.00000         0.000000      0.000000      0.000000
      1.00000      0.00000      0.00000         0.000000      0.000000      0.000000
""", encoding="utf-8")

    fine_dir = folder / "fine"
    fine_dir.mkdir()
    fine_outcar = fine_dir / "OUTCAR"
    fine_outcar.write_text("""
 POTCAR:    PAW_PBE C 08Apr2002
 FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
  free  energy   TOTEN  =       -12.3 eV
 VOLUME and BASIS-vectors are now :
  volume of cell :      125.00
      direct lattice vectors
     5.000000000  0.000000000  0.000000000
     0.000000000  5.000000000  0.000000000
     0.000000000  0.000000000  5.000000000
 POSITION                                       TOTAL-FORCE (eV/Angst)
      0.00000      0.00000      0.00000         0.000000      0.000000      0.000000
      1.00000      0.00000      0.00000         0.000000      0.000000      0.000000
""", encoding="utf-8")

    # 创建 Final OUTCAR
    final_dir = fine_dir / "final"
    final_dir.mkdir()
    final_outcar = final_dir / "OUTCAR"
    final_outcar.write_text("""
 POTCAR:    PAW_PBE C 08Apr2002
 FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
  free  energy   TOTEN  =       -13.8 eV
 VOLUME and BASIS-vectors are now :
  volume of cell :      125.00
      direct lattice vectors
     5.000000000  0.000000000  0.000000000
     0.000000000  5.000000000  0.000000000
     0.000000000  0.000000000  5.000000000
 POSITION                                       TOTAL-FORCE (eV/Angst)
      0.00000      0.00000      0.00000         0.000000      0.000000      0.000000
      1.00000      0.00000      0.00000         0.000000      0.000000      0.000000
""", encoding="utf-8")

    # 执行测试
    vasp_processor.read_vaspout_save_csv(molecules_prior=False, relaxation=True)

    # 验证 CSV 文件
    csv_file = vasp_processor.base_dir / "vasp_density_energy.csv"
    assert csv_file.exists()

    import csv
    with csv_file.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 1
        assert "Final_Energy" in rows[0]  # 有弛豫模式
        assert "Final_Density" in rows[0]

    assert "Maximum Final Density" in caplog.text


# ==================== 测试 export_max_density_structure ====================
def test_export_max_density_structure_without_relaxation(
    vasp_processor: VaspProcessing, tmp_path: Path, caplog
):
    """测试导出最高密度结构（无弛豫）"""
    caplog.set_level(logging.INFO)

    # 创建 CSV 文件
    csv_file = vasp_processor.base_dir / "vasp_density_energy.csv"
    csv_file.write_text("""Number,MLP_Energy,Rough_Energy,Fine_Energy,MLP_Density,Rough_Density,Fine_Density,Fine_Ions_Check,Fine_PC
001,-10.1,-11.5,-12.3,0.345,0.350,0.355,True,0.85
002,-10.2,-11.6,-12.4,0.340,0.345,0.350,True,0.83
003,-10.3,-11.7,-12.5,0.350,0.360,0.370,True,0.87
""", encoding="utf-8")

    # 创建对应的结构文件夹
    for num in ["001", "002", "003"]:
        folder = vasp_processor.vasp_optimized_dir / f"2.876_{num}"
        folder.mkdir(parents=True)
        fine_dir = folder / "fine"
        fine_dir.mkdir()
        contcar = fine_dir / "CONTCAR"
        contcar.write_text(f"Structure {num}\n", encoding="utf-8")

    # 执行导出
    vasp_processor.export_max_density_structure(relaxation=False)

    # 验证 POSCAR 文件生成
    poscar = vasp_processor.base_dir / "POSCAR"
    assert poscar.exists()
    content = poscar.read_text(encoding="utf-8")
    assert "Structure 003" in content  # 003 的密度最高

    # 验证日志
    assert "Maximum Fine Density: 0.37" in caplog.text
    assert "Structure Number: 003" in caplog.text


def test_export_max_density_structure_with_relaxation(
    vasp_processor: VaspProcessing, tmp_path: Path, caplog
):
    """测试导出最高密度结构（有弛豫）"""
    caplog.set_level(logging.INFO)

    # 创建 CSV 文件
    csv_file = vasp_processor.base_dir / "vasp_density_energy.csv"
    csv_file.write_text("""Number,MLP_Energy,Rough_Energy,Fine_Energy,Final_Energy,MLP_Density,Rough_Density,Fine_Density,Final_Density,Final_Ions_Check,Final_PC
001,-10.1,-11.5,-12.3,-13.8,0.345,0.350,0.355,0.365,True,0.85
002,-10.2,-11.6,-12.4,-13.9,0.340,0.345,0.350,0.360,True,0.83
003,-10.3,-11.7,-12.5,-14.0,0.350,0.360,0.370,0.380,True,0.87
""", encoding="utf-8")

    # 创建对应的结构文件夹
    for num in ["001", "002", "003"]:
        folder = vasp_processor.vasp_optimized_dir / f"2.876_{num}"
        folder.mkdir(parents=True)
        fine_dir = folder / "fine"
        fine_dir.mkdir()
        final_dir = fine_dir / "final"
        final_dir.mkdir()
        contcar = final_dir / "CONTCAR"
        contcar.write_text(f"Final Structure {num}\n", encoding="utf-8")

    # 执行导出
    vasp_processor.export_max_density_structure(relaxation=True)

    # 验证 POSCAR 文件
    poscar = vasp_processor.base_dir / "POSCAR"
    assert poscar.exists()
    content = poscar.read_text(encoding="utf-8")
    assert "Final Structure 003" in content

    assert "Maximum Final Density: 0.38" in caplog.text


def test_export_max_density_structure_no_csv(
    vasp_processor: VaspProcessing, caplog
):
    """测试 CSV 文件不存在的情况"""
    caplog.set_level(logging.INFO)

    vasp_processor.export_max_density_structure(relaxation=False)

    assert "CSV file not found" in caplog.text


def test_export_max_density_structure_no_valid_structure(
    vasp_processor: VaspProcessing, caplog
):
    """测试没有有效结构（Ions_Check 全为 False）"""
    caplog.set_level(logging.INFO)

    # 创建 CSV，所有 Ions_Check 都是 False
    csv_file = vasp_processor.base_dir / "vasp_density_energy.csv"
    csv_file.write_text("""Number,MLP_Energy,Rough_Energy,Fine_Energy,MLP_Density,Rough_Density,Fine_Density,Fine_Ions_Check,Fine_PC
001,-10.1,-11.5,-12.3,0.345,0.350,0.355,False,0.85
002,-10.2,-11.6,-12.4,0.340,0.345,0.350,False,0.83
""", encoding="utf-8")

    vasp_processor.export_max_density_structure(relaxation=False)

    assert "No valid structure found" in caplog.text


def test_export_max_density_structure_with_backup(
    vasp_processor: VaspProcessing, caplog
):
    """测试 POSCAR 备份机制"""
    caplog.set_level(logging.INFO)

    # 创建已存在的 POSCAR
    existing_poscar = vasp_processor.base_dir / "POSCAR"
    existing_poscar.write_text("Old POSCAR\n", encoding="utf-8")

    # 创建 CSV
    csv_file = vasp_processor.base_dir / "vasp_density_energy.csv"
    csv_file.write_text("""Number,MLP_Energy,Rough_Energy,Fine_Energy,MLP_Density,Rough_Density,Fine_Density,Fine_Ions_Check,Fine_PC
001,-10.1,-11.5,-12.3,0.345,0.350,0.355,True,0.85
""", encoding="utf-8")

    # 创建结构文件
    folder = vasp_processor.vasp_optimized_dir / "2.876_001"
    folder.mkdir(parents=True)
    fine_dir = folder / "fine"
    fine_dir.mkdir()
    contcar = fine_dir / "CONTCAR"
    contcar.write_text("New Structure\n", encoding="utf-8")

    # 执行导出
    vasp_processor.export_max_density_structure(relaxation=False)

    # 验证备份文件存在
    backup_files = list(vasp_processor.base_dir.glob("POSCAR.bak.*"))
    assert len(backup_files) == 1
    assert "Existing POSCAR backed up" in caplog.text

    # 验证新 POSCAR
    assert existing_poscar.read_text() == "New Structure\n"


# ==================== 测试 SSH 分支和异常处理 ====================
@patch("dpdispatcher.Submission.run_submission")
@patch("dpdispatcher.Submission.__init__", return_value=None)
@patch("dpdispatcher.Task.__init__", return_value=None)
def test_dpdisp_vasp_optimization_tasks_ssh_context(
    mock_task, mock_sub, mock_run, vasp_processor: VaspProcessing, tmp_path: Path, caplog
):
    """测试 SSH 模式下的目录清理"""
    caplog.set_level(logging.INFO)

    # 创建测试 CONTCAR 文件
    contcar = vasp_processor.for_vasp_opt_dir / "CONTCAR_001"
    contcar.write_text("dummy", encoding="utf-8")
    outcar = vasp_processor.for_vasp_opt_dir / "OUTCAR_001"
    outcar.write_text("TOTEN = -10.123456\n", encoding="utf-8")

    # 创建 data 目录（模拟 SSH 模式会创建的目录）
    data_dir = vasp_processor.for_vasp_opt_dir / "data"
    data_dir.mkdir()
    (data_dir / "test.txt").write_text("test", encoding="utf-8")

    # 创建 machine 和 resources（SSH 模式）
    machine_path = tmp_path / "machine.yaml"
    resources_path = tmp_path / "resources.yaml"

    machine_path.write_text("""
context_type: SSHContext
local_root: ./
remote_root: /remote/path
remote_profile:
  hostname: example.com
  username: user
batch_type: Shell
""", encoding="utf-8")

    resources_path.write_text("""
number_node: 1
cpu_per_node: 8
gpu_per_node: 0
group_size: 1
""", encoding="utf-8")

    # Mock machine.serialize() 返回 SSHContext
    from unittest.mock import MagicMock
    mock_machine = MagicMock()
    mock_machine.serialize.return_value = {"context_type": "SSHContext"}

    with patch("ion_CSP.vasp_processing.machine_resources_prep") as mock_prep:
        mock_prep.return_value = (mock_machine, MagicMock(), "test")

        # 执行
        vasp_processor.dpdisp_vasp_optimization_tasks(
            machine_path=str(machine_path),
            resources_path=str(resources_path),
            nodes=1,
        )

    # 验证 data 目录被删除
    assert not data_dir.exists()
    assert "Batch VASP optimization completed!!!" in caplog.text




def test_read_mlp_properties_outcar_generic_exception(
    vasp_processor: VaspProcessing, tmp_path: Path, caplog
):
    """测试 OUTCAR 读取的通用异常"""
    caplog.set_level(logging.ERROR)

    contcar = tmp_path / "CONTCAR"
    contcar.write_text("""System with 2 atoms
1.0
5.0 0.0 0.0
0.0 5.0 0.0
0.0 0.0 5.0
C N
1 1
Direct
0.0 0.0 0.0
1.0 0.0 0.0""")

    # 创建一个会导致通用异常的 OUTCAR（例如权限问题）
    outcar = tmp_path / "OUTCAR"
    outcar.write_text("TOTEN = invalid_value\n", encoding="utf-8")

    density, energy = vasp_processor._read_mlp_properties(contcar, outcar)

    assert density is not None
    assert energy is None
    assert "Unexpected error reading OUTCAR file" in caplog.text


@patch("ion_CSP.vasp_processing.read_vasp_out")
def test_read_vasp_outcar_generic_exception(
    mock_read_vasp_out, vasp_processor: VaspProcessing, tmp_path: Path, caplog
):
    """测试 _read_vasp_outcar 的通用异常"""
    caplog.set_level(logging.ERROR)

    # Mock 抛出通用异常
    mock_read_vasp_out.side_effect = RuntimeError("Unexpected error")

    outcar = tmp_path / "OUTCAR"
    outcar.write_text("dummy", encoding="utf-8")

    atoms, density, energy = vasp_processor._read_vasp_outcar(outcar)

    assert atoms is None
    assert density == float("-inf")
    assert energy == float("inf")
    assert "Unexpected error reading OUTCAR file" in caplog.text


# ==================== 测试 read_vaspout_save_csv 的边界情况 ====================
@patch("ion_CSP.vasp_processing.identify_molecules")
def test_read_vaspout_save_csv_invalid_folder_name(
    mock_identify, vasp_processor: VaspProcessing, caplog
):
    """测试文件夹名称格式异常 - 需要至少一个有效文件夹"""
    caplog.set_level(logging.WARNING)

    mock_identify.return_value = (
        {frozenset([("C", 1), ("H", 4)]): 1},
        True,
        [{"C": 1, "H": 4}],
    )

    # 创建一个格式错误的文件夹
    invalid_folder = vasp_processor.vasp_optimized_dir / "invalidname"
    invalid_folder.mkdir()

    # 创建一个有效的文件夹以避免空序列错误
    valid_folder = vasp_processor.vasp_optimized_dir / "2.876_001"
    valid_folder.mkdir()

    mlp_contcar = vasp_processor.vasp_optimized_dir / "CONTCAR_2.876_001"
    mlp_contcar.write_text("""System with 2 atoms
1.0
5.0 0.0 0.0
0.0 5.0 0.0
0.0 0.0 5.0
C N
1 1
Direct
0.0 0.0 0.0
1.0 0.0 0.0""", encoding="utf-8")

    mlp_outcar = vasp_processor.vasp_optimized_dir / "OUTCAR_2.876_001"
    mlp_outcar.write_text("TOTEN = -10.123456\n", encoding="utf-8")

    rough_outcar = valid_folder / "OUTCAR"
    rough_outcar.write_text("""
 POTCAR:    PAW_PBE C 08Apr2002
 FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
  free  energy   TOTEN  =       -11.5 eV
 VOLUME and BASIS-vectors are now :
  volume of cell :      125.00
      direct lattice vectors
     5.000000000  0.000000000  0.000000000
     0.000000000  5.000000000  0.000000000
     0.000000000  0.000000000  5.000000000
 POSITION                                       TOTAL-FORCE (eV/Angst)
      0.00000      0.00000      0.00000         0.000000      0.000000      0.000000
      1.00000      0.00000      0.00000         0.000000      0.000000      0.000000
""", encoding="utf-8")

    fine_dir = valid_folder / "fine"
    fine_dir.mkdir()
    fine_outcar = fine_dir / "OUTCAR"
    fine_outcar.write_text("""
 POTCAR:    PAW_PBE C 08Apr2002
 FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
  free  energy   TOTEN  =       -12.3 eV
 VOLUME and BASIS-vectors are now :
  volume of cell :      125.00
      direct lattice vectors
     5.000000000  0.000000000  0.000000000
     0.000000000  5.000000000  0.000000000
     0.000000000  0.000000000  5.000000000
 POSITION                                       TOTAL-FORCE (eV/Angst)
      0.00000      0.00000      0.00000         0.000000      0.000000      0.000000
      1.00000      0.00000      0.00000         0.000000      0.000000      0.000000
""", encoding="utf-8")

    vasp_processor.read_vaspout_save_csv(molecules_prior=False, relaxation=False)

    # 验证警告日志
    assert "Skipping folder with unexpected name format" in caplog.text
    # 验证 CSV 仍然生成（使用有效文件夹）
    csv_file = vasp_processor.base_dir / "vasp_density_energy.csv"
    assert csv_file.exists()


@patch("ion_CSP.vasp_processing.identify_molecules")
def test_read_vaspout_save_csv_packing_coefficient_exception(
    mock_identify, vasp_processor: VaspProcessing, caplog
):
    """测试堆积系数计算异常（缺少 JSON 文件）"""
    caplog.set_level(logging.INFO)

    mock_identify.return_value = (
        {frozenset([("C", 1), ("H", 4)]): 1},
        True,
        [{"C": 1, "H": 4}],
    )

    # 删除 JSON 文件（但保留 config.yaml）
    (vasp_processor.base_dir / "N2O.json").unlink()
    (vasp_processor.base_dir / "H2O.json").unlink()

    # 创建测试结构
    folder = vasp_processor.vasp_optimized_dir / "2.876_001"
    folder.mkdir()

    mlp_contcar = vasp_processor.vasp_optimized_dir / "CONTCAR_2.876_001"
    mlp_contcar.write_text("""System with 2 atoms
1.0
5.0 0.0 0.0
0.0 5.0 0.0
0.0 0.0 5.0
C N
1 1
Direct
0.0 0.0 0.0
1.0 0.0 0.0""", encoding="utf-8")

    mlp_outcar = vasp_processor.vasp_optimized_dir / "OUTCAR_2.876_001"
    mlp_outcar.write_text("TOTEN = -10.123456\n", encoding="utf-8")

    rough_outcar = folder / "OUTCAR"
    rough_outcar.write_text("""
 POTCAR:    PAW_PBE C 08Apr2002
 FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
  free  energy   TOTEN  =       -11.5 eV
 VOLUME and BASIS-vectors are now :
  volume of cell :      125.00
      direct lattice vectors
     5.000000000  0.000000000  0.000000000
     0.000000000  5.000000000  0.000000000
     0.000000000  0.000000000  5.000000000
 POSITION                                       TOTAL-FORCE (eV/Angst)
      0.00000      0.00000      0.00000         0.000000      0.000000      0.000000
      1.00000      0.00000      0.00000         0.000000      0.000000      0.000000
""", encoding="utf-8")

    fine_dir = folder / "fine"
    fine_dir.mkdir()
    fine_outcar = fine_dir / "OUTCAR"
    fine_outcar.write_text("""
 POTCAR:    PAW_PBE C 08Apr2002
 FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
  free  energy   TOTEN  =       -12.3 eV
 VOLUME and BASIS-vectors are now :
  volume of cell :      125.00
      direct lattice vectors
     5.000000000  0.000000000  0.000000000
     0.000000000  5.000000000  0.000000000
     0.000000000  0.000000000  5.000000000
 POSITION                                       TOTAL-FORCE (eV/Angst)
      0.00000      0.00000      0.00000         0.000000      0.000000      0.000000
      1.00000      0.00000      0.00000         0.000000      0.000000      0.000000
""", encoding="utf-8")

    # 应该能正常运行，PC 值为 False
    vasp_processor.read_vaspout_save_csv(molecules_prior=False, relaxation=False)

    csv_file = vasp_processor.base_dir / "vasp_density_energy.csv"
    assert csv_file.exists()

    # 验证 CSV 中 PC 值为 False
    import csv
    with csv_file.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert rows[0]["Fine_PC"] == "False"


def test_export_max_density_structure_csv_parse_error(
    vasp_processor: VaspProcessing, caplog
):
    """测试 CSV 解析错误"""
    caplog.set_level(logging.WARNING)

    # 创建格式错误的 CSV（缺少必需的列）
    csv_file = vasp_processor.base_dir / "vasp_density_energy.csv"
    csv_file.write_text("""Number,Energy
001,-10.1
""", encoding="utf-8")

    vasp_processor.export_max_density_structure(relaxation=False)

    assert "Required column not found" in caplog.text


def test_export_max_density_structure_incomplete_row(
    vasp_processor: VaspProcessing, caplog
):
    """测试 CSV 中不完整的行"""
    caplog.set_level(logging.WARNING)

    csv_file = vasp_processor.base_dir / "vasp_density_energy.csv"
    csv_file.write_text("""Number,MLP_Energy,Rough_Energy,Fine_Energy,MLP_Density,Rough_Density,Fine_Density,Fine_Ions_Check,Fine_PC
001,-10.1
002,-10.2,-11.6,-12.4,0.340,0.345,0.350,True,0.83
""", encoding="utf-8")

    # 创建结构文件
    folder = vasp_processor.vasp_optimized_dir / "2.876_002"
    folder.mkdir(parents=True)
    fine_dir = folder / "fine"
    fine_dir.mkdir()
    contcar = fine_dir / "CONTCAR"
    contcar.write_text("Structure 002\n", encoding="utf-8")

    vasp_processor.export_max_density_structure(relaxation=False)

    # 应该跳过不完整的行，使用 002
    poscar = vasp_processor.base_dir / "POSCAR"
    assert poscar.exists()
    assert "Warning: Incomplete row" in caplog.text


def test_export_max_density_structure_contcar_not_found(
    vasp_processor: VaspProcessing, caplog
):
    """测试目标 CONTCAR 不存在"""
    caplog.set_level(logging.INFO)

    csv_file = vasp_processor.base_dir / "vasp_density_energy.csv"
    csv_file.write_text("""Number,MLP_Energy,Rough_Energy,Fine_Energy,MLP_Density,Rough_Density,Fine_Density,Fine_Ions_Check,Fine_PC
001,-10.1,-11.5,-12.3,0.345,0.350,0.355,True,0.85
""", encoding="utf-8")

    # 创建文件夹但不创建 CONTCAR
    folder = vasp_processor.vasp_optimized_dir / "2.876_001"
    folder.mkdir(parents=True)
    fine_dir = folder / "fine"
    fine_dir.mkdir()
    # 不创建 CONTCAR

    vasp_processor.export_max_density_structure(relaxation=False)

    assert "Eligible CONTCAR not found" in caplog.text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=ion_CSP.vasp_processing"])
    