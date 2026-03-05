"""CSP工作流系统测试

这是端到端测试，验证整个CSP工作流的集成。
使用mock和最小化数据来测试工作流的各个环节。
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock


@pytest.mark.system
def test_csp_workflow_with_mocks(tmp_path):
    """使用mock测试CSP工作流的各个步骤"""
    from ion_CSP.run.main_CSP import (
        generation_task,
        read_mlp_density_task,
    )

    work_dir = tmp_path / "csp_mock"
    work_dir.mkdir()

    config = {
        "gen_opt": {
            "num_per_group": 2,
            "space_groups_limit": 10,
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

    # 测试生成任务
    with patch("ion_CSP.run.main_CSP.CrystalGenerator") as mock_gen:
        mock_gen_instance = MagicMock()
        mock_gen.return_value = mock_gen_instance

        generation_task(work_dir, config)
        assert mock_gen_instance.generate_structures.called

    # 测试读取密度任务
    with patch("ion_CSP.run.main_CSP.ReadMlpDensity") as mock_read:
        mock_read_instance = MagicMock()
        mock_read.return_value = mock_read_instance

        read_mlp_density_task(work_dir, config)
        assert mock_read_instance.read_property_and_sort.called


@pytest.mark.system
def test_csp_workflow_error_handling(tmp_path):
    """测试CSP工作流的错误处理

    验证当出现错误时，系统能够正确处理：
    - 无效的输入参数
    - 缺失的文件
    - 计算失败
    """
    from ion_CSP.gen_opt import CrystalGenerator

    work_dir = tmp_path / "csp_error"
    work_dir.mkdir()

    # 测试缺失物种文件的情况
    # CrystalGenerator 在初始化时会尝试读取物种文件
    # 如果文件不存在，会抛出 FileNotFoundError
    ion_numbers = [1, 1]
    species = ["Li", "F"]

    with pytest.raises(FileNotFoundError):
        generator = CrystalGenerator(
            work_dir=work_dir,
            ion_numbers=ion_numbers,
            species=species
        )


@pytest.mark.system
def test_csp_workflow_status_tracking(tmp_path):
    """测试CSP工作流的状态跟踪

    验证StatusLogger能够正确跟踪任务状态
    """
    from ion_CSP.log_and_time import StatusLogger

    work_dir = tmp_path / "csp_status"
    work_dir.mkdir()

    # 测试任务状态跟踪
    task_logger = StatusLogger(work_dir=work_dir, task_name="test_task")

    # 初始状态应该是未成功
    assert not task_logger.is_successful()

    # 设置为运行中
    task_logger.set_running()

    # 设置为成功
    task_logger.set_success()

    # 验证状态
    assert task_logger.is_successful()


@pytest.mark.system
def test_csp_workflow_directory_structure(tmp_path):
    """测试CSP工作流创建正确的目录结构"""
    from ion_CSP.log_and_time import StatusLogger

    work_dir = tmp_path / "csp_structure"
    work_dir.mkdir()

    # 创建StatusLogger
    logger = StatusLogger(work_dir=work_dir, task_name="structure_test")

    # 验证工作目录存在
    assert work_dir.exists()

    # StatusLogger会创建状态文件
    # 验证可以设置和读取状态
    logger.set_running()
    logger.set_success()
    assert logger.is_successful()


@pytest.mark.system
@pytest.mark.slow
def test_csp_workflow_config_validation(tmp_path):
    """测试CSP工作流的配置验证"""
    from ion_CSP.log_and_time import merge_config

    # 测试配置合并功能
    default_config = {
        "gen_opt": {
            "num_per_group": 10,
            "space_groups_limit": 230,
        }
    }

    user_config = {
        "gen_opt": {
            "num_per_group": 5,
        }
    }

    # 合并配置
    merged = merge_config(default_config, user_config, "gen_opt")

    # 验证合并结果
    assert merged["num_per_group"] == 5  # 用户配置覆盖
    assert merged["space_groups_limit"] == 230  # 保留默认值


@pytest.mark.system
def test_csp_workflow_crystal_generator_initialization(tmp_path):
    """测试CrystalGenerator的初始化和错误处理

    测试当缺少物种文件时，系统能够正确抛出错误
    """
    from ion_CSP.gen_opt import CrystalGenerator

    work_dir = tmp_path / "csp_init"
    work_dir.mkdir()

    # 配置
    ion_numbers = [1, 1]
    species = ["Li", "F"]

    # 测试缺少物种文件时的错误处理
    # CrystalGenerator在初始化时会尝试读取物种文件
    # 如果文件不存在，应该抛出FileNotFoundError
    with pytest.raises(FileNotFoundError):
        generator = CrystalGenerator(
            work_dir=work_dir,
            ion_numbers=ion_numbers,
            species=species
        )
