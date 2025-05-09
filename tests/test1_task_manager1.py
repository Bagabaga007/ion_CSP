import os
import time
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock
from ion_CSP.task_manager import TaskManager


@pytest.fixture(scope="session", autouse=True)
def set_working_directory():
    """设置工作目录为项目根目录"""
    project_root = Path(__file__).resolve().parent  # 假设测试文件在 tests 目录下
    os.chdir(project_root)
    yield

@pytest.fixture
def task_manager(tmp_path):
    # 使用临时目录替代工作目录，避免污染真实文件系统
    tm = TaskManager()
    tm.workspace = tmp_path
    tm.log_dir = tmp_path / tm.log_base
    tm.log_dir.mkdir(parents=True, exist_ok=True)
    return tm


def test_task_runner_creates_log_and_pid(monkeypatch, tmp_path, task_manager):
    # 模拟 subprocess.Popen，避免真正启动子进程
    mock_process = MagicMock()
    mock_process.pid = 9999

    def fake_popen(*args, **kwargs):
        return mock_process

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    # 调用 task_runner
    work_dir = tmp_path / "work"
    task_manager.task_runner("CSP", str(work_dir))

    # 检查日志目录下是否生成了符号链接（symlink）
    symlink_path = task_manager.log_dir / f"CSP_{mock_process.pid}.log"
    assert symlink_path.exists()
    assert symlink_path.is_symlink()


def test_get_related_tasks_basic(task_manager, monkeypatch):
    """测试标准日志文件处理"""
    # 验证 log_dir 是 Path 对象
    assert isinstance(task_manager.log_dir, Path), "log_dir 必须是 pathlib.Path 对象"
    log_dir = task_manager.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    print(log_dir)
    # 模拟的时间戳
    now = time.time()

    # 创建模拟的 stat 对象
    mock_stats = {
        8: Mock(st_mtime=now - 8000),
        5: Mock(st_mtime=now - 5000),
        3: Mock(st_mtime=now - 3000),
        1: Mock(st_mtime=now - 1000),
    }

    # 模拟 os.stat 行为
    def mock_os_stat(path, **kwargs):
        try:
            base_name = path.name.split(".")[0]
            pid = int(base_name.split("_")[-1])
            return mock_stats.get(pid, Mock(st_mtime=0))
        except (IndexError, ValueError):
            return Mock(st_mtime=0)

    monkeypatch.setattr("os.stat", mock_os_stat)
    monkeypatch.setattr(task_manager, "_is_pid_running", lambda _: True)

    # 创建日志文件
    for pid in [8, 5, 3, 1]:
        log_file = log_dir / f"CSP_{pid:04d}.log"
        log_file.touch()

    # 调用方法
    tasks = task_manager.get_related_tasks()
    print(tasks)
    # 断言
    assert len(tasks) == 4
    for task in tasks:
        assert task["pid"] in [8, 5, 3, 1]
        assert task["status"] == "Running"


def test_main_menu_navigation(monkeypatch, task_manager):
    """测试主菜单导航"""
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=["3", "q"]))
    with patch("sys.stdout") as mock_stdout:
        task_manager.main_menu()
        output = mock_stdout.getvalue()
        assert "View Logs" in output
        assert "Terminate Tasks" in output


# def test_get_related_tasks_process_status(task_manager, monkeypatch):
#     """测试进程状态判断"""
#     test_file = task_manager.log_dir / "CSP_0001.log"
#     test_file.touch()

#     # 模拟 os.stat
#     mock_stat = Mock(st_mtime=time.time())
#     monkeypatch.setattr("os.stat", lambda _: mock_stat)

#     # 模拟进程状态
#     monkeypatch.setattr(task_manager, "_is_pid_running", lambda pid: pid % 2 == 0)

#     # 执行测试
#     tasks = task_manager.get_related_tasks()

#     # 验证结果
#     assert len(tasks) == 1
#     assert tasks[0]["status"] == "Terminated"  # PID=1 为奇数，应标记为终止


# 你可以继续添加针对 _safe_kill、_is_pid_running 等方法的单元测试
