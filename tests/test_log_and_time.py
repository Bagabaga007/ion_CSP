import yaml
import pytest
import logging
from pathlib import Path
from dpdispatcher.dlog import dlog
import importlib
import dpdispatcher
from unittest.mock import patch, Mock, MagicMock

from ion_CSP.log_and_time import (
    log_and_time,
    merge_config,
    StatusLogger,
    redirect_dpdisp_logging,
    get_work_dir_and_config,
    machine_resources_prep,
)


# ========================== 测试 log_and_time 装饰器 ===============================
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


def test_log_and_time_decorator_main_module(tmp_path, caplog):
    # 模拟函数在 __main__ 中（module 为 None）
    def dummy_in_main(work_dir):
        return "executed"
    caplog.set_level(logging.INFO)
    # 临时替换 inspect.getmodule 为返回 None
    with patch("inspect.getmodule", return_value=None):
        decorated = log_and_time(dummy_in_main)
        result = decorated(tmp_path)
        assert result == "executed"
        assert "Start running:" in caplog.text
        assert "End running:" in caplog.text


def test_log_and_time_decorator_exception(tmp_path, caplog):
    # 确保捕获所有日志
    caplog.set_level(logging.INFO)

    # 使用装饰器的函数
    result = dummy_function(tmp_path)

    with pytest.raises(ZeroDivisionError):
        @log_and_time
        def faulty_function(work_dir):
            return 1 / 0
        
        faulty_function(tmp_path)
    assert "Error occurred: division by zero" in caplog.text


# ========================== 测试 merge_config 函数 ====================
def test_merge_config():
    default = {"key1": {"a": 1, "b": 2}, "key2": {"c": 3}}
    user = {"key1": {"b": 20, "d": 4}, "key3": {"e": 5}}

    # 合并 key1
    merged = merge_config(default, user, "key1")
    assert merged == {"a": 1, "b": 20, "d": 4}

    # 测试默认配置中不存在的键
    with pytest.raises(KeyError):
        merge_config(default, user, "key3")

    # 测试用户配置中不存在的键
    with pytest.raises(KeyError):
        merge_config(default, user, "key2")

    # 测试非字典值
    with pytest.raises(TypeError):
        merge_config({"key": 1}, {"key": 2}, "key")


# ========================== 测试 StatusLogger 类 ====================
def test_status_logger_initialization(tmp_path):
    logger = StatusLogger(tmp_path, "TestTask")

    assert logger.task_name == "TestTask"
    assert logger.current_status == "INITIAL"
    assert logger.run_count == 0
    assert (tmp_path / "workflow_status.log").exists()

    # 检查 YAML 文件是否创建
    yaml_file = tmp_path / "workflow_status.yaml"
    assert yaml_file.exists()

    # 检查 YAML 内容
    with open(yaml_file, "r") as f:
        status_info = yaml.safe_load(f)
        assert "TestTask" in status_info
        assert status_info["TestTask"]["current_status"] == "INITIAL"
        assert status_info["TestTask"]["run_count"] == 0


def test_status_logger_transitions(tmp_path):
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


# 定义信号测试用例：(signum, expected_message_fragment, expected_status)
SIGNAL_TEST_CASES = [
    (2, "has been interrupted by 'Ctrl + C'", "KILLED"),  # SIGINT
    (15, "has been killed by 'kill <pid>' order", "KILLED"),  # SIGTERM
    (3, "received signal 3. Exiting ...", "KILLED"),  # SIGQUIT
    (9, "received signal 9. Exiting ...", "KILLED"),  # SIGKILL
    (1, "received signal 1. Exiting ...", "KILLED"),  # SIGHUP
]
@pytest.mark.parametrize(
    "signum, expected_msg_frag, expected_status", SIGNAL_TEST_CASES
)
def test_status_logger_signal_handler_various_signals(
    tmp_path, caplog, signum, expected_msg_frag, expected_status
):
    # 1. 创建 StatusLogger 实例
    logger = StatusLogger(tmp_path, "TestTask")

    # 2. 将 caplog 的 handler 挂到 "WorkflowLogger" 上（关键！）
    workflow_logger = logging.getLogger("WorkflowLogger")
    for h in list(workflow_logger.handlers):
        workflow_logger.removeHandler(h)
    workflow_logger.addHandler(caplog.handler)
    workflow_logger.setLevel(logging.WARNING)

    # 3. 模拟信号处理
    with patch("sys.exit") as mock_exit:
        logger._signal_handler(signum, None)
        mock_exit.assert_called_once_with(0)

    # 4. 获取所有日志记录
    # caplog.records 是一个列表，包含每个 LogRecord 对象
    found = False
    for record in caplog.records:
        if record.levelname == "WARNING" and expected_msg_frag in record.message:
            found = True
            break

    assert found, (
        f"Expected log message to contain: '{expected_msg_frag}'\n"
        f"Got logs: {[r.message for r in caplog.records]}"
    )

    # 5. 验证状态
    assert logger.current_status == expected_status

    # 6. 验证 YAML
    yaml_file = tmp_path / "workflow_status.yaml"
    assert yaml_file.exists()
    with open(yaml_file, "r") as f:
        status_info = yaml.safe_load(f)
        assert status_info["TestTask"]["current_status"] == expected_status


# ========================== 测试 redirect_dpdisp_logging 函数 ====================
def test_redirect_dpdisp_logging_with_stream_handler(tmp_path):
    # 1. 保存原始 dlog 的 handlers
    original_handlers = list(dlog.handlers)
    dlog.handlers.clear()  # 清空真实 dlog 的 handlers，确保干净

    # 2. 添加一个 StreamHandler（模拟真实环境）
    stream_handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    stream_handler.setFormatter(formatter)
    dlog.addHandler(stream_handler)

    # 3. 确保有 StreamHandler
    assert any(isinstance(h, logging.StreamHandler) for h in dlog.handlers)

    # 4. 创建目标日志文件
    custom_log_path = tmp_path / "custom_dpdispatcher.log"

    # 5. 调用函数：它会操作真实的 dlog，但我们已经清空并控制了它
    redirect_dpdisp_logging(str(custom_log_path))

    # 6. 检查 dlog.handlers 中是否添加了 FileHandler
    new_file_handler = None
    for h in dlog.handlers:
        if isinstance(h, logging.FileHandler) and h.baseFilename == str(
            custom_log_path
        ):
            new_file_handler = h
            break

    assert new_file_handler is not None, "Expected a new FileHandler for custom log"

    # 7. 强制刷新
    new_file_handler.flush()
    new_file_handler.close()

    # 8. 验证内容
    with open(custom_log_path, "r") as f:
        content = f.read()
        assert "LOG INIT:dpdispatcher log direct to" in content

    # 9. 验证 formatter 被复制
    assert new_file_handler.formatter is not None
    assert new_file_handler.formatter._fmt == formatter._fmt

    # 10. 恢复原始状态
    dlog.handlers.clear()
    for h in original_handlers:
        dlog.addHandler(h)


def test_redirect_dpdisp_logging_no_stream_handler(tmp_path):
    # 1. 保存原始 handlers
    original_handlers = list(dlog.handlers)

    # 2. 清空 dlog 的所有 handlers
    dlog.handlers.clear()

    # 3. 确保没有 StreamHandler（关键！）
    assert not any(isinstance(h, logging.StreamHandler) for h in dlog.handlers)

    # 4. 设置 dlog 的 level 为 INFO，确保 info() 能执行
    dlog.setLevel(logging.INFO)

    # 5. 创建目标日志文件
    custom_log_path = tmp_path / "custom_dpdispatcher.log"

    # 6. 调用函数（它会操作真实的 dlog，但现在我们控制了它）
    redirect_dpdisp_logging(str(custom_log_path))

    # 7. 检查 dlog.handlers 中是否添加了 FileHandler
    new_file_handler = None
    for h in dlog.handlers:
        if isinstance(h, logging.FileHandler) and h.baseFilename == str(
            custom_log_path
        ):
            new_file_handler = h
            break

    assert new_file_handler is not None, "Expected a new FileHandler for custom log"

    # 8. 强制刷新确保写入
    new_file_handler.flush()
    new_file_handler.close()

    # 9. 验证日志内容
    with open(custom_log_path, "r") as f:
        content = f.read()
        assert "LOG INIT:dpdispatcher log direct to" in content

    # 10. 验证使用了默认格式 %(message)s
    assert new_file_handler.formatter._fmt == "%(message)s"

    # 11. 恢复原始状态
    dlog.handlers.clear()
    for h in original_handlers:
        dlog.addHandler(h)


def test_redirect_dpdisp_logging_with_non_stream_handler(tmp_path):
    """使用真实 FileHandler 避免 isinstance 失效，只 Mock dlog 对象"""

    # 1. 创建一个真实的 FileHandler（确保 isinstance 检查通过）
    fake_file_handler = logging.FileHandler(tmp_path / "interference.log")
    fake_file_handler.level = logging.INFO

    # 2. 创建一个 Mock 的 dlog 对象
    mock_dlog = MagicMock()
    mock_dlog.handlers = [fake_file_handler]  # 设置一个真实 FileHandler
    mock_dlog.removeHandler = MagicMock()
    mock_dlog.addHandler = MagicMock()
    mock_dlog.info = MagicMock()

    # 3. 用 patch 替换 dlog
    with patch("ion_CSP.log_and_time.dlog", new=mock_dlog):
        # 4. 调用函数
        custom_log_path = tmp_path / "custom_dpdispatcher.log"
        redirect_dpdisp_logging(str(custom_log_path))

        # 5. 验证：removeHandler 被调用了一次（因为有 FileHandler）
        mock_dlog.removeHandler.assert_called_once_with(fake_file_handler)

        # 6. 验证：addHandler 被调用了一次（添加了新 FileHandler）
        assert mock_dlog.addHandler.call_count == 1
        added_handler = mock_dlog.addHandler.call_args[0][0]
        assert isinstance(added_handler, logging.FileHandler)
        assert added_handler.baseFilename == str(custom_log_path)

        # 7. 验证：formatter 是默认的 %(message)s（因为没有 StreamHandler）
        assert added_handler.formatter._fmt == "%(message)s"

        # 8. 验证：info 被调用
        mock_dlog.info.assert_called_once_with(
            f"LOG INIT:dpdispatcher log direct to {custom_log_path}"
        )


# ========================== 测试 get_work_dir_and_config 函数 ====================
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


def test_get_work_dir_and_config_config_not_found(monkeypatch, tmp_path):
    # 创建一个存在的工作目录
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    # 不创建 config.yaml → 触发 FileNotFoundError
    monkeypatch.setattr("builtins.input", lambda _: str(work_dir))
    monkeypatch.setattr("sys.argv", ["script.py"])

    # 关键：patch sys.exit，让它不退出，而是抛出异常（coverage 能记录）
    with patch("sys.exit") as mock_exit:
        get_work_dir_and_config()

        # 验证 sys.exit(1) 被调用
        mock_exit.assert_called_once_with(1)


def test_get_work_dir_and_config_invalid_then_valid_workdir(monkeypatch, tmp_path):
    # 创建一个临时目录作为最终有效路径
    valid_dir = tmp_path / "valid_project"
    valid_dir.mkdir()

    # 模拟用户先输入无效路径，再输入有效路径
    inputs = [
        "/nonexistent/path",  # 无效
        str(tmp_path / "not_a_dir"),  # 无效（文件不存在）
        str(valid_dir),  # 有效 → 应该退出循环
    ]
    monkeypatch.setattr("builtins.input", lambda _: inputs.pop(0))

    # 模拟 sys.argv 只有程序名（无参数）
    monkeypatch.setattr("sys.argv", ["script.py"])

    # 创建 config.yaml（避免因 config 问题干扰）
    (valid_dir / "config.yaml").touch()

    # 执行函数
    work_dir, user_config = get_work_dir_and_config()

    # 验证
    assert work_dir == valid_dir.resolve()
    assert user_config == {}  # 空文件


def test_get_work_dir_and_config_yaml_syntax_error(monkeypatch, tmp_path, capsys):
    # 1. 创建工作目录
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    # 2. 创建一个语法错误的 config.yaml（未闭合的列表）
    (work_dir / "config.yaml").write_text(
        """
key1: value1
key2: 
  nested: 
    - item1
    - item2
  invalid: [  # ← 缺少闭合括号
""",
        encoding="utf-8",
    )

    # 3. 模拟输入工作目录
    monkeypatch.setattr("builtins.input", lambda _: str(work_dir))

    # 4. 模拟 sys.argv
    monkeypatch.setattr("sys.argv", ["script.py"])

    # 5. 捕获退出
    with pytest.raises(SystemExit) as excinfo:
        get_work_dir_and_config()

    # 6. 验证退出码为 1
    assert excinfo.value.code == 1

    # 7. 检查 stdout 与 stderr
    captured = capsys.readouterr()  # 注意：是 readouterr()，不是 readerr()

    # 验证错误信息被打印到 stderr
    assert "Error parsing YAML file:" in captured.err
    assert "Line 8, Column 1" in captured.err  # 具体行号可能变化，但应包含
    assert "Details:" in captured.err
    assert "expected the node content" in captured.err  # yaml 解析错误的典型描述


# ==================== 测试 machine_resources_prep 函数 ====================
def test_machine_resources_prep_success(tmp_path: Path):
    machine_path1 = tmp_path / "machine.json"
    resources_path1 = tmp_path / "resources.json"

    machine_path1.write_text(
        '{"context_type": "LocalContext", "local_root": "./", "remote_root": "/your/remote/workplace", "batch_type": "Shell"}',
        encoding="utf-8",
    )
    resources_path1.write_text(
        '{"number_node": 1, "cpu_per_node": 4, "gpu_per_node": 1, "group_size": 1}',
        encoding="utf-8",
    )

    machine1, resources1, parent1 = machine_resources_prep(
        str(machine_path1), str(resources_path1)
    )

    assert machine1.serialize()["context_type"] == "LocalContext"
    assert parent1 == ""

    machine_path2 = tmp_path / "machine.yaml"
    resources_path2 = tmp_path / "resources.yaml"

    machine_path2.write_text(
        """
context_type: LocalContext
local_root: ./ 
remote_root: /your/remote/workplace
batch_type: Shell
""",
        encoding="utf-8",
    )

    resources_path2.write_text(
        """
number_node: 2
cpu_per_node: 8
gpu_per_node: 0
group_size: 1
""",
        encoding="utf-8",
    )

    machine2, resources2, parent2 = machine_resources_prep(
        str(machine_path2), str(resources_path2)
    )

    assert machine2.serialize()["context_type"] == "LocalContext"
    assert parent2 == ""


def test_machine_resources_prep_yaml_ssh_parse_only(tmp_path: Path):
    machine_path = tmp_path / "machine.yaml"
    resources_path = tmp_path / "resources.yaml"

    machine_path.write_text(
        """
context_type: SSHContext
local_root: ./
remote_root: /your/remote/workplace
batch_type: Shell
remote_profile:
  hostname: "sshhost"
  username: "testuser"
  password: "testpass"
""",
        encoding="utf-8",
    )

    resources_path.write_text(
        """
number_node: 2
cpu_per_node: 8
gpu_per_node: 0
group_size: 1
""",
        encoding="utf-8",
    )

    # 模拟 SSHSession._setup_ssh() 为空操作，避免连接
    with (
        patch("dpdispatcher.contexts.ssh_context.SSHSession._setup_ssh"),
        patch("dpdispatcher.contexts.ssh_context.SSHSession.ensure_alive"),
        patch(
            "dpdispatcher.contexts.ssh_context.SSHSession.sftp", new_callable=Mock
        ) as mock_sftp,
    ):
        # 让 sftp 属性返回一个空的 Mock 对象，避免访问 _sftp
        mock_sftp.return_value = Mock()  # 返回一个什么都不做的 SFTP 对象

        machine, resources, parent = machine_resources_prep(
            str(machine_path), str(resources_path)
        )

    assert machine.serialize()["context_type"] == "SSHContext"
    assert parent == "data/"
    assert resources.serialize()["number_node"] == 2
    assert resources.serialize()["cpu_per_node"] == 8


def test_machine_resources_prep_invalid_type(tmp_path: Path):
    machine_path = tmp_path / "machine.txt"
    resources_path = tmp_path / "resources.json"

    machine_path.write_text("dummy", encoding="utf-8")
    with pytest.raises(KeyError, match="Unsupported machine file type"):
        machine_resources_prep(str(machine_path), str(resources_path))

    machine_path = tmp_path / "machine.json"
    machine_path.write_text(
        '{"context_type": "LocalContext", "local_root": "./", "remote_root": "/your/remote/workplace", "batch_type": "Shell"}',
        encoding="utf-8",
    )
    resources_path = tmp_path / "resources.txt"
    resources_path.write_text("dummy", encoding="utf-8")
    with pytest.raises(KeyError, match="Unsupported resources file type"):
        machine_resources_prep(str(machine_path), str(resources_path))


def test_machine_resources_prep_unsupported_context_type(tmp_path):
    # 1. 创建一个非法的 machine.yaml 文件
    machine_path = tmp_path / "machine.yaml"
    machine_path.write_text(
        """
context_type: LazyLocalContext
local_root: ./
remote_root: /remote/work
batch_type: Shell
""",
        encoding="utf-8",
    )

    # 2. 创建一个合法的 resources.yaml 文件（确保不影响测试）
    resources_path = tmp_path / "resources.yaml"
    resources_path.write_text(
        """
number_node: 1
cpu_per_node: 4
gpu_per_node: 0
group_size: 1
""",
        encoding="utf-8",
    )

    # 3. 验证：当 context_type 不支持时，应抛出 KeyError
    with pytest.raises(
        KeyError, match=r"Unsupported context type in machine configuration"
    ):
        machine_resources_prep(str(machine_path), str(resources_path))
