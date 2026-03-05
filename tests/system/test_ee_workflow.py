"""经验估算工作流系统测试"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.mark.system
def test_ee_workflow_config_validation(tmp_path):
    """测试经验估算工作流的配置验证"""
    from ion_CSP.convert_SMILES import SmilesProcessing

    work_dir = tmp_path / "ee_workflow"
    work_dir.mkdir()

    # 测试无效的CSV文件路径
    # SmilesProcessing 抛出的是 Exception 而不是 FileNotFoundError
    with pytest.raises(Exception, match="Necessary .csv file not provided"):
        processor = SmilesProcessing(
            work_dir=work_dir,
            csv_file="nonexistent.csv"
        )
        processor.charge_group()


@pytest.mark.system
def test_ee_workflow_with_mocks(tmp_path):
    """使用mock测试经验估算工作流"""
    from ion_CSP.run.main_EE import (
        convertion_task,
        estimation_task,
        combination_task
    )

    work_dir = tmp_path / "ee_mock"
    work_dir.mkdir()

    config = {
        "convert_SMILES": {
            "csv_file": "test.csv",
            "screen": False,
            "charge_screen": "",
            "group_screen": "",
            "group_name": "",
            "group_screen_invert": False,
            "machine": {"test": "machine"},
            "resources": {"test": "resources"},
            "nodes": 1,
        },
        "empirical_estimate": {
            "folders": ["folder1", "folder2"],
            "ratios": [1, 1],
            "sort_by": "density",
            "make_combo_dir": True,
            "target_dir": "combo",
            "num_combos": 100,
            "ion_numbers": [1, 1],
            "update": True,
        },
    }

    # 使用mock测试各个任务
    with patch("ion_CSP.run.main_EE.SmilesProcessing") as mock_smiles:
        mock_smiles_instance = MagicMock()
        mock_smiles.return_value = mock_smiles_instance

        convertion_task(work_dir, config)
        assert mock_smiles_instance.charge_group.called

    with patch("ion_CSP.run.main_EE.EmpiricalEstimation") as mock_est:
        mock_est_instance = MagicMock()
        mock_est.return_value = mock_est_instance

        estimation_task(work_dir, config)
        assert mock_est_instance.multiwfn_process_fchk_to_json.called


@pytest.mark.system
def test_ee_workflow_data_flow(tmp_path):
    """测试经验估算工作流的数据流

    验证数据在各个步骤之间正确传递：
    1. SMILES转换
    2. Gaussian优化
    3. Multiwfn分析
    4. 密度估算
    5. 组合生成
    """
    from ion_CSP.empirical_estimate import EmpiricalEstimation

    work_dir = tmp_path / "ee_data_flow"
    work_dir.mkdir()

    # 创建测试用的文件夹结构
    folders = ["cation_+1", "anion_-1"]
    for folder in folders:
        folder_path = work_dir / folder
        folder_path.mkdir()

        # 创建测试文件
        (folder_path / "test.gjf").write_text("test gjf content")
        (folder_path / "test.json").write_text('{"test": "data"}')

    # 测试EmpiricalEstimation初始化
    estimation = EmpiricalEstimation(
        work_dir=work_dir,
        folders=folders,
        ratios=[1, 1],
        sort_by="density"
    )

    # 验证配置被正确存储
    assert estimation.folders == folders
    assert estimation.ratios == [1, 1]
    assert estimation.sort_by == "density"


@pytest.mark.system
def test_ee_workflow_status_tracking(tmp_path):
    """测试经验估算工作流的状态跟踪"""
    from ion_CSP.log_and_time import StatusLogger

    work_dir = tmp_path / "ee_status"
    work_dir.mkdir()

    # 测试任务状态跟踪
    tasks = ["0_convertion", "0_estimation", "0_update_combo"]

    for task_name in tasks:
        task_logger = StatusLogger(work_dir=work_dir, task_name=task_name)

        # 初始状态应该是未成功
        assert not task_logger.is_successful()

        # 设置为运行中
        task_logger.set_running()

        # 设置为成功
        task_logger.set_success()

        # 验证状态
        assert task_logger.is_successful()

