import os
import pytest
import psutil
import logging
import importlib
from pathlib import Path
from unittest.mock import patch, MagicMock

from ion_CSP.task_manager import TaskManager


@pytest.fixture(scope="session", autouse=True)
def set_working_directory():
    project_root = Path(__file__).resolve().parent
    os.chdir(project_root)
    yield
    os.chdir(project_root)


@pytest.fixture
def task_manager(tmp_path):
    os.chdir(tmp_path)
    tm = TaskManager()
    tm.log_dir = tmp_path / "logs"
    tm.log_dir.mkdir(exist_ok=True)
    tm.workspace = tmp_path
    tm._setup_logging()
    tm.version = "test_version"
    return tm


# ==================== 初始化相关测试 ====================
@patch("importlib.metadata.version")
def test_init_initializes_attributes(mock_version, task_manager):
    mock_version.side_effect = importlib.metadata.PackageNotFoundError
    tm = task_manager
    assert tm.env == "LOCAL"
    assert isinstance(tm.project_root, Path)
    assert isinstance(tm.workspace, Path)
    assert isinstance(tm.log_dir, Path)
    assert tm.version == "test_version"


@patch("os.environ", new={})
@patch("pathlib.Path.exists")
def test_init_detects_docker_env(mock_exists):
    mock_exists.return_value = True
    tm = TaskManager()
    assert tm.env == "DOCKER"
    assert tm.workspace == Path("/app")
    assert isinstance(tm.log_dir, Path)


def test_init_detects_conda_env():
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = "myenv"
        tm = TaskManager()
        assert tm.envs == "LOCAL (myenv)"


def test_init_detects_non_conda_env():
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = None
        tm = TaskManager()
        assert tm.envs == "LOCAL (Not Conda Env)"


def test_init_raises_on_detection_error():
    with patch("pathlib.Path.mkdir") as mock_mkdir:
        mock_mkdir.side_effect = PermissionError("Permission denied")
        with pytest.raises(PermissionError):
            TaskManager()


def test_init_raises_on_unexpected_error():
    with patch("pathlib.Path.mkdir") as mock_mkdir:
        mock_mkdir.side_effect = Exception("Unknown error")
        with pytest.raises(Exception):
            TaskManager()


def test_init_logs_initialization():
    with patch("logging.info") as mock_info:
        tm = TaskManager()
        mock_info.assert_any_call("TaskManager initialization finished")


# ==================== 字符串表示测试 ====================
@patch("importlib.metadata.version")
def test_repr(mock_version):
    mock_version.side_effect = importlib.metadata.PackageNotFoundError
    tm = TaskManager()
    assert "version=unknown" in repr(tm)


# ==================== 版本检测测试 ====================
def test_get_version_from_package():
    with patch("importlib.metadata.version") as mock_version:
        mock_version.return_value = "1.0.0"
        tm = TaskManager()
        assert tm.version == "1.0.0"


def test_get_version_package_not_found():
    with patch("importlib.metadata.version") as mock_version:
        mock_version.side_effect = importlib.metadata.PackageNotFoundError
        tm = TaskManager()
        assert tm.version == "unknown"


def test_get_version_other_error():
    with patch("importlib.metadata.version") as mock_version:
        mock_version.side_effect = Exception("Network error")
        tm = TaskManager()
        assert tm.version == "unknown"


# ==================== 环境检测测试 ====================
def test_detect_env_docker():
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = True
        tm = TaskManager()
        assert tm.env == "DOCKER"
        assert tm.workspace == Path("/app")
        assert tm.log_dir == Path("/app/logs")


def test_detect_env_create_workspace():
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = False
        with patch("pathlib.Path.mkdir") as mock_mkdir:
            tm = TaskManager()
            mock_mkdir.assert_called_once_with(exist_ok=True)


def test_detect_env_permission_error():
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = False
        with patch("pathlib.Path.mkdir") as mock_mkdir:
            mock_mkdir.side_effect = PermissionError("Permission denied")
            with pytest.raises(PermissionError):
                TaskManager()


def test_detect_env_os_error():
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = False
        with patch("pathlib.Path.mkdir") as mock_mkdir:
            mock_mkdir.side_effect = OSError("Disk full")
            with pytest.raises(OSError):
                TaskManager()


def test_detect_env_unexpected_error():
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = False
        with patch("pathlib.Path.mkdir") as mock_mkdir:
            mock_mkdir.side_effect = Exception("Unknown error")
            with pytest.raises(Exception):
                TaskManager()


# ==================== 日志系统测试 ====================
def test_setup_logging_creates_log_dir():
    with patch("pathlib.Path.mkdir") as mock_mkdir:
        tm = TaskManager()
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)


def test_setup_logging_sets_up_handlers():
    with patch("logging.getLogger") as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger
        tm = TaskManager()
        mock_logger.setLevel.assert_called_once_with(logging.INFO)
        mock_logger.handlers.clear.assert_called_once()
        assert mock_logger.addHandler.call_count == 2
        assert mock_logger.propagate is False


def test_setup_logging_permission_error_fallback():
    with patch("pathlib.Path.mkdir") as mock_mkdir:
        mock_mkdir.side_effect = PermissionError("Permission denied")
        with patch("tempfile.gettempdir", return_value="/tmp"):
            tm = TaskManager()
            assert tm.log_dir == Path("/tmp/myapp_logs")


def test_setup_logging_os_error():
    with patch("pathlib.Path.mkdir") as mock_mkdir:
        mock_mkdir.side_effect = OSError("Disk full")
        with pytest.raises(OSError):
            TaskManager()


def test_setup_logging_unexpected_error():
    with patch("pathlib.Path.mkdir") as mock_mkdir:
        mock_mkdir.side_effect = Exception("Unknown error")
        with pytest.raises(Exception):
            TaskManager()


def test_setup_fallback_logging():
    with patch("logging.basicConfig") as mock_basic_config:
        tm = TaskManager()
        tm._setup_fallback_logging()
        mock_basic_config.assert_called_once_with(
            level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
        )


# ==================== 任务运行测试 ====================
@patch("ion_CSP.task_manager.importlib.util.find_spec")
@patch("ion_CSP.task_manager.subprocess.Popen")
@patch("ion_CSP.task_manager.Path.open")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.symlink_to")
@patch("ion_CSP.task_manager.Path.unlink")
def test_task_runner_success(
    mock_unlink,
    mock_symlink_to,
    mock_resolve,
    mock_open,
    mock_popen,
    mock_find_spec,
    task_manager,
    monkeypatch,
):
    mock_spec = MagicMock()
    mock_find_spec.return_value = mock_spec

    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.stdout = MagicMock()
    mock_popen.return_value = mock_proc

    mock_file = MagicMock()
    mock_open.return_value.__enter__.return_value = mock_file

    mock_resolve.return_value = mock_resolve

    mock_symlink_to.return_value = None

    monkeypatch.setattr("builtins.input", lambda _: None)

    work_dir = task_manager.workspace / "ee_test"
    work_dir.mkdir()

    task_manager.task_runner("EE", str(work_dir))

    console_log = work_dir / "main_EE_console.log"
    pid_file = work_dir / "pid.txt"
    output_log = work_dir / "main_EE_output.log"
    std_log = task_manager.log_dir / "EE_12345.log"

    mock_open.assert_any_call(console_log, "w")
    mock_open.assert_any_call(pid_file, "w")
    assert mock_open.call_count == 2

    mock_resolve.assert_called_once_with()

    mock_symlink_to.assert_called_once_with(output_log)

    mock_unlink.assert_called_once_with(missing_ok=True)

    log_content = (task_manager.log_dir / "system.log").read_text()
    assert "Started EE module (PID: 12345)" in log_content


@patch("ion_CSP.task_manager.importlib.util.find_spec")
@patch("ion_CSP.task_manager.subprocess.Popen")
@patch("ion_CSP.task_manager.Path.open")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.symlink_to")
def test_task_runner_pid_write_failure_fixed(
    mock_symlink_to,
    mock_resolve,
    mock_open,
    mock_popen,
    mock_find_spec,
    task_manager,
    monkeypatch,
):
    mock_spec = MagicMock()
    mock_find_spec.return_value = mock_spec

    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.stdout = MagicMock()
    mock_popen.return_value = mock_proc

    mock_open.side_effect = PermissionError("Permission denied")

    mock_resolve.return_value = mock_resolve

    mock_symlink_to.return_value = None

    monkeypatch.setattr("builtins.input", lambda _: None)

    work_dir = task_manager.workspace / "ee_test"
    work_dir.mkdir()

    task_manager.task_runner("EE", str(work_dir))

    console_log = work_dir / "main_EE_console.log"
    mock_open.assert_called_once_with(console_log, "w")

    mock_popen.assert_not_called()

    pid_file = work_dir / "pid.txt"
    assert not pid_file.exists()

    std_log = task_manager.log_dir / "EE_12345.log"
    assert not std_log.exists()

    log_content = (task_manager.log_dir / "system.log").read_text()
    assert "Permission denied when writing to" in log_content


@patch("ion_CSP.task_manager.importlib.util.find_spec")
@patch("ion_CSP.task_manager.subprocess.Popen")
@patch("ion_CSP.task_manager.Path.open")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.symlink_to")
def test_task_runner_module_not_found(
    mock_symlink_to,
    mock_resolve,
    mock_open,
    mock_popen,
    mock_find_spec,
    task_manager,
    monkeypatch,
):
    mock_find_spec.return_value = None

    monkeypatch.setattr("builtins.input", lambda _: None)

    work_dir = task_manager.workspace / "ee_test"
    work_dir.mkdir()

    with pytest.raises(ImportError, match="Module ion_CSP.run.main_EE not found"):
        task_manager.task_runner("EE", str(work_dir))

    mock_open.assert_not_called()
    mock_popen.assert_not_called()
    mock_resolve.assert_not_called()
    mock_symlink_to.assert_not_called()


@patch("ion_CSP.task_manager.importlib.util.find_spec")
@patch("ion_CSP.task_manager.subprocess.Popen")
@patch("ion_CSP.task_manager.Path.open")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.symlink_to")
def test_task_runner_workdir_not_exists(
    mock_symlink_to,
    mock_resolve,
    mock_open,
    mock_popen,
    mock_find_spec,
    task_manager,
    monkeypatch,
):
    mock_find_spec.return_value = MagicMock()

    monkeypatch.setattr("builtins.input", lambda _: None)

    work_dir = task_manager.workspace / "nonexistent_dir"

    with patch("sys.stdout") as mock_stdout:
        task_manager.task_runner("EE", str(work_dir))
        mock_stdout.write.assert_any_call(f"Work directory {work_dir} does not exist\n")

    mock_open.assert_not_called()
    mock_popen.assert_not_called()
    mock_resolve.assert_not_called()
    mock_symlink_to.assert_not_called()


@patch("ion_CSP.task_manager.importlib.util.find_spec")
@patch("ion_CSP.task_manager.subprocess.Popen")
@patch("ion_CSP.task_manager.Path.open")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.symlink_to")
def test_task_runner_pid_write_failure_after_start(
    mock_symlink_to,
    mock_resolve,
    mock_open,
    mock_popen,
    mock_find_spec,
    task_manager,
    monkeypatch,
):
    mock_spec = MagicMock()
    mock_find_spec.return_value = mock_spec

    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.stdout = MagicMock()
    mock_popen.return_value = mock_proc

    mock_file = MagicMock()
    mock_open.side_effect = [mock_file, PermissionError("Permission denied")]

    mock_resolve.return_value = mock_resolve

    mock_symlink_to.return_value = None

    monkeypatch.setattr("builtins.input", lambda _: None)

    work_dir = task_manager.workspace / "ee_test"
    work_dir.mkdir()

    task_manager.task_runner("EE", str(work_dir))

    console_log = work_dir / "main_EE_console.log"
    pid_file = work_dir / "pid.txt"

    mock_open.assert_any_call(console_log, "w")
    mock_open.assert_any_call(pid_file, "w")
    assert mock_open.call_count == 2

    mock_popen.assert_called_once()

    mock_symlink_to.assert_not_called()

    log_content = (task_manager.log_dir / "system.log").read_text()
    assert "Error writing PID file" in log_content
    assert "Started EE module (PID: 12345)" in log_content


# ==================== 日志查看测试 ====================
@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.Path.unlink")
def test_view_logs_valid_files(
    mock_unlink, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.touch()
    mock_glob.return_value = [log_file]
    mock_resolve.return_value = log_file
    mock_stat.return_value.st_mtime = 100

    with patch("ion_CSP.task_manager.re.compile") as mock_re_compile:
        mock_pattern = MagicMock()
        mock_pattern.match.return_value = True
        mock_re_compile.return_value = mock_pattern

        task_manager.view_logs()

    mock_glob.assert_called_once_with("**/*.log")
    mock_re_compile.assert_called_once_with(r"(CSP|EE)_\d+$")


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
def test_view_logs_broken_symlink(mock_stat, mock_resolve, mock_glob, task_manager):
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.symlink_to("/nonexistent/file.log")
    mock_glob.return_value = [log_file]
    mock_resolve.side_effect = FileNotFoundError

    with patch("ion_CSP.task_manager.re.compile") as mock_re_compile:
        mock_pattern = MagicMock()
        mock_pattern.match.return_value = True
        mock_re_compile.return_value = mock_pattern

        task_manager.view_logs()

    assert not log_file.exists()


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_view_logs_no_matching_files(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    mock_glob.return_value = []
    task_manager.view_logs()
    mock_glob.assert_called_once_with("**/*.log")
    mock_re_compile.assert_not_called()


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_view_logs_invalid_file_name(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file = task_manager.log_dir / "invalid.log"
    log_file.touch()
    mock_glob.return_value = [log_file]
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = False
    mock_re_compile.return_value = mock_pattern
    task_manager.view_logs()
    mock_re_compile.assert_called_once_with(r"(CSP|EE)_\d+$")


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_view_logs_process_error(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.touch()
    mock_glob.return_value = [log_file]
    mock_resolve.return_value = log_file
    mock_stat.side_effect = Exception("Stat error")
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern
    with patch("logging.error") as mock_error:
        task_manager.view_logs()
        mock_error.assert_called_once()


# ==================== 任务状态获取测试 ====================
@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_get_related_tasks_valid_tasks(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.touch()
    mock_glob.return_value = [log_file]
    mock_resolve.return_value = log_file
    mock_stat.return_value.st_mtime = 100
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern

    with patch(
        "ion_CSP.task_manager.TaskManager._is_valid_task_pid", return_value=True
    ):
        with patch(
            "ion_CSP.task_manager.TaskManager._is_pid_running", return_value=True
        ):
            tasks = task_manager.get_related_tasks()

    assert len(tasks) == 1
    assert tasks[0]["module"] == "CSP"
    assert tasks[0]["pid"] == 1234
    assert tasks[0]["status"] == "Running"


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_get_related_tasks_invalid_pid(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file = task_manager.log_dir / "CSP_999.log"
    log_file.touch()
    mock_glob.return_value = [log_file]
    mock_resolve.return_value = log_file
    mock_stat.return_value.st_mtime = 100
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern

    with patch(
        "ion_CSP.task_manager.TaskManager._is_valid_task_pid", return_value=False
    ):
        tasks = task_manager.get_related_tasks()

    assert len(tasks) == 0


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_get_related_tasks_parse_error(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file = task_manager.log_dir / "CSP_abc.log"
    log_file.touch()
    mock_glob.return_value = [log_file]
    mock_resolve.return_value = log_file
    mock_stat.return_value.st_mtime = 100
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern

    tasks = task_manager.get_related_tasks()

    assert len(tasks) == 0


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_get_related_tasks_mixed_status(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.touch()
    mock_glob.return_value = [log_file]
    mock_resolve.return_value = log_file
    mock_stat.return_value.st_mtime = 100
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern
    with patch(
        "ion_CSP.task_manager.TaskManager._is_valid_task_pid", return_value=True
    ):
        with patch(
            "ion_CSP.task_manager.TaskManager._is_pid_running", return_value=False
        ):
            tasks = task_manager.get_related_tasks()
    assert tasks[0]["status"] == "Terminated"


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_get_related_tasks_no_valid_tasks(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.touch()
    mock_glob.return_value = [log_file]
    mock_resolve.return_value = log_file
    mock_stat.return_value.st_mtime = 100
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern
    with patch(
        "ion_CSP.task_manager.TaskManager._is_valid_task_pid", return_value=False
    ):
        tasks = task_manager.get_related_tasks()
    assert len(tasks) == 0


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_get_related_tasks_multiple_files(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file1 = task_manager.log_dir / "CSP_1234.log"
    log_file2 = task_manager.log_dir / "CSP_5678.log"
    log_file1.touch()
    log_file2.touch()
    mock_glob.return_value = [log_file1, log_file2]
    mock_resolve.side_effect = [log_file1, log_file2]
    mock_stat.side_effect = [MagicMock(st_mtime=100), MagicMock(st_mtime=200)]
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern
    with patch(
        "ion_CSP.task_manager.TaskManager._is_valid_task_pid", return_value=True
    ):
        with patch(
            "ion_CSP.task_manager.TaskManager._is_pid_running", return_value=True
        ):
            tasks = task_manager.get_related_tasks()
    assert len(tasks) == 2
    assert tasks[0]["pid"] == 5678
    assert tasks[1]["pid"] == 1234


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_get_related_tasks_duplicate_pid(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file1 = task_manager.log_dir / "CSP_1234.log"
    log_file2 = task_manager.log_dir / "CSP_1234.log"
    log_file1.touch()
    log_file2.touch()
    mock_glob.return_value = [log_file1, log_file2]
    mock_resolve.side_effect = [log_file1, log_file2]
    mock_stat.side_effect = [MagicMock(st_mtime=100), MagicMock(st_mtime=200)]
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern
    with patch(
        "ion_CSP.task_manager.TaskManager._is_valid_task_pid", return_value=True
    ):
        with patch(
            "ion_CSP.task_manager.TaskManager._is_pid_running", return_value=True
        ):
            tasks = task_manager.get_related_tasks()
    assert len(tasks) == 2


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_get_related_tasks_invalid_file_extension(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file = task_manager.log_dir / "CSP_1234.txt"
    log_file.touch()
    mock_glob.return_value = [log_file]
    mock_resolve.return_value = log_file
    mock_stat.return_value.st_mtime = 100
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = False
    mock_re_compile.return_value = mock_pattern
    tasks = task_manager.get_related_tasks()
    assert len(tasks) == 0


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_get_related_tasks_empty_log_dir(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    mock_glob.return_value = []
    tasks = task_manager.get_related_tasks()
    assert len(tasks) == 0


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_get_related_tasks_symlinked_log_file(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file = task_manager.log_dir / "CSP_1234.log"
    symlink = task_manager.log_dir / "CSP_1234_symlink.log"
    log_file.touch()
    symlink.symlink_to(log_file)
    mock_glob.return_value = [log_file, symlink]
    mock_resolve.side_effect = [log_file, log_file]
    mock_stat.side_effect = [MagicMock(st_mtime=100), MagicMock(st_mtime=100)]
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern
    with patch(
        "ion_CSP.task_manager.TaskManager._is_valid_task_pid", return_value=True
    ):
        with patch(
            "ion_CSP.task_manager.TaskManager._is_pid_running", return_value=True
        ):
            tasks = task_manager.get_related_tasks()
    assert len(tasks) == 2


@patch("ion_CSP.task_manager.Path.glob")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.stat")
@patch("ion_CSP.task_manager.re.compile")
def test_get_related_tasks_invalid_symlink_target(
    mock_re_compile, mock_stat, mock_resolve, mock_glob, task_manager
):
    log_file = task_manager.log_dir / "CSP_1234.log"
    symlink = task_manager.log_dir / "CSP_1234_symlink.log"
    log_file.touch()
    symlink.symlink_to("/nonexistent/file.log")
    mock_glob.return_value = [log_file, symlink]
    mock_resolve.side_effect = [log_file, FileNotFoundError]
    mock_stat.side_effect = [MagicMock(st_mtime=100), MagicMock(st_mtime=100)]
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern
    with patch("logging.error") as mock_error:
        with patch(
            "ion_CSP.task_manager.TaskManager._is_valid_task_pid", return_value=True
        ):
            with patch(
                "ion_CSP.task_manager.TaskManager._is_pid_running", return_value=True
            ):
                tasks = task_manager.get_related_tasks()
        assert len(tasks) == 1
        mock_error.assert_called_once()


# ==================== 过滤任务测试 ====================
@patch("ion_CSP.task_manager.TaskManager.get_related_tasks")
def test_view_filtered_tasks_csp(mock_get_related_tasks, task_manager):
    all_tasks = [
        {"module": "CSP", "real_log": "/csp.log"},
        {"module": "EE", "real_log": "/ee.log"},
    ]
    mock_get_related_tasks.return_value = all_tasks

    with patch("ion_CSP.task_manager.TaskManager._paginate_tasks") as mock_paginate:
        task_manager.view_filtered_tasks("CSP", "view")
        mock_paginate.assert_called_once_with(
            [{"module": "CSP", "real_log": "/csp.log"}], "view"
        )


@patch("ion_CSP.task_manager.TaskManager.get_related_tasks")
def test_view_filtered_tasks_ee(mock_get_related_tasks, task_manager):
    all_tasks = [
        {"module": "CSP", "real_log": "/csp.log"},
        {"module": "EE", "real_log": "/ee.log"},
    ]
    mock_get_related_tasks.return_value = all_tasks

    with patch("ion_CSP.task_manager.TaskManager._paginate_tasks") as mock_paginate:
        task_manager.view_filtered_tasks("EE", "kill")
        mock_paginate.assert_called_once_with(
            [{"module": "EE", "real_log": "/ee.log"}], "kill"
        )


@patch("ion_CSP.task_manager.TaskManager.get_related_tasks")
def test_view_filtered_tasks_no_match(mock_get_related_tasks, task_manager):
    all_tasks = [{"module": "CSP", "real_log": "/csp.log"}]
    mock_get_related_tasks.return_value = all_tasks

    with patch("ion_CSP.task_manager.TaskManager._paginate_tasks") as mock_paginate:
        task_manager.view_filtered_tasks("EE", "view")
        mock_paginate.assert_not_called()


# ==================== 任务终止测试 ====================
@patch("ion_CSP.task_manager.psutil.Process")
def test_is_valid_task_pid_valid(mock_process, task_manager):
    mock_proc = MagicMock()
    mock_proc.name.return_value = "python"
    mock_proc.cmdline.return_value = ["python", "-m", "ion_CSP.run.main_CSP", "/path"]
    mock_process.return_value = mock_proc
    assert task_manager._is_valid_task_pid(123) is True


@patch("ion_CSP.task_manager.psutil.Process")
def test_is_valid_task_pid_invalid_name(mock_process, task_manager):
    mock_proc = MagicMock()
    mock_proc.name.return_value = "bash"
    mock_process.return_value = mock_proc
    assert task_manager._is_valid_task_pid(123) is False


@patch("ion_CSP.task_manager.psutil.Process")
def test_is_valid_task_pid_invalid_cmdline(mock_process, task_manager):
    mock_proc = MagicMock()
    mock_proc.name.return_value = "python"
    mock_proc.cmdline.return_value = ["python", "-m", "other.module"]
    mock_process.return_value = mock_proc
    assert task_manager._is_valid_task_pid(123) is False


@patch("ion_CSP.task_manager.psutil.Process")
def test_is_valid_task_pid_no_such_process(mock_process, task_manager):
    mock_process.side_effect = psutil.NoSuchProcess(999)
    assert task_manager._is_valid_task_pid(999) is False


@patch("ion_CSP.task_manager.psutil.Process")
def test_is_pid_running_running(mock_process, task_manager):
    mock_proc = MagicMock()
    mock_proc.status.return_value = psutil.STATUS_RUNNING
    mock_process.return_value = mock_proc
    assert task_manager._is_pid_running(123) is True


@patch("ion_CSP.task_manager.psutil.Process")
def test_is_pid_running_sleeping(mock_process, task_manager):
    mock_proc = MagicMock()
    mock_proc.status.return_value = psutil.STATUS_SLEEPING
    mock_process.return_value = mock_proc
    assert task_manager._is_pid_running(123) is True


@patch("ion_CSP.task_manager.psutil.Process")
def test_is_pid_running_terminated(mock_process, task_manager):
    mock_proc = MagicMock()
    mock_proc.status.return_value = psutil.STATUS_ZOMBIE
    mock_process.return_value = mock_proc
    assert task_manager._is_pid_running(123) is False


@patch("ion_CSP.task_manager.psutil.Process")
def test_is_pid_running_no_such_process(mock_process, task_manager):
    mock_process.side_effect = psutil.NoSuchProcess(999)
    assert task_manager._is_pid_running(999) is False


@patch("ion_CSP.task_manager.psutil.Process")
def test_safe_kill_graceful_exit(mock_process, task_manager, monkeypatch):
    mock_proc = MagicMock()
    mock_proc.status.return_value = psutil.STATUS_RUNNING
    mock_proc.wait.return_value = 0
    mock_process.return_value = mock_proc
    monkeypatch.setattr("builtins.input", lambda _: None)
    result = task_manager._safe_kill("CSP", 1234)
    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once_with(timeout=5)
    assert result == 0


@patch("ion_CSP.task_manager.psutil.Process")
def test_safe_kill_force_kill(mock_process, task_manager, monkeypatch):
    mock_proc = MagicMock()
    mock_proc.status.return_value = psutil.STATUS_RUNNING
    mock_proc.wait.side_effect = psutil.TimeoutExpired(seconds=5)
    mock_proc.kill = MagicMock()
    mock_process.return_value = mock_proc
    monkeypatch.setattr("builtins.input", lambda _: None)
    result = task_manager._safe_kill("CSP", 1234)
    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once_with(timeout=5)
    mock_proc.kill.assert_called_once()
    assert result == -1


@patch("ion_CSP.task_manager.psutil.Process")
def test_safe_kill_process_not_exists(mock_process, task_manager, monkeypatch):
    mock_process.side_effect = psutil.NoSuchProcess(999)
    monkeypatch.setattr("builtins.input", lambda _: None)
    result = task_manager._safe_kill("CSP", 999)
    mock_process.assert_called_once_with(999)
    assert result == -2


@patch("ion_CSP.task_manager.psutil.Process")
def test_safe_kill_access_denied(mock_process, task_manager, monkeypatch):
    mock_process.side_effect = psutil.AccessDenied()
    monkeypatch.setattr("builtins.input", lambda _: None)
    result = task_manager._safe_kill("CSP", 1234)
    mock_process.assert_called_once_with(1234)
    assert result == -2


@patch("ion_CSP.task_manager.psutil.Process")
def test_safe_kill_unknown_error(mock_process, task_manager, monkeypatch):
    mock_process.side_effect = Exception("Unknown error")
    monkeypatch.setattr("builtins.input", lambda _: None)
    result = task_manager._safe_kill("CSP", 1234)
    mock_process.assert_called_once_with(1234)
    assert result == -3


@patch("ion_CSP.task_manager.Path.unlink")
def test_cleanup_task_files(mock_unlink, task_manager):
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.touch()
    task_manager._cleanup_task_files("CSP", 1234)
    mock_unlink.assert_called_once()


@patch("ion_CSP.task_manager.Path.unlink")
def test_cleanup_task_files_no_file(mock_unlink, task_manager):
    log_file = task_manager.log_dir / "CSP_1234.log"
    assert not log_file.exists()
    task_manager._cleanup_task_files("CSP", 1234)
    mock_unlink.assert_not_called()


@patch("ion_CSP.task_manager.TaskManager.get_related_tasks")
def test_safe_terminate_no_tasks(mock_get_related_tasks, task_manager, monkeypatch):
    mock_get_related_tasks.return_value = []
    monkeypatch.setattr("builtins.input", lambda _: "")
    task_manager.safe_terminate()
    mock_get_related_tasks.assert_called_once()


# ==================== 主菜单测试 ====================
@patch("ion_CSP.task_manager.input")
@patch("ion_CSP.task_manager.os.system")
def test_main_menu_run_ee(mock_system, mock_input, task_manager):
    mock_input.side_effect = ["1", "/tmp/ee", "q"]
    with patch.object(task_manager, "task_runner") as mock_run:
        with pytest.raises(SystemExit):
            task_manager.main_menu()
        mock_run.assert_called_once_with("EE", "/tmp/ee")


@patch("ion_CSP.task_manager.input")
@patch("ion_CSP.task_manager.os.system")
def test_main_menu_run_csp(mock_system, mock_input, task_manager):
    mock_input.side_effect = ["2", "/tmp/csp", "q"]
    with patch.object(task_manager, "task_runner") as mock_run:
        with pytest.raises(SystemExit):
            task_manager.main_menu()
        mock_run.assert_called_once_with("CSP", "/tmp/csp")


@patch("ion_CSP.task_manager.input")
@patch("ion_CSP.task_manager.os.system")
def test_main_menu_view_logs(mock_system, mock_input, task_manager):
    mock_input.side_effect = ["3", "q"]
    with patch.object(task_manager, "view_logs") as mock_view:
        with pytest.raises(SystemExit):
            task_manager.main_menu()
        mock_view.assert_called_once()


@patch("ion_CSP.task_manager.input")
@patch("ion_CSP.task_manager.os.system")
def test_main_menu_terminate_tasks(mock_system, mock_input, task_manager):
    mock_input.side_effect = ["4", "q"]
    with patch.object(task_manager, "safe_terminate") as mock_terminate:
        with pytest.raises(SystemExit):
            task_manager.main_menu()
        mock_terminate.assert_called_once()


@patch("ion_CSP.task_manager.input")
@patch("ion_CSP.task_manager.os.system")
def test_main_menu_exit(mock_system, mock_input, task_manager):
    mock_input.side_effect = ["q"]
    with pytest.raises(SystemExit):
        task_manager.main_menu()


@patch("ion_CSP.task_manager.input")
@patch("ion_CSP.task_manager.os.system")
def test_main_menu_invalid_choice(mock_system, mock_input, task_manager):
    mock_input.side_effect = ["x", "q"]
    with pytest.raises(SystemExit):
        task_manager.main_menu()
