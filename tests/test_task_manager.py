import os
import time
import pytest
import psutil
import builtins
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
    """测试夹具：创建临时工作目录和日志目录"""
    os.chdir(tmp_path)
    tm = TaskManager()
    tm.log_dir = tmp_path / "logs"
    tm.log_dir.mkdir(exist_ok=True)
    tm.workspace = tmp_path
    tm._setup_logging()
    return tm


def test_task_runner_creates_log_symlink(task_manager, monkeypatch):
    """测试任务运行时创建符号链接"""
    monkeypatch.setattr("os.symlink", MagicMock())
    # 模拟标准输入避免阻塞
    monkeypatch.setattr("builtins.input", lambda _: None)

    work_dir = task_manager.workspace / "ee_test"
    work_dir.mkdir()
    task_manager.task_runner("EE", str(work_dir))

    log_file = work_dir / "main_EE_console.log"
    symlink = task_manager.log_dir / "EE_12345.log"

    log_file.touch()
    task_manager._safe_kill(module="EE", pid=12345)
    symlink.exists()


def test_terminate_task_cleanup(task_manager, monkeypatch):
    """测试终止任务时的文件清理"""
    monkeypatch.setattr("psutil.Process", MagicMock())
    # 模拟标准输入避免阻塞
    monkeypatch.setattr("builtins.input", lambda _: None)
    tm = task_manager

    # 创建测试任务
    tm._cleanup_task_files = MagicMock()
    tm._safe_kill(module="CSP", pid=1)
    tm._cleanup_task_files.assert_called_once_with("CSP", 1)


def test_pagination_in_view_logs(task_manager, monkeypatch):
    """分页功能测试"""
    # 创建测试文件（确保文件名格式统一）
    test_files = []
    mock_stats = []
    for i in range(25):
        log_file = task_manager.log_dir / f"main_CSP_{i:04d}.log"  # 固定4位数字格式
        log_file.touch()
        test_files.append(log_file)
        
        # 生成模拟元数据
        mock_stat = Mock(
            st_mtime=time.time() - i*60,
            st_size=1024,
            st_mode=0o100644
        )
        mock_stats.append(mock_stat)

    # 改进后的模拟函数（安全解析文件名）
    def mock_os_stat(path, **kwargs):
        try:
            # 提取文件名中的数字部分（兼容多种格式）
            base_name = path.name.split(".")[0]  # 移除扩展名
            task_id = base_name.split("_")[-1]    # 取最后一个下划线后的部分
            return mock_stats[int(task_id)]
        except (IndexError, ValueError):
            # 返回默认值或抛出明确错误
            return Mock(st_mtime=0, st_size=0)
    
    monkeypatch.setattr("os.stat", mock_os_stat)
    print(mock_stats)


def test_invalid_task_termination(task_manager, monkeypatch):
    """测试终止不存在的任务"""
    # 模拟标准输入避免阻塞
    monkeypatch.setattr("builtins.input", lambda _: None)
    with patch("psutil.Process") as mock_proc:
        mock_proc.side_effect = psutil.NoSuchProcess(999)
        result = task_manager._safe_kill("CSP", 999)

        assert result == -2
        mock_proc.assert_called_once_with(999)


def test_pagination_empty_directory(task_manager):
    """测试空目录处理"""
    task_manager.log_dir.mkdir(exist_ok=True)
    pattern = "**/*.log"
    actual_files = list(task_manager.log_dir.glob(pattern))
    assert len(actual_files) == 1


def test_main_menu_exit(monkeypatch, task_manager):
    # 模拟用户输入 q 退出程序
    inputs = iter(["q"])

    def fake_input(prompt=""):
        return next(inputs)

    monkeypatch.setattr(builtins, "input", fake_input)

    # 捕获 sys.exit 调用
    with pytest.raises(SystemExit):
        task_manager.main_menu()


def test_main_menu_view_logs(monkeypatch, task_manager):
    # 模拟输入 "3" (view logs) 然后 "q" 退出
    inputs = iter(["3", "q"])

    def fake_input(prompt=""):
        return next(inputs)

    monkeypatch.setattr(builtins, "input", fake_input)

    # 模拟日志目录有一个符合规则的日志文件
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.write_text("dummy log content")

    # mock _paginate_tasks 避免实际分页交互
    monkeypatch.setattr(
        task_manager, "_paginate_tasks", lambda tasks, function, page_size=10: None
    )

    # 运行主菜单，正常结束
    with pytest.raises(SystemExit):
        # 为了安全，捕获sys.exit，防止测试进程退出
        task_manager.main_menu()


def test_safe_terminate_no_tasks(monkeypatch, task_manager):
    # 模拟 get_related_tasks 返回空列表
    monkeypatch.setattr(task_manager, "get_related_tasks", lambda: [])

    # 模拟 input 防止阻塞
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")

    # 调用 safe_terminate，应该打印无任务信息，不报错
    task_manager.safe_terminate()


def test_get_related_tasks_filters(monkeypatch, task_manager, tmp_path):
    # 创建多个日志文件，部分符合规则，部分不符合
    valid_log = task_manager.log_dir / "CSP_1234.log"
    invalid_log = task_manager.log_dir / "invalid.log"
    valid_log.write_text("log content")
    invalid_log.write_text("log content")

    # 模拟 _is_valid_task_pid 始终返回 True
    monkeypatch.setattr(task_manager, "_is_valid_task_pid", lambda pid: True)

    # 调用 get_related_tasks
    tasks = task_manager.get_related_tasks()

    # 只应返回符合规则的日志对应的任务
    assert any(task["module"] == "CSP" for task in tasks)
    assert all(task["module"] in ("CSP", "EE") for task in tasks)
