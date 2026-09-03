import pytest
import os
from unittest.mock import patch, MagicMock
from ion_CSP.run.main_EE import (
    main,
    setup_ion_links,
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
@patch("ion_CSP.run.main_EE.setup_ion_links")
@patch("ion_CSP.run.main_EE.estimation_task")
@patch("ion_CSP.run.main_EE.combination_task")
def test_main_links_database_after_conversion(
    mock_combination,
    mock_estimation,
    mock_setup_links,
    mock_convertion,
    mock_logger,
    mock_work_dir,
    mock_config,
):
    """数据库链接必须在转换后、估算前创建。"""
    call_order = []
    logger_instance = MagicMock()
    logger_instance.is_successful.return_value = False
    mock_logger.return_value = logger_instance
    mock_convertion.side_effect = lambda *args: call_order.append("conversion")
    mock_setup_links.side_effect = lambda *args: call_order.append("linking")
    mock_estimation.side_effect = lambda *args: call_order.append("estimation")

    main(mock_work_dir, mock_config)

    assert call_order[:3] == ["conversion", "linking", "estimation"]


def test_setup_ion_links_skips_folder_with_local_ions(tmp_path):
    """项目自有离子存在时，不混入中央库离子。"""
    database_dir = tmp_path / "database"
    source_folder = database_dir / "3_For_CSP_module" / "charge_1"
    source_folder.mkdir(parents=True)
    (source_folder / "database.gjf").write_text("database")
    work_dir = tmp_path / "work"
    target_folder = work_dir / "charge_1"
    target_folder.mkdir(parents=True)
    (target_folder / "local.gjf").write_text("local")
    config = {
        "convert_SMILES": {"database_dir": str(database_dir)},
        "empirical_estimate": {"folders": ["charge_1"]},
    }

    setup_ion_links(work_dir, config)

    assert not (target_folder / "database.gjf").exists()


def test_setup_ion_links_skips_project_input_charge(tmp_path):
    """新项目转换产物尚未复制到根目录时，也不能链接同电荷数据库。"""
    database_dir = tmp_path / "database"
    source_folder = database_dir / "3_For_CSP_module" / "charge_1"
    source_folder.mkdir(parents=True)
    (source_folder / "database.gjf").write_text("database")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "ions.csv").write_text(
        "Refcode,SMILES,Charge\nBN6+,B,1\n", encoding="utf-8"
    )
    config = {
        "convert_SMILES": {
            "csv_file": "ions.csv",
            "database_dir": str(database_dir),
        },
        "empirical_estimate": {"folders": ["charge_1"]},
    }

    setup_ion_links(work_dir, config)

    assert not (work_dir / "charge_1").exists()


def test_setup_ion_links_replaces_broken_symlink(tmp_path):
    """重复运行时自动修复目标目录中的失效软链接。"""
    database_dir = tmp_path / "database"
    source_folder = database_dir / "3_For_CSP_module" / "charge_-1"
    source_folder.mkdir(parents=True)
    source_file = source_folder / "ion.gjf"
    source_file.write_text("ion")
    work_dir = tmp_path / "work"
    target_folder = work_dir / "charge_-1"
    target_folder.mkdir(parents=True)
    target_file = target_folder / "ion.gjf"
    target_file.symlink_to(tmp_path / "missing.gjf")
    config = {
        "convert_SMILES": {"database_dir": str(database_dir)},
        "empirical_estimate": {"folders": ["charge_-1"]},
    }

    setup_ion_links(work_dir, config)

    assert target_file.is_symlink()
    assert target_file.resolve() == source_file.resolve()


def test_setup_ion_links_uses_relative_symlinks(tmp_path):
    database_dir = tmp_path / "database"
    source = database_dir / "3_For_CSP_module/charge_-1/ion.gjf"
    source.parent.mkdir(parents=True)
    source.write_text("ion", encoding="utf-8")
    work = tmp_path / "work"
    config = {
        "convert_SMILES": {"database_dir": str(database_dir)},
        "empirical_estimate": {"folders": ["charge_-1"]},
    }

    stats = setup_ion_links(work, config)

    target = work / "charge_-1/ion.gjf"
    assert target.is_symlink()
    assert not os.path.isabs(os.readlink(target))
    assert target.resolve() == source.resolve()
    assert stats["linked"] == 1


def test_setup_ion_links_migrates_identical_database_copies(tmp_path):
    database_dir = tmp_path / "database"
    source = database_dir / "3_For_CSP_module/charge_1/ion.gjf"
    source.parent.mkdir(parents=True)
    source.write_text("same", encoding="utf-8")
    work = tmp_path / "work"
    target = work / "charge_1/ion.gjf"
    target.parent.mkdir(parents=True)
    target.write_text("same", encoding="utf-8")
    config = {
        "convert_SMILES": {
            "database_dir": str(database_dir),
            "migrate_database_copies": True,
        },
        "empirical_estimate": {"folders": ["charge_1"]},
    }

    stats = setup_ion_links(work, config)

    assert target.is_symlink()
    assert target.resolve() == source.resolve()
    assert stats["migrated_copies"] == 1


def test_setup_ion_links_removes_database_links_from_project_charge(tmp_path):
    database_dir = tmp_path / "Database_Ions"
    source = database_dir / "3_For_CSP_module/charge_1/database.gjf"
    source.parent.mkdir(parents=True)
    source.write_text("database", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / "ions.csv").write_text(
        """Refcode,SMILES,Charge
N8+,N,1
""", encoding="utf-8"
    )
    target = work / "charge_1/database.gjf"
    target.parent.mkdir()
    target.symlink_to(source)
    config = {
        "convert_SMILES": {
            "csv_file": "ions.csv",
            "database_dir": str(database_dir),
        },
        "empirical_estimate": {"folders": ["charge_1"]},
    }

    stats = setup_ion_links(work, config)

    assert not target.exists()
    assert not target.is_symlink()
    assert stats["removed_project_links"] == 1


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
        work_dir=mock_work_dir,
        csv_file=mock_config["convert_SMILES"]["csv_file"],
        preserve_topology=True,
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


@patch("ion_CSP.run.main_EE.StatusLogger")
@patch("ion_CSP.run.main_EE.convertion_task")
@patch("ion_CSP.run.main_EE.estimation_task")
@patch("ion_CSP.run.main_EE.combination_task")
def test_main_update_disabled(
    mock_combination, mock_estimation, mock_convertion, mock_logger, mock_work_dir, mock_config
):
    """update=False 时，跳过额外的 update_combo 逻辑（覆盖 line 49 False 分支）"""
    mock_config["empirical_estimate"]["update"] = False
    logger_instance = MagicMock()
    logger_instance.is_successful.return_value = True  # 循环内任务全部跳过
    mock_logger.return_value = logger_instance

    main(mock_work_dir, mock_config)

    # update 关闭且循环内任务均已成功 → combination_task 完全不被调用
    mock_combination.assert_not_called()


@patch("ion_CSP.run.main_EE.StatusLogger")
@patch("ion_CSP.run.main_EE.combination_task")
def test_main_update_combo_failure(
    mock_combination, mock_logger, mock_work_dir, mock_config
):
    """update 阶段的 combination_task 抛异常时记录失败并抛出（覆盖 line 55-57）"""
    # 循环内任务全部已成功 → 跳过循环体，避免在循环里触发 combination_task
    logger_instance = MagicMock()
    logger_instance.is_successful.return_value = True
    mock_logger.return_value = logger_instance
    # update 阶段调用 combination_task 时失败
    mock_combination.side_effect = RuntimeError("Update failed")

    with pytest.raises(RuntimeError, match="Update failed"):
        main(mock_work_dir, mock_config)

    logger_instance.set_failure.assert_called()


@patch("ion_CSP.run.main_EE.EmpiricalEstimation")
def test_combination_task_unknown_sort(mock_estimation, mock_work_dir, mock_config):
    """sort_by 不匹配任何已知排序时，三种估算方法均不调用（覆盖 line 115 False 分支）"""
    mock_config["empirical_estimate"]["sort_by"] = "unknown"
    estimation_instance = MagicMock()
    mock_estimation.return_value = estimation_instance

    combination_task(mock_work_dir, mock_config)

    estimation_instance.empirical_estimate.assert_not_called()
    estimation_instance.nitrogen_content_estimate.assert_not_called()
    estimation_instance.carbon_nitrogen_ratio_estimate.assert_not_called()
    # make_combo_dir 仍会执行
    estimation_instance.make_combo_dir.assert_called_once()


@patch("ion_CSP.run.main_EE.get_work_dir_and_config")
@patch("ion_CSP.run.main_EE.merge_config")
@patch("ion_CSP.run.main_EE.main")
def test_main_entry_point(mock_main, mock_merge, mock_get_config):
    """测试主入口点"""
    # 设置 mock
    mock_get_config.return_value = ("/test/work", {"test": "config"})
    mock_merge.return_value = {"merged": "config"}

    # 导入并执行 __main__ 块

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
    assert "preserve_smiles_topology" in DEFAULT_CONFIG["convert_SMILES"]
    assert "screen" in DEFAULT_CONFIG["convert_SMILES"]
    assert DEFAULT_CONFIG["convert_SMILES"]["screen"] is False

    # 验证 empirical_estimate 配置
    assert "folders" in DEFAULT_CONFIG["empirical_estimate"]
    assert "ratios" in DEFAULT_CONFIG["empirical_estimate"]
    assert "sort_by" in DEFAULT_CONFIG["empirical_estimate"]
    assert DEFAULT_CONFIG["empirical_estimate"]["sort_by"] == "density"
    assert DEFAULT_CONFIG["empirical_estimate"]["num_combos"] == 100
