import pytest
import logging
from pathlib import Path
from unittest.mock import patch

from ion_CSP.convert_SMILES import SmilesProcessing


@pytest.fixture
def smiles_processor(tmp_path: Path):
    """
    每个测试获得一个全新的、干净的 SmilesProcessing 实例。
    自动清理所有可能残留的目录，确保测试隔离。
    """
    # 1. 创建独立的工作目录
    base_dir = tmp_path / "test_work_dir"
    base_dir.mkdir(parents=True, exist_ok=True)

    # 2. 创建测试用的 CSV 文件
    csv_data = """SMILES,Charge,Refcode,Number
CCO,0,REF001,1
C[N+](C)(C)C,1,REF002,2
C1=CC=NC=C1,0,REF003,3
[O-]C=O,-1,REF004,4
invalid_smiles,0,REF005,5"""
    csv_path = base_dir / "test.csv"
    csv_path.write_text(csv_data, encoding="utf-8")

    # 3. 创建 param 资源目录
    param_dir = base_dir / "param"
    param_dir.mkdir()
    (param_dir / "g16_sub.sh").write_text("echo 'Mock script'", encoding="utf-8")

    # 4. 模拟 importlib.resources.files
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value = param_dir

        # 5. 模拟日志重定向
        with patch("ion_CSP.convert_SMILES.redirect_dpdisp_logging"):

            # 6. 创建实例 —— 它会自动创建 converted_dir 和 gaussian_optimized_dir
            sp = SmilesProcessing(base_dir, "test.csv")
            yield sp


# ==================== 测试初始化 ====================
def test_initialization(smiles_processor: SmilesProcessing):
    sp = smiles_processor
    assert sp.base_dir.exists()
    assert sp.base_name == "Refcode"
    assert len(sp.df) == 5
    assert list(sp.df["Refcode"]) == ["REF001", "REF002", "REF003", "REF004", "REF005"]
    assert len(sp.grouped) == 3  # 三个电荷组: -1, 0, 1


# ==================== 测试 charge_group() ====================
def test_charge_group_success(smiles_processor: SmilesProcessing, caplog):
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    # 执行成功转换
    sp.charge_group()

    # 验证输出目录
    output_dir = sp.converted_dir
    assert output_dir.exists()

    # 验证电荷分组
    charge_dirs = [d for d in output_dir.iterdir() if d.is_dir()]
    assert len(charge_dirs) == 3
    assert set(d.name for d in charge_dirs) == {"charge_-1", "charge_0", "charge_1"}

    # 验证文件生成
    assert (output_dir / "charge_0" / "REF001.gjf").exists()
    assert (output_dir / "charge_1" / "REF002.gjf").exists()
    assert (output_dir / "charge_-1" / "REF004.gjf").exists()

    # 验证日志：成功生成 + 无效 SMILES 被记录
    assert "Successfully generated .gjf files: 4" in caplog.text
    assert "Errors encounted: 1" in caplog.text
    assert "REF005" in caplog.text
    assert "Invalid SMILES:" in caplog.text


def test_charge_group_failure_no_csv(smiles_processor: SmilesProcessing, caplog):
    # 重新创建实例，但传入不存在的 CSV
    with pytest.raises(Exception, match="Necessary .csv file not provided:"):
        SmilesProcessing(smiles_processor.base_dir, "nonexistent.csv")


# ==================== 测试 screen() ====================
def test_screen_success(smiles_processor: SmilesProcessing, caplog):
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    # 筛选带正电荷的 [N+] 基团
    sp.screen(
        charge_screen=1,
        group_screen="[N+]",
        group_name="quaternary_ammonium",
        group_screen_invert=False,
    )

    # 验证输出目录
    screen_dir = sp.converted_dir / "quaternary_ammonium_1"
    assert screen_dir.exists()

    # 验证只生成目标文件
    files = list(screen_dir.glob("*.gjf"))
    assert len(files) == 1
    assert files[0].name == "REF002.gjf"

    # 验证日志
    assert (
        "Number of ions with charge of [1] and quaternary_ammonium group: 1"
        in caplog.text
    )


def test_screen_failure_no_match(smiles_processor: SmilesProcessing, caplog):
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    # 筛选一个不存在的基团
    sp.screen(
        charge_screen=0,
        group_screen="XYZ",  # 不存在
        group_name="xyz_group",
        group_screen_invert=False,
    )

    # 验证输出目录被创建（即使为空）
    screen_dir = sp.converted_dir / "xyz_group_0"
    assert screen_dir.exists()
    assert len(list(screen_dir.glob("*.gjf"))) == 0

    # 验证日志
    assert "Number of ions with charge of [0] and xyz_group group: 0" in caplog.text


# ==================== 测试 dpdisp_gaussian_tasks() ====================
@patch("dpdispatcher.Submission.run_submission")
@patch("dpdispatcher.Submission.__init__", return_value=None)
@patch("dpdispatcher.Task.__init__", return_value=None)
def test_dpdisp_gaussian_tasks_success(
    mock_task,
    mock_sub,
    mock_run,
    smiles_processor: SmilesProcessing,
    tmp_path: Path,
    caplog,
):
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    # 1. 创建测试 .gjf 文件（真实存在，但由 fixture 保证目录干净）
    charge1_dir = sp.converted_dir / "charge_1"
    charge1_dir.mkdir(parents=True, exist_ok=True)
    (charge1_dir / "REF001.gjf").write_text("dummy content", encoding="utf-8")
    (charge1_dir / "REF002.gjf").write_text("dummy content", encoding="utf-8")

    # 2. 创建 machine 和 resources 配置文件
    machine_config = tmp_path / "machine.json"
    resources_config = tmp_path / "resources.json"

    machine_config.write_text(
        """
{
    "context_type": "LocalContext",
    "local_root": "./",
    "remote_root": "/workplace/autodpgen/pytest",
    "batch_type": "Shell"
}
""",
        encoding="utf-8",
    )

    resources_config.write_text(
        """
{
    "number_node": 1,
    "cpu_per_node": 4,
    "gpu_per_node": 0,
    "queue_name": "normal",
    "group_size": 1
}
""",
        encoding="utf-8",
    )

    # 3. 保留所有 mock：只 mock dpdispatcher，不 mock shutil
    #    让 shutil.copyfile 真实执行，文件才能被复制到 optimized_dir
    sp.dpdisp_gaussian_tasks(
        folders=["charge_1"],
        machine_path=str(machine_config),
        resources_path=str(resources_config),
        nodes=2,
    )

    # 4. 验证日志成功
    assert "Batch Gaussian optimization completed!!!" in caplog.text

    # 5. 验证优化目录被创建，且文件被复制
    opt_dir = sp.gaussian_optimized_dir / "charge_1"
    assert opt_dir.exists()
    assert (opt_dir / "REF001.gjf").exists()
    assert (opt_dir / "REF002.gjf").exists()

    # 6. 验证 dpdispatcher 被调用
    mock_sub.assert_called()
    mock_task.assert_called()
    mock_run.assert_called_once()


@patch("dpdispatcher.Submission.run_submission")
@patch("dpdispatcher.Submission.__init__", return_value=None)
@patch("dpdispatcher.Task.__init__", return_value=None)
def test_dpdisp_gaussian_tasks_failure_no_files(
    mock_task,
    mock_sub,
    mock_run,
    smiles_processor: SmilesProcessing,
    tmp_path: Path,
    caplog,
):
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    # 1. 创建目录，但**不创建任何 .gjf 文件**
    charge1_dir = sp.converted_dir / "charge_1"
    charge1_dir.mkdir(parents=True, exist_ok=True)

    # 2. 创建 machine 和 resources 配置文件
    machine_config = tmp_path / "machine.json"
    resources_config = tmp_path / "resources.json"

    machine_config.write_text(
        """
{
    "context_type": "LocalContext",
    "local_root": "./",
    "remote_root": "/workplace/autodpgen/pytest",
    "batch_type": "Shell"
}
""",
        encoding="utf-8",
    )

    resources_config.write_text(
        """
{
    "number_node": 1,
    "cpu_per_node": 4,
    "gpu_per_node": 0,
    "queue_name": "normal",
    "group_size": 1
}
""",
        encoding="utf-8",
    )

    # 3. 创建 config.yaml 文件（必须存在，否则会报错）
    config_path = sp.base_dir / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
gen_opt:
  species: ["N2O.json", "H2O.json"]
  ion_numbers: [2, 1]
""",
        encoding="utf-8",
    )


    # 4. 执行一次：没有 .gjf 文件 → 应触发 "No available folders..." 日志
    sp.dpdisp_gaussian_tasks(
        folders=["charge_1"],
        machine_path=str(machine_config),
        resources_path=str(resources_config),
        nodes=2,
    )

    # 5. 验证日志：提示无文件
    assert (
        "No .gjf files found in folder: charge_1" in caplog.text
    )

    # 6. 验证优化目录未被创建
    opt_dir = sp.gaussian_optimized_dir / "charge_1"
    assert not opt_dir.exists()

    # 7. 验证 dpdispatcher 未被调用
    mock_sub.assert_not_called()
    mock_task.assert_not_called()
    mock_run.assert_not_called()


# ==================== 测试错误处理 ====================
def test_error_handling(smiles_processor: SmilesProcessing, caplog):
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    # 1. 空文件名
    with pytest.raises(Exception, match="Necessary .csv file not provided!"):
        SmilesProcessing(sp.base_dir, "")

    # 2. 传入目录
    with pytest.raises(Exception, match="Expected a CSV file, but got a directory"):
        SmilesProcessing(sp.base_dir, ".")

    # 3. 文件不存在
    with pytest.raises(Exception, match="Necessary .csv file not provided:"):
        SmilesProcessing(sp.base_dir, "nonexistent.csv")

    # 4. CSV 文件缺少必要列：无 SMILES
    bad_csv = sp.base_dir / "bad.csv"
    bad_csv.write_text("Charge,Refcode\n1,REF001")
    with pytest.raises(
        Exception, match="CSV file missing required columns: {'SMILES'}"
    ):
        SmilesProcessing(sp.base_dir, bad_csv.name)

    # 5. CSV 文件缺少 Charge
    bad_csv.write_text("SMILES,Refcode\nCCO,REF001")
    with pytest.raises(
        Exception, match="CSV file missing required columns: {'Charge'}"
    ):
        SmilesProcessing(sp.base_dir, bad_csv.name)

    # 6. CSV 文件既无 Refcode 也无 Number
    bad_csv.write_text("SMILES,Charge\nCCO,0")
    with pytest.raises(
        Exception, match="CSV file must contain at least one of:"
    ):
        SmilesProcessing(sp.base_dir, bad_csv.name)

    # 7. CSV 文件为空
    bad_csv.write_text("")
    with pytest.raises(Exception, match="CSV file is empty"):
        SmilesProcessing(sp.base_dir, bad_csv.name)

    # 8. 无效 SMILES 日志（正常流程）
    sp = SmilesProcessing(sp.base_dir, "test.csv")
    sp.charge_group()
    assert "REF005" in caplog.text
    assert "Invalid SMILES:" in caplog.text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=ion_CSP.convert_SMILES"])