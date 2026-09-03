import json
import pytest
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

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

        # 5. 创建实例 —— 它会自动创建 converted_dir 和 gaussian_optimized_dir
        sp = SmilesProcessing(base_dir, "test.csv", structure_snapshots=False)
        yield sp


# ==================== 测试初始化 ====================
def test_initialization_success(smiles_processor: SmilesProcessing):
    sp = smiles_processor
    assert sp.base_dir.exists()
    assert sp.base_name == "Refcode"
    assert len(sp.df) == 5
    assert list(sp.df["Refcode"]) == ["REF001", "REF002", "REF003", "REF004", "REF005"]
    assert len(sp.grouped) == 3  # 三个电荷组: -1, 0, 1
    assert not (sp.base_dir / "dpdispatcher.log").exists()


def test_initialization_sort_by_number_when_refcode_missing(
    smiles_processor: SmilesProcessing, caplog
):
    """测试当 CSV 缺少 Refcode 列时，自动使用 Number 排序，并设置 self.base_name = 'Number'"""
    csv_data = """SMILES,Charge,Number
CCO,0,1
C[N+](C)(C)C,1,2"""
    csv_path = smiles_processor.base_dir / "no_refcode.csv"
    csv_path.write_text(csv_data, encoding="utf-8")

    sp = SmilesProcessing(smiles_processor.base_dir, csv_path.name)

    assert sp.base_name == "Number"  # 验证排序依据


# ==================== 测试 _validate_csv_format() ====================
def test_validate_csv_format_parser_error(smiles_processor: SmilesProcessing):
    """Test that a malformed CSV (e.g., unmatched quotes) raises ParserError"""
    csv_data = """SMILES,Charge,Refcode
"CCO,0,REF001
C[N+](C)(C)C,1,REF002"""
    csv_path = smiles_processor.base_dir / "malformed.csv"
    csv_path.write_text(csv_data, encoding="utf-8")

    with pytest.raises(
        Exception, match=r"CSV file is malformed \(e.g., wrong delimiter\):.*\nError:"
    ):
        SmilesProcessing(smiles_processor.base_dir, csv_path.name)


def test_validate_csv_format_non_numeric_charge(smiles_processor: SmilesProcessing):
    """测试 Charge 列为非数值类型（如字符串）时抛出异常"""
    csv_data = """SMILES,Charge,Refcode
CCO,abc,REF001"""
    csv_path = smiles_processor.base_dir / "non_numeric_charge.csv"
    csv_path.write_text(csv_data, encoding="utf-8")

    with pytest.raises(
        Exception, match=r"Column 'Charge' must be numeric"
    ):
        SmilesProcessing(smiles_processor.base_dir, csv_path.name)


# ==================== 测试 _convert_SMILES() ====================        
@patch("rdkit.Chem.AddHs")
def test_convert_SMILES_add_hydrogens_exception(
    mock_add_hydrogens, smiles_processor: SmilesProcessing, caplog
):
    """测试添加氢原子时抛出异常，应记录错误并返回失败码"""
    sp = smiles_processor
    caplog.set_level(logging.ERROR)

    # 模拟 AddHs 抛异常
    mock_add_hydrogens.side_effect = Exception("Failed to add hydrogens")

    # 有效 SMILES，但添加氢时失败
    result_flag, basename = sp._convert_SMILES(
        dir_path=sp.converted_dir / "charge_0",
        smiles="CCO",
        basename="REF001",
        charge=0,
    )

    assert result_flag is False
    assert (
        "Error occurred while adding hydrogens to molecule REF001 with charge 0: Failed to add hydrogens"
        in caplog.text
    )


def test_convert_SMILES_charge_mismatch(smiles_processor: SmilesProcessing, caplog):
    """测试计算出的分子电荷与 CSV 中指定电荷不一致，应记录错误并返回失败、不写文件"""
    sp = smiles_processor
    caplog.set_level(logging.ERROR)

    dir_path = sp.converted_dir / "charge_1"
    dir_path.mkdir(parents=True, exist_ok=True)

    # 乙醇 CCO，实际电荷为 0，但指定为 1
    result_flag, basename = sp._convert_SMILES(
        dir_path=dir_path,
        smiles="CCO",
        basename="REF001",
        charge=1,  # 给定电荷错误
    )

    assert result_flag is False  # 电荷不匹配视为失败
    assert "REF001: charge wrong! calculated 0 and given 1" in caplog.text
    # 不应写入被错误分组的结构文件
    assert not (dir_path / "REF001.gjf").exists()


def test_convert_SMILES_adds_smiles_bond_constraints(
    smiles_processor: SmilesProcessing,
):
    result_flag, _ = smiles_processor._convert_SMILES(
        dir_path=smiles_processor.converted_dir / "charge_0",
        smiles="CCO",
        basename="CONSTRAINTS",
        charge=0,
    )

    assert result_flag is True
    content = (
        smiles_processor.converted_dir / "charge_0" / "CONSTRAINTS.gjf"
    ).read_text(encoding="utf-8")
    assert "opt=(MaxCycles=100,ModRedundant)" in content
    assert "B 1 2 F" in content
    assert content.count("\nB ") == 8


def test_convert_SMILES_can_disable_smiles_bond_constraints(
    smiles_processor: SmilesProcessing,
):
    result_flag, _ = smiles_processor._convert_SMILES(
        dir_path=smiles_processor.converted_dir / "charge_0",
        smiles="CCO",
        basename="NO_CONSTRAINTS",
        charge=0,
        preserve_topology=False,
    )

    assert result_flag is True
    content = (
        smiles_processor.converted_dir / "charge_0" / "NO_CONSTRAINTS.gjf"
    ).read_text(encoding="utf-8")
    assert "ModRedundant" not in content
    assert "\nB " not in content


def test_convert_SMILES_multiplicity_radical(smiles_processor: SmilesProcessing):
    """自由基的多重度应为 未成对电子数+1（甲基自由基 → 2），而非 2n+1（=3）"""
    sp = smiles_processor
    dir_path = sp.converted_dir / "charge_0"
    dir_path.mkdir(parents=True, exist_ok=True)

    # 甲基自由基 [CH3]：1 个未成对电子 → 多重度应为 2
    result_flag, _ = sp._convert_SMILES(
        dir_path=dir_path, smiles="[CH3]", basename="RAD001", charge=0
    )

    assert result_flag is True
    content = (dir_path / "RAD001.gjf").read_text()
    # Gaussian 输入的电荷/多重度行应为 "0 2"
    assert "\n0 2\n" in content
    assert "0 3" not in content


@patch("pathlib.Path.write_text")
def test_convert_SMILES_gjf_write_exception(
    mock_write_text, smiles_processor: SmilesProcessing, caplog
):
    """测试生成 .gjf 文件时写入失败，应捕获异常并返回失败码"""
    sp = smiles_processor
    caplog.set_level(logging.ERROR)

    mock_write_text.side_effect = PermissionError("Permission denied")

    result_flag, basename = sp._convert_SMILES(
        dir_path=sp.converted_dir / "charge_0",
        smiles="CCO",
        basename="REF001",
        charge=0,
    )

    assert result_flag is False
    assert (
        "Error occurred while optimizing molecule of REF001 with charge 0: Permission denied"
        in caplog.text
    )


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


def test_charge_group_renders_initial_bonded_snapshots(tmp_path: Path):
    work_dir = tmp_path / "snapshot_project"
    work_dir.mkdir()
    (work_dir / "ions.csv").write_text(
        "SMILES,Charge,Refcode\nNNN,0,N3\n", encoding="utf-8"
    )
    processor = SmilesProcessing(
        work_dir,
        "ions.csv",
        structure_snapshots=True,
        snapshot_dpi=72,
    )

    processor.charge_group()

    snapshot_dir = work_dir / "structure_snapshots/initial/charge_0/N3"
    manifest = json.loads((snapshot_dir / "N3_initial_snapshot.json").read_text())
    assert manifest["stage"] == "initial"
    assert manifest["topology_match"] is True
    assert len(manifest["views"]) == 4
    assert (snapshot_dir / manifest["multiview"]).is_file()


def test_snapshot_render_failure_does_not_discard_gaussian_input(
    smiles_processor: SmilesProcessing, caplog
):
    smiles_processor.structure_snapshots = True
    with patch(
        "ion_CSP.convert_SMILES.render_gjf_snapshots",
        side_effect=RuntimeError("render failed"),
    ):
        result, _ = smiles_processor._convert_SMILES(
            smiles_processor.converted_dir / "charge_0",
            "CCO",
            "SNAPSHOT_WARNING",
            0,
        )

    assert result is True
    assert "Unable to render initial snapshots" in caplog.text
    assert (
        smiles_processor.converted_dir / "charge_0/SNAPSHOT_WARNING.gjf"
    ).is_file()


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


def test_screen_only_charge_screen(smiles_processor: SmilesProcessing, caplog):
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    # 筛选带正电荷的 [N+] 基团
    sp.screen(
        charge_screen=1,
        group_name="only_charge",
    )

    # 验证输出目录
    screen_dir = sp.converted_dir / "only_charge_1"
    assert screen_dir.exists()

    # 验证只生成目标文件
    files = list(screen_dir.glob("*.gjf"))
    assert len(files) == 1
    assert files[0].name == "REF002.gjf"

    # 验证日志
    assert "Number of ions with charge of [1] and only_charge group: 1" in caplog.text


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

    # 验证日志
    assert "Number of ions with charge of [0] and xyz_group group: 0" in caplog.text


def test_screen_invert_condition(smiles_processor: SmilesProcessing, caplog):
    """测试 group_screen_invert=True 时，筛选不包含指定基团的离子"""
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    # 筛选不包含 [N+] 的离子（即排除 REF002）
    sp.screen(
        charge_screen=0,
        group_screen="[N+]",
        group_name="non_quaternary",
        group_screen_invert=True,  # 关键：取反
    )

    # 验证只保留 REF001 和 REF003（CCO 和 C1=CC=NC=C1）
    screen_dir = sp.converted_dir / "non_quaternary_0"
    assert screen_dir.exists()
    files = list(screen_dir.glob("*.gjf"))
    assert len(files) == 2
    assert any(f.name == "REF001.gjf" for f in files)
    assert any(f.name == "REF003.gjf" for f in files)
    assert not any(f.name == "REF002.gjf" for f in files)

    # 验证日志
    assert (
        "Number of ions with charge of [0] and non_quaternary group: 2" in caplog.text
    )


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
def test_dpdisp_gaussian_tasks_folder_exists_in_base_dir_only(
    mock_task,
    mock_sub,
    mock_run,
    smiles_processor: SmilesProcessing,
    tmp_path: Path,
    caplog,
):
    """测试当文件夹在 converted_dir 不存在，但在 base_dir 存在时，正确处理"""
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    # 1. 创建测试 .gjf 文件（真实存在，但由 fixture 保证目录干净）
    charge1_dir = sp.base_dir / "charge_1"
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

    # 5. 验证日志：提示无需优化的文件
    assert (
        "No .gjf files need Gaussian optimization in folder: charge_1" in caplog.text
    )

    # 6. 验证优化目录未被创建
    opt_dir = sp.gaussian_optimized_dir / "charge_1"
    assert not opt_dir.exists()

    # 7. 验证 dpdispatcher 未被调用
    mock_sub.assert_not_called()
    mock_task.assert_not_called()
    mock_run.assert_not_called()


@patch("dpdispatcher.Submission.run_submission")
@patch("dpdispatcher.Submission.__init__", return_value=None)
@patch("dpdispatcher.Task.__init__", return_value=None)
@patch("dpdispatcher.contexts.ssh_context.SSHSession._setup_ssh")
@patch("dpdispatcher.contexts.ssh_context.SSHSession.ensure_alive")
@patch("dpdispatcher.contexts.ssh_context.SSHSession.sftp", new_callable=MagicMock)
@patch("shutil.rmtree")
def test_dpdisp_gaussian_tasks_cleanup_ssh_context(
    mock_rmtree,
    mock_sftp,
    mock_ensure_alive,
    mock_setup_ssh,
    mock_task,
    mock_sub,
    mock_run,
    smiles_processor: SmilesProcessing,
    tmp_path: Path,
):
    """测试当使用 SSHContext 时，任务完成后删除 data/ 目录"""
    sp = smiles_processor
    machine_path = tmp_path / "machine.json"
    resources_path = tmp_path / "resources.json"

    machine_config = {
        "context_type": "SSHContext",
        "local_root": "./",
        "remote_root": "/remote/workplace",
        "batch_type": "Shell",
        "remote_profile": {
            "hostname": "your.host.name.IPv4",
            "username": "your_username",
        }
    }
    machine_path.write_text(json.dumps(machine_config, indent=2), encoding="utf-8")
    resources_config = {
        "number_node": 1,
        "cpu_per_node": 4,
        "gpu_per_node": 0,
        "group_size": 1,
    }
    resources_path.write_text(json.dumps(resources_config, indent=2), encoding="utf-8")

    # 创建 converted_dir/data/ 目录（模拟远程结构）
    (sp.converted_dir / "data").mkdir(parents=True)

    sp.dpdisp_gaussian_tasks(
        folders=["charge_0"],
        machine_path=str(machine_path),
        resources_path=str(resources_path),
        nodes=1,
    )

    # 验证 rmtree 被调用，删除的是 data/ 目录
    mock_rmtree.assert_called_once_with(sp.converted_dir / "data", ignore_errors=True)


def test_dpdisp_gaussian_tasks_no_folders(smiles_processor: SmilesProcessing, caplog):
    """测试未传入任何文件夹时，记录错误并提前返回"""
    sp = smiles_processor
    caplog.set_level(logging.ERROR)

    sp.dpdisp_gaussian_tasks(
        folders=[],
        machine_path="dummy.json",
        resources_path="dummy.json",
        nodes=1,
    )

    assert (
        "No available folders for dpdispatcher to process Gaussian tasks."
        in caplog.text
    )


def test_dpdisp_gaussian_tasks_folder_not_exist_in_both_dirs(
    smiles_processor: SmilesProcessing, tmp_path: Path, caplog
):
    """测试提供的文件夹在 converted_dir 和 base_dir 都不存在"""
    sp = smiles_processor
    caplog.set_level(logging.ERROR)

    machine_path = tmp_path / "machine.json"
    resources_path = tmp_path / "resources.json"
    machine_config = {
        "context_type": "LocalContext",
        "local_root": "./",
        "remote_root": "/remote/workplace",
        "batch_type": "Shell",
    }
    machine_path.write_text(json.dumps(machine_config, indent=2), encoding="utf-8")
    resources_config = {
        "number_node": 1,
        "cpu_per_node": 4,
        "gpu_per_node": 0,
        "group_size": 1,
    }
    resources_path.write_text(json.dumps(resources_config, indent=2), encoding="utf-8")

    sp.dpdisp_gaussian_tasks(
        folders=["nonexistent_folder"],
        machine_path=str(machine_path),
        resources_path=str(resources_path),
        nodes=1,
    )

    assert (
        "Provided folder nonexistent_folder is not either in the work directory or the converted directory."
        in caplog.text
    )


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
    sp = SmilesProcessing(sp.base_dir, "test.csv", structure_snapshots=False)
    sp.charge_group()
    assert "REF005" in caplog.text
    assert "Invalid SMILES:" in caplog.text


# ==================== 测试 _parse_gaussian_error ====================
def test_parse_gaussian_error_log_not_found(tmp_path: Path):
    """日志文件不存在时返回对应提示"""
    missing = tmp_path / "nope.log"
    assert (
        SmilesProcessing._parse_gaussian_error(missing)
        == "Gaussian log file not found"
    )


def test_parse_gaussian_error_unreadable(tmp_path: Path):
    """读取日志抛 OSError 时返回可读的错误信息"""
    # 用目录冒充日志文件，read_text 会抛 IsADirectoryError(OSError 子类)
    fake_log = tmp_path / "dir.log"
    fake_log.mkdir()
    result = SmilesProcessing._parse_gaussian_error(fake_log)
    assert result.startswith("Unable to read log file:")


@pytest.mark.parametrize(
    "content, expected",
    [
        (
            "Normal termination of Gaussian 16",
            "Optimization completed but formchk failed to generate .fchk",
        ),
        (
            "Number of steps exceeded, NStep= 100",
            "Geometry optimization did not converge (max steps exceeded)",
        ),
        ("Convergence failure -- run terminated.", "SCF convergence failure"),
        ("FormBX had a problem.", "Internal coordinate error (FormBX)"),
        (
            "NtrErr Called from FileIO.",
            "File I/O error (disk space or permission issue)",
        ),
        ("galloc:  could not allocate memory.", "Insufficient memory"),
        ("Link died unexpectedly", "Gaussian process crashed (link died or erroneous write)"),
        ("Erroneous write during file flush", "Gaussian process crashed (link died or erroneous write)"),
        (
            "Error termination via Lnk1e",
            "Gaussian terminated with error (check log for details)",
        ),
        ("some random text with no known marker", "Unknown error (no normal termination found)"),
    ],
)
def test_parse_gaussian_error_patterns(tmp_path: Path, content: str, expected: str):
    """逐一覆盖各类 Gaussian 报错模式的识别"""
    log_path = tmp_path / "job.log"
    log_path.write_text(content, encoding="utf-8")
    assert SmilesProcessing._parse_gaussian_error(log_path) == expected


# ==================== 测试跳过已预优化离子 ====================
def test_dpdisp_gaussian_tasks_skips_pre_optimized(
    smiles_processor: SmilesProcessing, tmp_path: Path, caplog
):
    """当 .gjf 已有对应 .json（已预优化）时，记录跳过日志且不提交任务"""
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    # 创建同名 .gjf + .json，使其被判定为已预优化
    folder_dir = sp.converted_dir / "charge_0"
    folder_dir.mkdir(parents=True, exist_ok=True)
    (folder_dir / "REF001.gjf").write_text("dummy", encoding="utf-8")
    (folder_dir / "REF001.json").write_text("{}", encoding="utf-8")

    machine_config = tmp_path / "machine.json"
    resources_config = tmp_path / "resources.json"
    machine_config.write_text(
        json.dumps({
            "context_type": "LocalContext",
            "local_root": "./",
            "remote_root": "/workplace/autodpgen/pytest",
            "batch_type": "Shell",
        }),
        encoding="utf-8",
    )
    resources_config.write_text(
        json.dumps({
            "number_node": 1,
            "cpu_per_node": 4,
            "gpu_per_node": 0,
            "group_size": 1,
        }),
        encoding="utf-8",
    )

    with patch("dpdispatcher.Submission.run_submission") as mock_run:
        sp.dpdisp_gaussian_tasks(
            folders=["charge_0"],
            machine_path=str(machine_config),
            resources_path=str(resources_config),
            nodes=1,
        )

    # 记录了「已预优化，跳过」的日志
    assert "already pre-optimized" in caplog.text
    assert "REF001" in caplog.text
    # 没有需要优化的 .gjf，因此提示无需优化且未提交任务
    assert "No .gjf files need Gaussian optimization in folder: charge_0" in caplog.text
    mock_run.assert_not_called()


def test_dpdisp_gaussian_tasks_json_without_matching_gjf(
    smiles_processor: SmilesProcessing, tmp_path: Path, caplog
):
    """有 .json 但无同名 .gjf 时，skipped 为空，不记录预优化跳过日志"""
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    # 仅有 .json，没有任何同名 .gjf
    folder_dir = sp.converted_dir / "charge_0"
    folder_dir.mkdir(parents=True, exist_ok=True)
    (folder_dir / "REF001.json").write_text("{}", encoding="utf-8")

    machine_config = tmp_path / "machine.json"
    resources_config = tmp_path / "resources.json"
    machine_config.write_text(
        json.dumps({
            "context_type": "LocalContext",
            "local_root": "./",
            "remote_root": "/workplace/autodpgen/pytest",
            "batch_type": "Shell",
        }),
        encoding="utf-8",
    )
    resources_config.write_text(
        json.dumps({
            "number_node": 1,
            "cpu_per_node": 4,
            "gpu_per_node": 0,
            "group_size": 1,
        }),
        encoding="utf-8",
    )

    with patch("dpdispatcher.Submission.run_submission") as mock_run:
        sp.dpdisp_gaussian_tasks(
            folders=["charge_0"],
            machine_path=str(machine_config),
            resources_path=str(resources_config),
            nodes=1,
        )

    assert "already pre-optimized" not in caplog.text
    assert "No .gjf files need Gaussian optimization in folder: charge_0" in caplog.text
    mock_run.assert_not_called()


# ==================== 测试作业全部成功（无失败分支）====================
@patch("dpdispatcher.Submission.run_submission")
@patch("dpdispatcher.Submission.__init__", return_value=None)
@patch("dpdispatcher.Task.__init__", return_value=None)
def test_dpdisp_gaussian_tasks_all_jobs_succeed(
    mock_task,
    mock_sub,
    mock_run,
    smiles_processor: SmilesProcessing,
    tmp_path: Path,
    caplog,
):
    """当每个 .gjf 都生成了非空 .fchk 时，视为成功，不记录失败告警"""
    sp = smiles_processor
    caplog.set_level(logging.INFO)

    folder_dir = sp.converted_dir / "charge_1"
    folder_dir.mkdir(parents=True, exist_ok=True)
    (folder_dir / "REF001.gjf").write_text("dummy", encoding="utf-8")

    machine_config = tmp_path / "machine.json"
    resources_config = tmp_path / "resources.json"
    machine_config.write_text(
        json.dumps({
            "context_type": "LocalContext",
            "local_root": "./",
            "remote_root": "/workplace/autodpgen/pytest",
            "batch_type": "Shell",
        }),
        encoding="utf-8",
    )
    resources_config.write_text(
        json.dumps({
            "number_node": 1,
            "cpu_per_node": 4,
            "gpu_per_node": 0,
            "group_size": 1,
        }),
        encoding="utf-8",
    )

    # LocalContext 下 parent="", 单节点任务目录为 converted_dir/pop0
    # 用 run_submission 的 side_effect 模拟 Gaussian 产出非空 .log/.fchk
    def fake_run(*args, **kwargs):
        task_dir = sp.converted_dir / "pop0"
        (task_dir / "REF001.log").write_text("Normal termination", encoding="utf-8")
        (task_dir / "REF001.fchk").write_text("fchk data", encoding="utf-8")

    mock_run.side_effect = fake_run

    sp.dpdisp_gaussian_tasks(
        folders=["charge_1"],
        machine_path=str(machine_config),
        resources_path=str(resources_config),
        nodes=1,
    )

    # 成功路径：优化目录生成了非空 .fchk，且没有失败告警
    opt_dir = sp.gaussian_optimized_dir / "charge_1"
    fchk = opt_dir / "REF001.fchk"
    assert fchk.exists() and fchk.stat().st_size > 0
    assert "Batch Gaussian optimization completed!!!" in caplog.text
    assert "jobs failed in folder" not in caplog.text
    mock_run.assert_called_once()



# ==================== 测试数据库复用（_build_database_index / reuse_from_database）====

def _make_database(root: Path, group: str, refcode: str, smiles: str, charge: int,
                   with_products: bool = True, id_col: str = "Refcode"):
    """在 root 下构造一个最小中央离子库：CSV + 3_For_CSP_module 产物"""
    csv_dir = root / "1_CSV_Database"
    prod_dir = root / "3_For_CSP_module" / group
    csv_dir.mkdir(parents=True, exist_ok=True)
    prod_dir.mkdir(parents=True, exist_ok=True)
    csv = csv_dir / f"{group}.csv"
    csv.write_text(f"SMILES,{id_col},Charge\n{smiles},{refcode},{charge}\n", encoding="utf-8")
    if with_products:
        (prod_dir / f"{refcode}.gjf").write_text("opt gjf", encoding="utf-8")
        (prod_dir / f"{refcode}.json").write_text("{}", encoding="utf-8")
    return root


def test_canonical_smiles_valid_and_invalid(smiles_processor: SmilesProcessing):
    """规范化：合法 SMILES 的不同等价写法归一，非法返回 None"""
    a = smiles_processor._canonical_smiles("[O-]C=O")
    b = smiles_processor._canonical_smiles("C(=O)[O-]")  # 等价的另一种写法
    assert a is not None and a == b
    assert smiles_processor._canonical_smiles("not_a_smiles") is None


def test_build_database_index_bad_csv_and_missing_columns(
    smiles_processor: SmilesProcessing, tmp_path: Path
):
    """索引健壮性：坏CSV、缺SMILES/Charge列、缺标识列、非法SMILES 均安全跳过"""
    db = tmp_path / "DB"
    csv_dir = db / "1_CSV_Database"
    (db / "3_For_CSP_module").mkdir(parents=True)
    csv_dir.mkdir(parents=True)
    # 1) 缺 SMILES/Charge 列
    (csv_dir / "charge_5.csv").write_text("Foo,Bar\n1,2\n", encoding="utf-8")
    # 2) 有 SMILES/Charge 但无 Refcode/Number 标识列
    (csv_dir / "charge_7.csv").write_text("SMILES,Charge\nCCO,7\n", encoding="utf-8")
    # 3) 合法列但 SMILES 非法（canon None → 跳过）
    (csv_dir / "charge_2.csv").write_text(
        "SMILES,Refcode,Charge\nnot_smiles,Z,2\n", encoding="utf-8"
    )
    index = smiles_processor._build_database_index(db)
    assert index == {}


def test_build_database_index_unreadable_csv(
    smiles_processor: SmilesProcessing, tmp_path: Path, caplog
):
    """CSV 读取抛异常时记录 warning 并跳过该文件"""
    db = tmp_path / "DB"
    csv_dir = db / "1_CSV_Database"
    (db / "3_For_CSP_module").mkdir(parents=True)
    csv_dir.mkdir(parents=True)
    (csv_dir / "charge_9.csv").write_text("SMILES,Refcode,Charge\nCCO,X,9\n", encoding="utf-8")
    with patch("pandas.read_csv", side_effect=ValueError("boom")):
        index = smiles_processor._build_database_index(db)
    assert index == {}
    assert "cannot read" in caplog.text


def test_build_database_index_success(smiles_processor: SmilesProcessing, tmp_path: Path):
    """正常构建索引：产物齐全的离子被纳入，键为(canonical_smiles, charge)"""
    db = _make_database(tmp_path / "DB", "charge_-1", "REF004", "[O-]C=O", -1)
    index = smiles_processor._build_database_index(db)
    canon = smiles_processor._canonical_smiles("[O-]C=O")
    assert (canon, -1) in index
    gjf, jsn = index[(canon, -1)]
    assert gjf.name == "REF004.gjf" and jsn.name == "REF004.json"


def test_build_database_index_no_csv_dir(smiles_processor: SmilesProcessing, tmp_path: Path):
    """数据库无 CSV 目录：返回空索引并记录"""
    index = smiles_processor._build_database_index(tmp_path / "missing_db")
    assert index == {}


def test_build_database_index_missing_products(
    smiles_processor: SmilesProcessing, tmp_path: Path
):
    """产物缺失(仅CSV无gjf/json)：不纳入索引"""
    db = _make_database(tmp_path / "DB", "charge_-1", "REF004", "[O-]C=O", -1,
                        with_products=False)
    index = smiles_processor._build_database_index(db)
    assert index == {}


def test_build_database_index_number_id_column(
    smiles_processor: SmilesProcessing, tmp_path: Path
):
    """标识列为 Number 时也能定位产物"""
    db = _make_database(tmp_path / "DB", "charge_3", "137", "[NH3+]C", 3, id_col="Number")
    index = smiles_processor._build_database_index(db)
    canon = smiles_processor._canonical_smiles("[NH3+]C")
    assert (canon, 3) in index


def test_reuse_from_database_empty_dir(smiles_processor: SmilesProcessing):
    """database_dir 为空：直接返回，不做任何事(向后兼容)"""
    reused, need = smiles_processor.reuse_from_database("")
    assert reused == [] and need == []


def test_reuse_from_database_hit_and_miss(
    smiles_processor: SmilesProcessing, tmp_path: Path
):
    """命中(SMILES+电荷符)复制gjf+json；未命中记入 need_calc"""
    # fixture 的 df 含 [O-]C=O(charge -1, REF004)。库里放它 → 应命中
    db = _make_database(tmp_path / "DB", "charge_-1", "DBREF", "[O-]C=O", -1)
    reused, need = smiles_processor.reuse_from_database(str(db))
    assert "REF004" in reused
    # 复用文件已落到 converted_dir/charge_-1
    dst = smiles_processor.converted_dir / "charge_-1"
    assert (dst / "REF004.gjf").exists() and (dst / "REF004.json").exists()
    # 其它离子未命中
    assert "REF001" in need


def test_reuse_from_database_charge_mismatch(
    smiles_processor: SmilesProcessing, tmp_path: Path
):
    """SMILES 相同但电荷不符：不复用"""
    # 库里把 [O-]C=O 标为 charge -2（与 df 里的 -1 不符）
    db = _make_database(tmp_path / "DB", "charge_-2", "DBREF", "[O-]C=O", -2)
    reused, need = smiles_processor.reuse_from_database(str(db))
    assert "REF004" not in reused
    assert "REF004" in need


def test_reuse_from_database_empty_index(
    smiles_processor: SmilesProcessing, tmp_path: Path
):
    """DB 存在但无可用条目：返回空，不复制"""
    (tmp_path / "DB" / "1_CSV_Database").mkdir(parents=True)
    reused, need = smiles_processor.reuse_from_database(str(tmp_path / "DB"))
    assert reused == [] and need == []

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=ion_CSP.convert_SMILES"])