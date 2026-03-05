import pytest
import logging
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from ion_CSP.run.main_EE import (
    main,
    convertion_task,
    estimation_task,
    combination_task,
    DEFAULT_CONFIG,
)


@pytest.fixture
def mock_work_dir(tmp_path):
    """创建临时工作目录"""
    work_dir = tmp_path / "test_work"
    work_dir.mkdir()
    return work_dir  # 返回 Path 对象而不是字符串



@pytest.fixture
def mock_config():
    """创建测试配置"""
    return {
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


@patch("ion_CSP.run.main_EE.StatusLogger")
@patch("ion_CSP.run.main_EE.convertion_task")
@patch("ion_CSP.run.main_EE.estimation_task")
@patch("ion_CSP.run.main_EE.combination_task")
def test_main_all_tasks_successful(
    mock_combination, mock_estimation, mock_convertion, mock_logger, mock_work_dir, mock_config
):
    """测试所有任务成功执行"""
    # 设置 StatusLogger mock
    logger_instance = MagicMock()
    logger_instance.is_successful.return_value = False
    mock_logger.return_value = logger_instance

    # 执行主函数
    main(mock_work_dir, mock_config)

    # 验证所有任务都被调用
    assert mock_convertion.called
    assert mock_estimation.called
    # combination_task 应该被调用两次（一次在循环中，一次在update逻辑中）
    assert mock_combination.call_count == 2

    # 验证 StatusLogger 被正确调用
    assert mock_logger.call_count >= 3  # 至少3个任务


@patch("ion_CSP.run.main_EE.StatusLogger")
@patch("ion_CSP.run.main_EE.convertion_task")
@patch("ion_CSP.run.main_EE.combination_task")
def test_main_task_already_successful(mock_combination, mock_convertion, mock_logger, mock_work_dir, mock_config):
    """测试任务已经成功完成的情况"""
    # 设置 StatusLogger mock - 任务已经成功
    logger_instance = MagicMock()
    logger_instance.is_successful.return_value = True
    mock_logger.return_value = logger_instance

    # 执行主函数
    main(mock_work_dir, mock_config)

    # 验证任务不会被重新执行
    assert not mock_convertion.called
    # 但是 combination_task 会在 update 逻辑中被调用
    assert mock_combination.called


@patch("ion_CSP.run.main_EE.StatusLogger")
@patch("ion_CSP.run.main_EE.convertion_task")
def test_main_task_failure(mock_convertion, mock_logger, mock_work_dir, mock_config):
    """测试任务失败的情况"""
    # 设置 StatusLogger mock
    logger_instance = MagicMock()
    logger_instance.is_successful.return_value = False
    mock_logger.return_value = logger_instance

    # 设置任务抛出异常
    mock_convertion.side_effect = RuntimeError("Task failed")

    # 验证异常被正确抛出
    with pytest.raises(RuntimeError, match="Task failed"):
        main(mock_work_dir, mock_config)

    # 验证 set_failure 被调用
    logger_instance.set_failure.assert_called()


@patch("ion_CSP.run.main_EE.SmilesProcessing")
def test_convertion_task_without_screen(mock_smiles, mock_work_dir, mock_config):
    """测试转换任务（不进行筛选）"""
    # 设置 mock
    smiles_instance = MagicMock()
    mock_smiles.return_value = smiles_instance

    # 执行转换任务
    convertion_task(mock_work_dir, mock_config)

    # 验证 SmilesProcessing 被正确初始化
    mock_smiles.assert_called_once_with(
        work_dir=mock_work_dir, csv_file=mock_config["convert_SMILES"]["csv_file"]
    )

    # 验证 charge_group 被调用
    smiles_instance.charge_group.assert_called_once()

    # 验证 screen 不被调用（因为 screen=False）
    smiles_instance.screen.assert_not_called()

    # 验证 dpdisp_gaussian_tasks 被调用
    smiles_instance.dpdisp_gaussian_tasks.assert_called_once()


@patch("ion_CSP.run.main_EE.SmilesProcessing")
def test_convertion_task_with_screen(mock_smiles, mock_work_dir, mock_config):
    """测试转换任务（进行筛选）"""
    # 修改配置启用筛选
    mock_config["convert_SMILES"]["screen"] = True
    mock_config["convert_SMILES"]["charge_screen"] = "+1"
    mock_config["convert_SMILES"]["group_screen"] = "[N+](=O)[O-]"
    mock_config["convert_SMILES"]["group_name"] = "nitro"
    mock_config["convert_SMILES"]["group_screen_invert"] = True

    # 设置 mock
    smiles_instance = MagicMock()
    mock_smiles.return_value = smiles_instance

    # 执行转换任务
    convertion_task(mock_work_dir, mock_config)

    # 验证 screen 被调用
    smiles_instance.screen.assert_called_once_with(
        charge_screen="+1",
        group_screen="[N+](=O)[O-]",
        group_name="nitro",
        group_screen_invert=True,
    )


@patch("ion_CSP.run.main_EE.EmpiricalEstimation")
def test_estimation_task(mock_estimation, mock_work_dir, mock_config):
    """测试估算任务"""
    # 设置 mock
    estimation_instance = MagicMock()
    mock_estimation.return_value = estimation_instance

    # 执行估算任务
    estimation_task(mock_work_dir, mock_config)

    # 验证 EmpiricalEstimation 被正确初始化
    mock_estimation.assert_called_once_with(
        work_dir=mock_work_dir,
        folders=mock_config["empirical_estimate"]["folders"],
        ratios=mock_config["empirical_estimate"]["ratios"],
        sort_by=mock_config["empirical_estimate"]["sort_by"],
    )

    # 验证方法被调用
    estimation_instance.multiwfn_process_fchk_to_json.assert_called_once()
    estimation_instance.gaussian_log_to_optimized_gjf.assert_called_once()


@patch("ion_CSP.run.main_EE.EmpiricalEstimation")
def test_combination_task_density_sort(mock_estimation, mock_work_dir, mock_config):
    """测试组合任务（按密度排序）"""
    # 设置 mock
    estimation_instance = MagicMock()
    mock_estimation.return_value = estimation_instance

    # 执行组合任务
    combination_task(mock_work_dir, mock_config)

    # 验证 empirical_estimate 被调用（因为 sort_by="density"）
    estimation_instance.empirical_estimate.assert_called_once()

    # 验证 make_combo_dir 被调用
    estimation_instance.make_combo_dir.assert_called_once_with(
        target_dir=mock_config["empirical_estimate"]["target_dir"],
        num_combos=mock_config["empirical_estimate"]["num_combos"],
        ion_numbers=mock_config["empirical_estimate"]["ion_numbers"],
    )


@patch("ion_CSP.run.main_EE.EmpiricalEstimation")
def test_combination_task_nitrogen_sort(mock_estimation, mock_work_dir, mock_config):
    """测试组合任务（按氮含量排序）"""
    # 修改配置为按氮含量排序
    mock_config["empirical_estimate"]["sort_by"] = "nitrogen"

    # 设置 mock
    estimation_instance = MagicMock()
    mock_estimation.return_value = estimation_instance

    # 执行组合任务
    combination_task(mock_work_dir, mock_config)

    # 验证 nitrogen_content_estimate 被调用
    estimation_instance.nitrogen_content_estimate.assert_called_once()

    # 验证 empirical_estimate 不被调用
    estimation_instance.empirical_estimate.assert_not_called()


@patch("ion_CSP.run.main_EE.EmpiricalEstimation")
def test_combination_task_nc_ratio_sort(mock_estimation, mock_work_dir, mock_config):
    """测试组合任务（按碳氮比排序）"""
    # 修改配置为按碳氮比排序
    mock_config["empirical_estimate"]["sort_by"] = "NC_ratio"

    # 设置 mock
    estimation_instance = MagicMock()
    mock_estimation.return_value = estimation_instance

    # 执行组合任务
    combination_task(mock_work_dir, mock_config)

    # 验证 carbon_nitrogen_ratio_estimate 被调用
    estimation_instance.carbon_nitrogen_ratio_estimate.assert_called_once()


@patch("ion_CSP.run.main_EE.EmpiricalEstimation")
def test_combination_task_no_combo_dir(mock_estimation, mock_work_dir, mock_config):
    """测试组合任务（不创建组合目录）"""
    # 修改配置不创建组合目录
    mock_config["empirical_estimate"]["make_combo_dir"] = False

    # 设置 mock
    estimation_instance = MagicMock()
    mock_estimation.return_value = estimation_instance

    # 执行组合任务
    combination_task(mock_work_dir, mock_config)

    # 验证 make_combo_dir 不被调用
    estimation_instance.make_combo_dir.assert_not_called()


@patch("ion_CSP.run.main_EE.get_work_dir_and_config")
@patch("ion_CSP.run.main_EE.merge_config")
@patch("ion_CSP.run.main_EE.main")
def test_main_entry_point(mock_main, mock_merge, mock_get_config):
    """测试主入口点"""
    # 设置 mock
    mock_get_config.return_value = ("/test/work", {"test": "config"})
    mock_merge.return_value = {"merged": "config"}

    # 导入并执行 __main__ 块
    import ion_CSP.run.main_EE as main_ee_module

    # 模拟 __main__ 执行
    work_dir, config = mock_get_config()
    modules = ["convert_SMILES", "empirical_estimate"]
    for module in modules:
        config[module] = mock_merge(
            default_config=DEFAULT_CONFIG, user_config=config, key=module
        )
    mock_main(work_dir, config)

    # 验证函数被调用
    mock_get_config.assert_called_once()
    assert mock_merge.call_count == 2
    mock_main.assert_called_once()


def test_default_config_structure():
    """测试默认配置结构"""
    assert "convert_SMILES" in DEFAULT_CONFIG
    assert "empirical_estimate" in DEFAULT_CONFIG

    # 验证 convert_SMILES 配置
    assert "csv_file" in DEFAULT_CONFIG["convert_SMILES"]
    assert "screen" in DEFAULT_CONFIG["convert_SMILES"]
    assert DEFAULT_CONFIG["convert_SMILES"]["screen"] is False

    # 验证 empirical_estimate 配置
    assert "folders" in DEFAULT_CONFIG["empirical_estimate"]
    assert "ratios" in DEFAULT_CONFIG["empirical_estimate"]
    assert "sort_by" in DEFAULT_CONFIG["empirical_estimate"]
    assert DEFAULT_CONFIG["empirical_estimate"]["sort_by"] == "density"
    assert DEFAULT_CONFIG["empirical_estimate"]["num_combos"] == 100
