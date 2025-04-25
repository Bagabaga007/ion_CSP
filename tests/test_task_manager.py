import mock
import psutil
import pytest
from pathlib import Path
from ion_CSP.task_manager import TaskManager


@pytest.fixture
def task_mgr():
    return TaskManager()

@pytest.fixture
def temp_workspace(tmp_path):
    """为每个测试创建独立工作目录"""
    return tmp_path / "workspace"

@pytest.fixture(autouse=True)
def cleanup_logs():
    """自动清理旧日志文件"""
    yield
    for log_file in Path("logs").glob("**/*.log"):
        log_file.unlink()

@pytest.fixture
def iso_fs(tmp_path):
    """创建隔离的文件系统环境"""
    iso_path = tmp_path / "iso_fs"
    iso_path.mkdir(parents=True, exist_ok=True)
    return iso_path

# @pytest.fixture(autouse=True)
# def cleanup_workspace(temp_workspace):
#     """自动清理测试工作目录"""
#     yield
#     for file in temp_workspace.glob("**/*"):
#         if file.is_file():
#             file.unlink()
#         elif file.is_dir():
#             file.rmdir()

def test_task_creation(temp_workspace, task_mgr):
    """测试任务创建流程"""
    work_dir = temp_workspace / "test_EE"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 模拟子进程
    mock_proc = mock.Mock()
    mock_proc.pid = 1234
    with mock.patch("subprocess.Popen", return_value=mock_proc):
        task_mgr.task_runner("EE", str(work_dir))

    # 验证日志文件创建
    log_file = work_dir / "main_EE_console.log"
    assert log_file.exists()

    # 验证PID文件处理
    pid_file = work_dir / "pid.txt"
    assert not pid_file.exists()

def test_process_termination(task_mgr):
    """测试进程终止功能"""
    # 创建测试进程
    test_pid = 9999
    mock_proc = mock.Mock(pid=test_pid, status=psutil.STATUS_RUNNING)
    with mock.patch("psutil.Process", return_value=mock_proc):
        task_mgr._safe_kill(module="TEST", pid=test_pid)
    # 验证终止流程
    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()

def test_log_parsing(temp_workspace, task_mgr):
    """测试日志文件解析逻辑"""
    work_dir = temp_workspace / "test_EE"
    work_dir.mkdir()

    test_log = Path("test_main_CSP_12345.log")
    test_log.write_text("PYTHON_PID: 6789")
    tasks = task_mgr.get_related_tasks(work_dir)
    assert len(tasks) == 1
    assert tasks[0]["pid"] == 6789
    assert tasks[0]["module"] == "CSP"
    test_log.unlink()

def test_process_status_check(monkeypatch, task_mgr):
    """模拟不同进程状态"""
    class MockProc:
        def __init__(self, status):
            self._status = status
        def status(self):
            return self._status
    monkeypatch.setattr(psutil.Process, "status", MockProc("running").status)
    assert task_mgr._is_pid_running(1) is True
    monkeypatch.setattr(psutil.Process, "status", MockProc("zombie").status)
    assert task_mgr._is_pid_running(1) is False

def test_log_symlink(iso_fs):
    """测试符号链接创建"""
    work_dir = iso_fs / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    output_log = work_dir / "output.log"
    output_log.write_text("test content")
    std_log = iso_fs / "logs/CSP_123.log"

    # 创建符号链接
    std_log.symlink_to(output_log)
    assert std_log.is_symlink()
    assert std_log.resolve() == output_log
