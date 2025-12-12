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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=ion_CSP.vasp_processing"])
    