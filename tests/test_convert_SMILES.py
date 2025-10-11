import pytest
import logging
from unittest.mock import patch
from ion_CSP.convert_SMILES import SmilesProcessing


# Fixture 创建临时工作目录和测试数据
@pytest.fixture
def setup_workdir(tmp_path):
    # 创建测试 CSV 文件（修正负电荷格式）
    csv_data = """SMILES,Charge,Refcode,Number
CCO,0,REF001,1
C[N+](C)(C)C,1,REF002,2
C1=CC=NC=C1,0,REF003,3
[O-]C=O,-1,REF004,4
invalid_smiles,0,REF005,5"""

    csv_path = tmp_path / "test.csv"
    csv_path.write_text(csv_data)

    # 创建模拟的 param 资源目录
    param_dir = tmp_path / "param"
    param_dir.mkdir()
    (param_dir / "g16_sub.sh").write_text("echo 'Mock script'")

    # 模拟 importlib.resources.files
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value = param_dir

    # 模拟重定向日志函数
    with patch("ion_CSP.convert_SMILES.redirect_dpdisp_logging"):
        yield tmp_path

# 测试初始化
def test_initialization(setup_workdir):
    tmp_path = setup_workdir
    work_dir = tmp_path

    sp = SmilesProcessing(work_dir, "test.csv")

    # 验证基础属性
    assert sp.base_dir == work_dir
    assert sp.base_name == "Refcode"

    # 验证数据处理（包括无效 SMILES）
    assert len(sp.df) == 5
    assert list(sp.df["Refcode"]) == ["REF001", "REF002", "REF003", "REF004", "REF005"]
    assert len(sp.grouped) == 3  # 三个电荷组: -1, 0, 1

# 测试 SMILES 转换
def test_smiles_conversion(setup_workdir, caplog):
    tmp_path = setup_workdir
    work_dir = tmp_path
    caplog.set_level(logging.INFO)

    sp = SmilesProcessing(work_dir, "test.csv")
    sp.charge_group()

    # 验证输出目录结构
    output_dir = tmp_path / "1_1_SMILES_gjf/test"
    assert output_dir.exists()

    # 验证电荷分组目录
    charge_dirs = [d for d in output_dir.iterdir() if d.is_dir()]
    assert len(charge_dirs) == 3
    assert set(d.name for d in charge_dirs) == {"charge_-1", "charge_0", "charge_1"}

    # 验证文件生成
    charge0_dir = output_dir / "charge_0"
    assert (charge0_dir / "REF001.gjf").exists()
    assert (charge0_dir / "REF003.gjf").exists()

    charge1_dir = output_dir / "charge_1"
    assert (charge1_dir / "REF002.gjf").exists()

    chargem1_dir = output_dir / "charge_-1"
    assert (chargem1_dir / "REF004.gjf").exists()

    # 验证文件内容
    with open(charge0_dir / "REF001.gjf") as f:
        content = f.read()
        assert "%nprocshared=8" in content
        assert "REF001" in content
        assert "0 1" in content  # 电荷+自旋多重度
        assert "C " in content and "O " in content  # 原子坐标

    # 验证无效 SMILES 的错误日志
    assert "REF005" in caplog.text
    assert "Invalid SMILES:" in caplog.text

# 测试筛选功能
def test_screening(setup_workdir, caplog):
    tmp_path = setup_workdir
    work_dir = tmp_path
    caplog.set_level(logging.INFO)

    sp = SmilesProcessing(work_dir, "test.csv")

    # 筛选带正电荷的阳离子
    sp.screen(
        charge_screen=1,
        group_screen="[N+]",
        group_name="quaternary_ammonium",
        group_screen_invert=False,
    )

    # 验证筛选目录
    screen_dir = tmp_path / "1_1_SMILES_gjf/test/quaternary_ammonium_1"
    assert screen_dir.exists()

    # 验证只生成目标文件
    files = list(screen_dir.glob("*.gjf"))
    assert len(files) == 1
    assert files[0].name == "REF002.gjf"

    # 验证日志输出
    assert (
        "Number of ions with charge of [1] and quaternary_ammonium group: 1"
        in caplog.text
    )

# 测试任务分发 (模拟dpdispatcher)
@patch("dpdispatcher.Submission.run_submission")
@patch("dpdispatcher.Submission.__init__", return_value=None)
@patch("dpdispatcher.Task.__init__", return_value=None)
def test_dpdisp_gaussian_tasks(mock_run, mock_sub, mock_task, setup_workdir, caplog):
    tmp_path = setup_workdir
    work_dir = tmp_path
    caplog.set_level(logging.INFO)

    # 准备测试数据
    sp = SmilesProcessing(work_dir, "test.csv")
    gjf_dir = tmp_path / "1_1_SMILES_gjf/test/charge_1"
    gjf_dir.mkdir(parents=True)
    (gjf_dir / "REF001.gjf").write_text("dummy content")
    (gjf_dir / "REF003.gjf").write_text("dummy content")

    # 创建更完整的机器配置
    machine_config = str(tmp_path / "machine.json")
    resources_config = str(tmp_path / "resources.json")

    # 添加必需的 batch_type 字段
    with open(machine_config, "w") as f:
        f.write("""{
            "context_type": "LocalContext",
            "local_root": "./",
            "remote_root": "/workplace/autodpgen/pytest",
            "batch_type": "Shell"
        }""")

    with open(resources_config, "w") as f:
        f.write("""{
            "number_node": 1,
            "cpu_per_node": 4,
            "gpu_per_node": 0,
            "queue_name": "normal",
            "group_size": 1
        }""")

    # 调用任务分发
    with patch("shutil.copyfile"), patch("shutil.rmtree"):
        sp.dpdisp_gaussian_tasks(
            folders=["charge_1"],
            machine=machine_config,
            resources=resources_config,
            nodes=2,
        )

    # 验证日志输出
    assert "Batch Gaussian optimization completed!!!" in caplog.text

    # 验证优化目录创建
    opt_dir = tmp_path / "1_2_Gaussian_optimized/charge_1"
    assert opt_dir.exists()

# 测试错误处理
def test_error_handling(setup_workdir, caplog):
    tmp_path = setup_workdir
    work_dir = tmp_path
    caplog.set_level(logging.INFO)

    # 无效 CSV 文件测试
    with pytest.raises(Exception, match="Necessary .csv file not provided!"):
        SmilesProcessing(work_dir, "")

    # 无效 SMILES 测试
    sp = SmilesProcessing(work_dir, "test.csv")
    sp.charge_group()

    # 验证错误日志
    assert "REF005" in caplog.text
    assert "Invalid SMILES:" in caplog.text
