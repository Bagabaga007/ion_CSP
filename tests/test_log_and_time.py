import os
import yaml
import pytest
from unittest.mock import patch
from ion_CSP.log_and_time import (
    log_and_time,
    merge_config,
    StatusLogger,
    redirect_dpdisp_logging,
    get_work_dir_and_config,
)


# 测试 log_and_time 装饰器
@log_and_time
def dummy_function(work_dir):
    return "Function executed"


def _test_log_and_time_decorator(tmp_path):
    # 使用装饰器的函数
    result = dummy_function(str(tmp_path))

    # 检查返回值
    assert result == "Function executed"

    # 获取脚本名并构造日志文件路径
    script_name = os.path.splitext(os.path.basename(__file__))[0]
    log_file = tmp_path / f"{script_name}_output.log"

    # 检查日志文件是否创建
    print(f"Expected log file path: {log_file}")  # Debugging line
    assert log_file.exists()

    # 验证日志内容
    with open(log_file, "r") as f:
        logs = f.readlines()
        assert "Start running: dummy_function" in logs[0]
        assert "End running: dummy_function" in logs[-1]


# 测试 merge_config 函数
def test_merge_config():
    default_config = {"key": {"a": 1, "b": 2}}
    user_config = {"key": {"b": 3, "c": 4}}
    merged = merge_config(default_config, user_config, "key")

    assert merged == {"a": 1, "b": 3, "c": 4}


# 测试 StatusLogger 类
def test_status_logger_initialization(tmp_path):
    logger = StatusLogger(tmp_path, "TestTask")

    assert logger.task_name == "TestTask"
    assert logger.current_status == "INITIAL"
    assert logger.run_count == 0
    assert os.path.exists(tmp_path / "workflow_status.log")
    assert os.path.exists(tmp_path / "workflow_status.yaml")


def test_status_logger_set_running(tmp_path):
    logger = StatusLogger(tmp_path, "TestTask")
    logger.set_running()

    assert logger.current_status == "RUNNING"
    assert logger.run_count == 1


def test_status_logger_set_success(tmp_path):
    logger = StatusLogger(tmp_path, "TestTask")
    logger.set_success()

    assert logger.current_status == "SUCCESS"


def test_status_logger_set_failure(tmp_path):
    logger = StatusLogger(tmp_path, "TestTask")
    logger.set_failure()

    assert logger.current_status == "FAILURE"


# 测试信号处理
def _test_signal_handler(caplog, tmp_path):
    logger = StatusLogger(tmp_path, "TestTask")

    with patch("sys.exit") as mock_exit:
        logger._signal_handler(2, None)  # 模拟 Ctrl + C
        assert "Process" in caplog.text
        mock_exit.assert_called_once_with(0)


# 测试 redirect_dpdisp_logging
def test_redirect_dpdisp_logging(tmp_path):
    custom_log_path = tmp_path / "custom_log.log"
    redirect_dpdisp_logging(str(custom_log_path))

    assert os.path.exists(custom_log_path)


# 测试 get_work_dir_and_config
def _test_get_work_dir_and_config(monkeypatch, tmp_path):
    # 创建一个模拟的 config.yaml 文件
    config_content = {"key": "value"}
    with open(tmp_path / "config.yaml", "w") as f:
        yaml.dump(config_content, f)

    # 模拟输入工作目录
    monkeypatch.setattr("builtins.input", lambda _: str(tmp_path))

    work_dir, user_config = get_work_dir_and_config()

    assert work_dir == str(tmp_path)
    assert user_config == config_content


def test_get_work_dir_and_config_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda _: str(tmp_path))

    # 测试找不到 config.yaml 的情况
    with pytest.raises(SystemExit):
        get_work_dir_and_config()
