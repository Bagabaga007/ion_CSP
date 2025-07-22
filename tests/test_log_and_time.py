import os
import yaml
import pytest
import logging
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


def test_log_and_time_decorator(tmp_path, caplog):
    # 确保捕获所有日志
    caplog.set_level(logging.INFO)

    # 使用装饰器的函数
    result = dummy_function(tmp_path)

    # 检查返回值
    assert result == "Function executed"

    # 检查日志是否被捕获
    assert "Start running:" in caplog.text
    assert "End running:" in caplog.text
    assert "Wall time:" in caplog.text
    assert "CPU time:" in caplog.text


# 测试 get_work_dir_and_config
def test_get_work_dir_and_config(monkeypatch, tmp_path):
    # 创建一个模拟的 config.yaml 文件
    config_content = {"key": "value"}
    with open(tmp_path / "config.yaml", "w") as f:
        yaml.dump(config_content, f)

    # 模拟输入工作目录
    monkeypatch.setattr("builtins.input", lambda _: str(tmp_path))

    # 保存原始的sys.argv
    # 然后模拟sys.argv，使其只包含程序名，避免pytest传递的参数干扰
    monkeypatch.setattr("sys.argv", ["run_pytest_script.py"])
    
    work_dir, user_config = get_work_dir_and_config()

    assert work_dir == tmp_path
    assert user_config == config_content


def test_get_work_dir_and_config_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda _: str(tmp_path))

    # 测试找不到 config.yaml 的情况
    with pytest.raises(SystemExit):
        get_work_dir_and_config()


# 测试信号处理
def test_signal_handler(tmp_path):
    # 创建 StatusLogger 实例
    logger = StatusLogger(tmp_path, "TestTask")

    # 创建模拟的 sys.exit
    with patch("sys.exit") as mock_exit:
        # 模拟 Ctrl + C
        logger._signal_handler(2, None)

        # 检查状态是否被设置为 KILLED
        assert logger.current_status == "KILLED"

        # 检查 sys.exit 是否被调用
        mock_exit.assert_called_once_with(0)

        # 检查 YAML 文件是否更新
        yaml_file = tmp_path / "workflow_status.yaml"
        assert yaml_file.exists()

        with open(yaml_file, "r") as f:
            status_info = yaml.safe_load(f)
            assert status_info["TestTask"]["current_status"] == "KILLED"


# 测试 merge_config 函数
def test_merge_config():
    default = {"key1": {"a": 1, "b": 2}, "key2": {"c": 3}}
    user = {"key1": {"b": 20, "d": 4}, "key3": {"e": 5}}

    # 合并 key1
    merged = merge_config(default, user, "key1")
    assert merged == {"a": 1, "b": 20, "d": 4}

    # 测试不存在的键
    with pytest.raises(KeyError):
        merge_config(default, user, "key3")

    # 测试非字典值
    with pytest.raises(TypeError):
        merge_config({"key": 1}, {"key": 2}, "key")


# 测试 StatusLogger 类
def test_status_logger_initialization(tmp_path):
    logger = StatusLogger(tmp_path, "TestTask")

    assert logger.task_name == "TestTask"
    assert logger.current_status == "INITIAL"
    assert logger.run_count == 0
    assert os.path.exists(tmp_path / "workflow_status.log")

    # 检查 YAML 文件是否创建
    yaml_file = tmp_path / "workflow_status.yaml"
    assert yaml_file.exists()

    # 检查 YAML 内容
    with open(yaml_file, "r") as f:
        status_info = yaml.safe_load(f)
        assert "TestTask" in status_info
        assert status_info["TestTask"]["current_status"] == "INITIAL"
        assert status_info["TestTask"]["run_count"] == 0


# 测试状态转换
def test_status_transitions(tmp_path):
    # 创建 StatusLogger 实例
    logger = StatusLogger(tmp_path, "TestTask")

    # 设置运行状态
    logger.set_running()
    assert logger.current_status == "RUNNING"
    assert logger.run_count == 1

    # 设置成功状态
    logger.set_success()
    assert logger.current_status == "SUCCESS"
    assert logger.is_successful()

    # 设置失败状态
    logger.set_failure()
    assert logger.current_status == "FAILURE"

    # 检查 YAML 文件是否更新
    yaml_file = tmp_path / "workflow_status.yaml"
    with open(yaml_file, "r") as f:
        status_info = yaml.safe_load(f)
        assert status_info["TestTask"]["current_status"] == "FAILURE"
        assert status_info["TestTask"]["run_count"] == 1


# 测试重定向 dpdispatcher 日志
def test_redirect_dpdisp_logging(tmp_path):
    # 创建自定义日志路径
    custom_log = tmp_path / "custom_dpdispatcher.log"

    # 重定向日志
    redirect_dpdisp_logging(str(custom_log))

    # 检查日志文件是否创建
    assert custom_log.exists()

    # 检查日志内容
    with open(custom_log, "r") as f:
        content = f.read()
        assert "LOG INIT:dpdispatcher log direct to" in content

