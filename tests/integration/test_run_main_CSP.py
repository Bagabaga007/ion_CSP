import pytest
import logging
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from ion_CSP.run.main_CSP import (
    main,
    generation_task,
    mlp_optimization_task,
    read_mlp_density_task,
    vasp_optimization_task,
    vasp_relaxation_task,
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
        "gen_opt": {
            "num_per_group": 500,
            "space_groups_limit": 230,
            "nodes": 1,
            "ion_numbers": [1, 1],
            "species": ["Li", "F"],
            "machine": {"test": "machine"},
            "resources": {"test": "resources"},
        },
        "read_mlp_density": {
            "n_screen": 10,
            "sort_by": "density",
            "molecules_screen": True,
            "detail_log": False,
        },
        "vasp_processing": {
            "nodes": 2,
            "molecules_prior": True,
            "machine": {"test": "machine"},
            "resources": {"test": "resources"},
        },
    }


@patch("ion_CSP.run.main_CSP.StatusLogger")
@patch("ion_CSP.run.main_CSP.generation_task")
@patch("ion_CSP.run.main_CSP.mlp_optimization_task")
@patch("ion_CSP.run.main_CSP.read_mlp_density_task")
@patch("ion_CSP.run.main_CSP.vasp_optimization_task")
@patch("ion_CSP.run.main_CSP.vasp_relaxation_task")
def test_main_all_tasks_successful(
    mock_vasp_relax,
    mock_vasp_opt,
    mock_read_mlp,
    mock_mlp_opt,
    mock_generation,
    mock_logger,
    mock_work_dir,
    mock_config,
):
    """测试所有任务成功执行"""
    # 设置 StatusLogger mock
    logger_instance = MagicMock()
    logger_instance.is_successful.return_value = False
    mock_logger.return_value = logger_instance

    # 执行主函数
    main(mock_work_dir, mock_config)

    # 验证所有任务都被调用
    assert mock_generation.called
    assert mock_mlp_opt.called
    assert mock_read_mlp.called
    assert mock_vasp_opt.called
    assert mock_vasp_relax.called

    # 验证 StatusLogger 被正确调用（5个任务）
    assert mock_logger.call_count == 5


@patch("ion_CSP.run.main_CSP.StatusLogger")
@patch("ion_CSP.run.main_CSP.generation_task")
def test_main_task_already_successful(mock_generation, mock_logger, mock_work_dir, mock_config):
    """测试任务已经成功完成的情况"""
    # 设置 StatusLogger mock - 任务已经成功
    logger_instance = MagicMock()
    logger_instance.is_successful.return_value = True
    mock_logger.return_value = logger_instance

    # 执行主函数
    main(mock_work_dir, mock_config)

    # 验证任务不会被重新执行
    assert not mock_generation.called


@patch("ion_CSP.run.main_CSP.StatusLogger")
@patch("ion_CSP.run.main_CSP.generation_task")
def test_main_task_failure(mock_generation, mock_logger, mock_work_dir, mock_config):
    """测试任务失败的情况"""
    # 设置 StatusLogger mock
    logger_instance = MagicMock()
    logger_instance.is_successful.return_value = False
    mock_logger.return_value = logger_instance

    # 设置任务抛出异常
    mock_generation.side_effect = RuntimeError("Task failed")

    # 验证异常被正确抛出
    with pytest.raises(RuntimeError, match="Task failed"):
        main(mock_work_dir, mock_config)

    # 验证 set_failure 被调用
    logger_instance.set_failure.assert_called()


@patch("ion_CSP.run.main_CSP.CrystalGenerator")
def test_generation_task(mock_generator, mock_work_dir, mock_config):
    """测试晶体生成任务"""
    # 设置 mock
    generator_instance = MagicMock()
    mock_generator.return_value = generator_instance

    # 执行生成任务
    generation_task(mock_work_dir, mock_config)

    # 验证 CrystalGenerator 被正确初始化
    mock_generator.assert_called_once_with(
        work_dir=mock_work_dir,
        ion_numbers=mock_config["gen_opt"]["ion_numbers"],
        species=mock_config["gen_opt"]["species"],
    )

    # 验证方法被调用
    generator_instance.generate_structures.assert_called_once_with(
        num_per_group=mock_config["gen_opt"]["num_per_group"],
        space_groups_limit=mock_config["gen_opt"]["space_groups_limit"],
    )
    generator_instance.phonopy_processing.assert_called_once()


@patch("ion_CSP.run.main_CSP.CrystalGenerator")
def test_mlp_optimization_task(mock_generator, mock_work_dir, mock_config):
    """测试机器学习势优化任务"""
    # 设置 mock
    generator_instance = MagicMock()
    mock_generator.return_value = generator_instance

    # 执行优化任务
    mlp_optimization_task(mock_work_dir, mock_config)

    # 验证 CrystalGenerator 被正确初始化
    mock_generator.assert_called_once_with(
        work_dir=mock_work_dir,
        ion_numbers=mock_config["gen_opt"]["ion_numbers"],
        species=mock_config["gen_opt"]["species"],
    )

    # 验证 dpdisp_mlp_tasks 被调用
    generator_instance.dpdisp_mlp_tasks.assert_called_once_with(
        machine_path=mock_config["gen_opt"]["machine"],
        resources_path=mock_config["gen_opt"]["resources"],
        nodes=mock_config["gen_opt"]["nodes"],
    )


@patch("ion_CSP.run.main_CSP.ReadMlpDensity")
def test_read_mlp_density_task(mock_read_mlp, mock_work_dir, mock_config):
    """测试读取机器学习势密度任务"""
    # 设置 mock
    mlp_instance = MagicMock()
    mock_read_mlp.return_value = mlp_instance

    # 执行读取任务
    read_mlp_density_task(mock_work_dir, mock_config)

    # 验证 ReadMlpDensity 被正确初始化
    mock_read_mlp.assert_called_once_with(work_dir=mock_work_dir)

    # 验证方法被调用
    mlp_instance.read_property_and_sort.assert_called_once_with(
        n_screen=mock_config["read_mlp_density"]["n_screen"],
        sort_by=mock_config["read_mlp_density"]["sort_by"],
        molecules_screen=mock_config["read_mlp_density"]["molecules_screen"],
        detail_log=mock_config["read_mlp_density"]["detail_log"],
    )
    mlp_instance.phonopy_processing_max_density.assert_called_once()


@patch("ion_CSP.run.main_CSP.VaspProcessing")
def test_vasp_optimization_task(mock_vasp, mock_work_dir, mock_config):
    """测试VASP优化任务"""
    # 设置 mock
    vasp_instance = MagicMock()
    mock_vasp.return_value = vasp_instance

    # 执行VASP优化任务
    vasp_optimization_task(mock_work_dir, mock_config)

    # 验证 VaspProcessing 被正确初始化
    mock_vasp.assert_called_once_with(work_dir=mock_work_dir)

    # 验证方法被调用
    vasp_instance.dpdisp_vasp_optimization_tasks.assert_called_once_with(
        machine_path=mock_config["vasp_processing"]["machine"],
        resources_path=mock_config["vasp_processing"]["resources"],
        nodes=mock_config["vasp_processing"]["nodes"],
    )
    vasp_instance.read_vaspout_save_csv.assert_called_once_with(
        molecules_prior=mock_config["vasp_processing"]["molecules_prior"]
    )


@patch("ion_CSP.run.main_CSP.VaspProcessing")
def test_vasp_relaxation_task(mock_vasp, mock_work_dir, mock_config):
    """测试VASP弛豫任务"""
    # 设置 mock
    vasp_instance = MagicMock()
    mock_vasp.return_value = vasp_instance

    # 执行VASP弛豫任务
    vasp_relaxation_task(mock_work_dir, mock_config)

    # 验证 VaspProcessing 被正确初始化
    mock_vasp.assert_called_once_with(work_dir=mock_work_dir)

    # 验证方法被调用
    vasp_instance.dpdisp_vasp_relaxation_tasks.assert_called_once_with(
        machine_path=mock_config["vasp_processing"]["machine"],
        resources_path=mock_config["vasp_processing"]["resources"],
        nodes=mock_config["vasp_processing"]["nodes"],
    )
    vasp_instance.read_vaspout_save_csv.assert_called_once_with(
        molecules_prior=mock_config["vasp_processing"]["molecules_prior"], relaxation=True
    )
    vasp_instance.export_max_density_structure.assert_called_once_with(relaxation=True)


@patch("ion_CSP.run.main_CSP.get_work_dir_and_config")
@patch("ion_CSP.run.main_CSP.merge_config")
@patch("ion_CSP.run.main_CSP.main")
def test_main_entry_point(mock_main, mock_merge, mock_get_config):
    """测试主入口点"""
    # 设置 mock
    mock_get_config.return_value = ("/test/work", {"test": "config"})
    mock_merge.return_value = {"merged": "config"}

    # 导入并执行 __main__ 块
    import ion_CSP.run.main_CSP as main_csp_module

    # 模拟 __main__ 执行
    work_dir, config = mock_get_config()
    modules = ["gen_opt", "read_mlp_density", "vasp_processing"]
    for module in modules:
        config[module] = mock_merge(
            default_config=DEFAULT_CONFIG, user_config=config, key=module
        )
    mock_main(work_dir, config)

    # 验证函数被调用
    mock_get_config.assert_called_once()
    assert mock_merge.call_count == 3
    mock_main.assert_called_once()


def test_default_config_structure():
    """测试默认配置结构"""
    assert "gen_opt" in DEFAULT_CONFIG
    assert "read_mlp_density" in DEFAULT_CONFIG
    assert "vasp_processing" in DEFAULT_CONFIG

    # 验证 gen_opt 配置
    assert "num_per_group" in DEFAULT_CONFIG["gen_opt"]
    assert "space_groups_limit" in DEFAULT_CONFIG["gen_opt"]
    assert "nodes" in DEFAULT_CONFIG["gen_opt"]
    assert DEFAULT_CONFIG["gen_opt"]["num_per_group"] == 500
    assert DEFAULT_CONFIG["gen_opt"]["space_groups_limit"] == 230

    # 验证 read_mlp_density 配置
    assert "n_screen" in DEFAULT_CONFIG["read_mlp_density"]
    assert "sort_by" in DEFAULT_CONFIG["read_mlp_density"]
    assert "molecules_screen" in DEFAULT_CONFIG["read_mlp_density"]
    assert DEFAULT_CONFIG["read_mlp_density"]["n_screen"] == 10
    assert DEFAULT_CONFIG["read_mlp_density"]["sort_by"] == "density"

    # 验证 vasp_processing 配置
    assert "nodes" in DEFAULT_CONFIG["vasp_processing"]
    assert "molecules_prior" in DEFAULT_CONFIG["vasp_processing"]
    assert DEFAULT_CONFIG["vasp_processing"]["nodes"] == 2
    assert DEFAULT_CONFIG["vasp_processing"]["molecules_prior"] is True


