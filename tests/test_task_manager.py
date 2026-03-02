import os
import pytest
import psutil
import logging
import importlib
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

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
@patch("logging.FileHandler")
@patch("logging.StreamHandler")
def test_init_detects_docker_env(mock_stream_handler, mock_file_handler, mock_exists):
    mock_exists.return_value = True
    # Configure mock handlers with proper level attribute
    mock_file_handler.return_value.level = logging.INFO
    mock_stream_handler.return_value.level = logging.INFO
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
@patch("logging.FileHandler")
@patch("logging.StreamHandler")
def test_detect_env_docker(mock_stream_handler, mock_file_handler):
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = True
        # Configure mock handlers with proper level attribute
        mock_file_handler.return_value.level = logging.INFO
        mock_stream_handler.return_value.level = logging.INFO
        tm = TaskManager()
        assert tm.env == "DOCKER"
        assert tm.workspace == Path("/app")
        assert tm.log_dir == Path("/app/logs")


def test_detect_env_create_workspace():
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = False
        with patch("pathlib.Path.mkdir") as mock_mkdir:
            tm = TaskManager()
            # mkdir is called twice: once in _detect_env for workspace, once in _setup_logging for log_dir
            assert mock_mkdir.call_count == 2


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
        # mkdir is called twice: once in _detect_env for workspace, once in _setup_logging for log_dir
        assert mock_mkdir.call_count == 2


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
    with patch("logging.FileHandler") as mock_file_handler:
        mock_file_handler.side_effect = PermissionError("Permission denied")
        with patch("tempfile.gettempdir", return_value="/tmp"):
            with patch("ion_CSP.task_manager.TaskManager._setup_fallback_logging"):
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
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.symlink_to")
@patch("ion_CSP.task_manager.Path.unlink")
def test_task_runner_success(
    mock_unlink,
    mock_symlink_to,
    mock_resolve,
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

    mock_resolve.return_value = mock_resolve

    mock_symlink_to.return_value = None

    monkeypatch.setattr("builtins.input", lambda _: None)

    work_dir = task_manager.workspace / "ee_test"
    work_dir.mkdir()

    task_manager.task_runner("EE", str(work_dir))

    # Verify subprocess was called
    assert mock_popen.called

    mock_resolve.assert_called_once_with()

    mock_symlink_to.assert_called_once()

    mock_unlink.assert_called_once_with(missing_ok=True)


@patch("ion_CSP.task_manager.importlib.util.find_spec")
@patch("ion_CSP.task_manager.subprocess.Popen")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.symlink_to")
def test_task_runner_pid_write_failure_fixed(
    mock_symlink_to,
    mock_resolve,
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

    # Mock Path.open to raise PermissionError
    with patch("pathlib.Path.open", side_effect=PermissionError("Permission denied")):
        mock_resolve.return_value = mock_resolve

        mock_symlink_to.return_value = None

        monkeypatch.setattr("builtins.input", lambda _: None)

        work_dir = task_manager.workspace / "ee_test"
        work_dir.mkdir()

        task_manager.task_runner("EE", str(work_dir))

        mock_popen.assert_not_called()


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

    with patch("builtins.print") as mock_print:
        task_manager.task_runner("EE", str(work_dir))
        mock_print.assert_any_call(f"Work directory {work_dir} does not exist")

    mock_open.assert_not_called()
    mock_popen.assert_not_called()
    mock_resolve.assert_not_called()
    mock_symlink_to.assert_not_called()


@patch("ion_CSP.task_manager.importlib.util.find_spec")
@patch("ion_CSP.task_manager.subprocess.Popen")
@patch("ion_CSP.task_manager.Path.resolve")
@patch("ion_CSP.task_manager.Path.symlink_to")
def test_task_runner_pid_write_failure_after_start(
    mock_symlink_to,
    mock_resolve,
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
    mock_proc.terminate = MagicMock()
    mock_popen.return_value = mock_proc

    # First call succeeds (console_log), second call fails (pid_file)
    call_count = [0]
    def mock_open_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call (console_log) succeeds
            return MagicMock(__enter__=MagicMock(return_value=MagicMock()), __exit__=MagicMock(return_value=False))
        else:
            # Second call (pid_file) fails
            raise PermissionError("Permission denied")

    with patch("pathlib.Path.open", side_effect=mock_open_side_effect):
        mock_resolve.return_value = mock_resolve

        mock_symlink_to.return_value = None

        monkeypatch.setattr("builtins.input", lambda _: None)

        work_dir = task_manager.workspace / "ee_test"
        work_dir.mkdir()

        task_manager.task_runner("EE", str(work_dir))

        # Verify process was terminated after PID write failure
        mock_proc.terminate.assert_called_once()

        mock_popen.assert_called_once()

        mock_symlink_to.assert_not_called()


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

        with patch("builtins.input", return_value="q"):
            task_manager.view_logs()

    mock_glob.assert_called_once_with("**/*.log")
    mock_re_compile.assert_called_once_with(r"(CSP|EE)_\d+$")


def test_view_logs_broken_symlink(task_manager):
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.symlink_to("/nonexistent/file.log")

    with patch("builtins.input", return_value="q"):
        task_manager.view_logs()

    # The broken symlink should be removed
    assert not log_file.exists()


def test_view_logs_no_matching_files(task_manager):
    # Ensure log_dir is empty
    for f in task_manager.log_dir.glob("*.log"):
        f.unlink()

    with patch("builtins.input", return_value="q"):
        task_manager.view_logs()


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
    with patch("builtins.input", return_value="q"):
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
        with patch("builtins.input", return_value="q"):
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


def test_get_related_tasks_parse_error(task_manager):
    # Create a log file with invalid PID (non-numeric)
    log_file = task_manager.log_dir / "CSP_abc.log"
    log_file.touch()

    tasks = task_manager.get_related_tasks()

    # File should be skipped due to invalid pattern
    assert len(tasks) == 0

    log_file.unlink()


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
    symlink = task_manager.log_dir / "EE_5678.log"  # Use valid filename pattern
    log_file.touch()
    symlink.symlink_to(log_file)
    mock_glob.return_value = [log_file, symlink]
    mock_resolve.side_effect = [log_file, log_file]
    mock_stat.side_effect = [MagicMock(st_mtime=100), MagicMock(st_mtime=100)]
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern

    # Mock re.match to return proper match objects
    match1 = MagicMock()
    match1.group.side_effect = lambda x: "CSP" if x == 1 else "1234"
    match2 = MagicMock()
    match2.group.side_effect = lambda x: "EE" if x == 1 else "5678"

    with patch("ion_CSP.task_manager.re.match", side_effect=[match1, match2]):
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
    log_file.touch()
    mock_glob.return_value = [log_file]
    mock_resolve.return_value = log_file
    mock_stat.return_value.st_mtime = 100
    mock_pattern = MagicMock()
    mock_pattern.match.return_value = True
    mock_re_compile.return_value = mock_pattern

    # Mock re.match to return proper match object
    match1 = MagicMock()
    match1.group.side_effect = lambda x: "CSP" if x == 1 else "1234"

    with patch("ion_CSP.task_manager.re.match", return_value=match1):
        with patch("logging.error") as mock_error:
            with patch(
                "ion_CSP.task_manager.TaskManager._is_valid_task_pid", return_value=True
            ):
                with patch(
                    "ion_CSP.task_manager.TaskManager._is_pid_running", return_value=True
                ):
                    tasks = task_manager.get_related_tasks()
            assert len(tasks) == 1


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
        with patch("builtins.input", return_value=""):
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
    mock_input.side_effect = ["1", "/tmp/ee", "", "q"]  # Added "" for "Press Enter to continue"
    with patch.object(task_manager, "task_runner") as mock_run:
        with pytest.raises(SystemExit):
            task_manager.main_menu()
        mock_run.assert_called_once_with("EE", "/tmp/ee")


@patch("ion_CSP.task_manager.input")
@patch("ion_CSP.task_manager.os.system")
def test_main_menu_run_csp(mock_system, mock_input, task_manager):
    mock_input.side_effect = ["2", "/tmp/csp", "", "q"]  # Added "" for "Press Enter to continue"
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
    mock_input.side_effect = ["x", "", "q"]  # Added "" for "Press Enter to continue"
    with pytest.raises(SystemExit):
        task_manager.main_menu()


# ==================== 新增测试以提高覆盖率 ====================
@patch("logging.FileHandler")
@patch("logging.StreamHandler")
def test_setup_logging_permission_error_docker(mock_stream_handler, mock_file_handler):
    """Test permission error in Docker environment"""
    with patch("pathlib.Path.exists", return_value=True):
        mock_file_handler.side_effect = PermissionError("Permission denied")
        mock_stream_handler.return_value.level = logging.INFO
        with pytest.raises(PermissionError, match="Insufficient permissions"):
            TaskManager()


@patch("logging.FileHandler")
@patch("logging.StreamHandler")
def test_setup_logging_os_error_docker(mock_stream_handler, mock_file_handler):
    """Test OSError in Docker environment"""
    with patch("pathlib.Path.exists", return_value=True):
        mock_file_handler.side_effect = OSError("Disk full")
        mock_stream_handler.return_value.level = logging.INFO
        with pytest.raises(OSError):
            TaskManager()


def test_display_tasks_invalid_function(task_manager):
    """Test _display_tasks with invalid function parameter"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    with pytest.raises(ValueError, match="Not supported function"):
        task_manager._display_tasks(tasks, 1, 1, 1, "invalid")


def test_paginate_tasks_filter(task_manager):
    """Test _paginate_tasks with filter option"""
    tasks = [
        {"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test1.log"},
        {"module": "EE", "pid": 5678, "status": "Running", "real_log": "/test2.log"},
    ]
    with patch("builtins.input", side_effect=["f", "CSP", "q"]):
        with patch.object(task_manager, "view_filtered_tasks") as mock_filter:
            task_manager._paginate_tasks(tasks, "kill")
            mock_filter.assert_called_once()


def test_paginate_tasks_kill_invalid_number(task_manager):
    """Test _paginate_tasks kill with invalid task number"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    with patch("builtins.input", side_effect=["k", "99", "", "q"]):
        task_manager._paginate_tasks(tasks, "kill")


def test_paginate_tasks_kill_cancel(task_manager):
    """Test _paginate_tasks kill with cancellation"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    with patch("builtins.input", side_effect=["k", "1", "n", "q"]):
        with patch.object(task_manager, "_safe_kill") as mock_kill:
            task_manager._paginate_tasks(tasks, "kill")
            mock_kill.assert_not_called()


def test_paginate_tasks_view_log(task_manager):
    """Test _paginate_tasks view log detail"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    with patch("builtins.input", side_effect=["1", "q"]):
        with patch("ion_CSP.task_manager.os.system") as mock_system:
            task_manager._paginate_tasks(tasks, "view")
            mock_system.assert_called_once_with("less /test.log")


def test_task_runner_symlink_exists(task_manager, monkeypatch):
    """Test task_runner when symlink already exists"""
    with patch("ion_CSP.task_manager.importlib.util.find_spec") as mock_find_spec:
        mock_find_spec.return_value = MagicMock()

        with patch("ion_CSP.task_manager.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            monkeypatch.setattr("builtins.input", lambda _: None)

            work_dir = task_manager.workspace / "test_symlink"
            work_dir.mkdir()

            # Create existing symlink
            std_log = task_manager.log_dir / "EE_12345.log"
            std_log.touch()

            task_manager.task_runner("EE", str(work_dir))

            # Verify symlink was recreated
            assert std_log.exists() or std_log.is_symlink()


def test_view_logs_process_exception(task_manager):
    """Test view_logs with exception during processing"""
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.touch()

    # Mock resolve to raise exception
    with patch("pathlib.Path.resolve", side_effect=Exception("Unexpected error")):
        with patch("builtins.input", return_value="q"):
            with patch("logging.error") as mock_error:
                task_manager.view_logs()
                mock_error.assert_called()

    log_file.unlink()


def test_get_related_tasks_value_error(task_manager):
    """Test get_related_tasks with ValueError during parsing"""
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.touch()

    with patch("ion_CSP.task_manager.re.match", side_effect=ValueError("Parse error")):
        with patch("logging.error") as mock_error:
            tasks = task_manager.get_related_tasks()
            assert len(tasks) == 0

    log_file.unlink()


def test_setup_logging_os_error_local(task_manager):
    """Test OSError in LOCAL environment during logging setup"""
    with patch("logging.FileHandler", side_effect=OSError("Disk full")):
        with pytest.raises(OSError):
            task_manager._setup_logging()


def test_setup_logging_unexpected_error_local(task_manager):
    """Test unexpected error in LOCAL environment during logging setup"""
    with patch("logging.FileHandler", side_effect=RuntimeError("Unexpected")):
        with pytest.raises(RuntimeError):
            task_manager._setup_logging()


def test_setup_fallback_logging_exception(task_manager):
    """Test _setup_fallback_logging when basicConfig fails"""
    with patch("logging.basicConfig", side_effect=Exception("Config failed")):
        with patch("builtins.print") as mock_print:
            task_manager._setup_fallback_logging()
            mock_print.assert_called_once()


def test_paginate_tasks_next_page(task_manager):
    """Test _paginate_tasks navigation to next page"""
    tasks = [{"module": "CSP", "pid": i, "status": "Running", "real_log": f"/test{i}.log"} for i in range(15)]
    with patch("builtins.input", side_effect=["n", "q"]):
        task_manager._paginate_tasks(tasks, "view")


def test_paginate_tasks_prev_page(task_manager):
    """Test _paginate_tasks navigation to previous page"""
    tasks = [{"module": "CSP", "pid": i, "status": "Running", "real_log": f"/test{i}.log"} for i in range(15)]
    with patch("builtins.input", side_effect=["n", "p", "q"]):
        task_manager._paginate_tasks(tasks, "view")


def test_paginate_tasks_invalid_view_selection(task_manager):
    """Test _paginate_tasks with invalid view selection"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    with patch("builtins.input", side_effect=["99", "", "q"]):
        task_manager._paginate_tasks(tasks, "view")


def test_paginate_tasks_invalid_command(task_manager):
    """Test _paginate_tasks with invalid command"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    with patch("builtins.input", side_effect=["z", "", "q"]):  # Added "" for "Press Enter to continue"
        task_manager._paginate_tasks(tasks, "view")


def test_paginate_tasks_filter_invalid_module(task_manager):
    """Test _paginate_tasks filter with invalid module name"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    with patch("builtins.input", side_effect=["f", "INVALID", "", "q"]):
        task_manager._paginate_tasks(tasks, "view")


def test_paginate_tasks_kill_confirm(task_manager):
    """Test _paginate_tasks kill with confirmation"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    with patch("builtins.input", side_effect=["k", "1", "y"]):
        with patch.object(task_manager, "_safe_kill") as mock_kill:
            task_manager._paginate_tasks(tasks, "kill")
            mock_kill.assert_called_once_with(module="CSP", pid=1234)


def test_task_runner_exception_starting_subprocess(task_manager, monkeypatch):
    """Test task_runner when subprocess raises exception"""
    with patch("ion_CSP.task_manager.importlib.util.find_spec") as mock_find_spec:
        mock_find_spec.return_value = MagicMock()

        with patch("ion_CSP.task_manager.subprocess.Popen", side_effect=Exception("Subprocess error")):
            monkeypatch.setattr("builtins.input", lambda _: None)

            work_dir = task_manager.workspace / "test_exception"
            work_dir.mkdir()

            task_manager.task_runner("EE", str(work_dir))


def test_view_logs_file_not_exists(task_manager):
    """Test view_logs when resolved file doesn't exist"""
    log_file = task_manager.log_dir / "CSP_1234.log"
    log_file.touch()

    # Create a real file that resolve returns but doesn't exist
    with patch("pathlib.Path.resolve", return_value=Path("/nonexistent/path.log")):
        with patch("os.path.exists", return_value=False):
            with patch("os.remove") as mock_remove:
                with patch("builtins.input", return_value="q"):
                    task_manager.view_logs()
                    mock_remove.assert_called()

    log_file.unlink()


def test_safe_terminate_with_tasks(task_manager):
    """Test safe_terminate when there are tasks"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    with patch.object(task_manager, "get_related_tasks", return_value=tasks):
        with patch("builtins.input", side_effect=["k", "1", "y"]):
            with patch.object(task_manager, "_safe_kill") as mock_kill:
                task_manager.safe_terminate()
                mock_kill.assert_called_once()


def test_main_function():
    """Test the main() function"""
    with patch("ion_CSP.task_manager.TaskManager") as mock_tm_class:
        mock_tm = MagicMock()
        mock_tm_class.return_value = mock_tm
        from ion_CSP.task_manager import main
        main()
        mock_tm.main_menu.assert_called_once()


def test_display_tasks_kill_invalid_function(task_manager):
    """Test _display_tasks with invalid function parameter in kill mode"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    with pytest.raises(ValueError, match="Not supported function"):
        task_manager._display_tasks(tasks, 1, 1, 1, "invalid_func")


def test_paginate_tasks_kill_invalid_task_number(task_manager):
    """Test _paginate_tasks kill with out-of-range task number"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    # Use "2" which is valid (1-10) but maps to global_index=1 which is >= len(tasks)=1
    with patch("builtins.input", side_effect=["k", "2", "", "q"]):
        task_manager._paginate_tasks(tasks, "kill")


def test_paginate_tasks_view_invalid_selection(task_manager):
    """Test _paginate_tasks view with out-of-range selection"""
    tasks = [{"module": "CSP", "pid": 1234, "status": "Running", "real_log": "/test.log"}]
    # Use "2" which is valid (1-10) but maps to global_index=1 which is >= len(tasks)=1
    with patch("builtins.input", side_effect=["2", "", "q"]):
        task_manager._paginate_tasks(tasks, "view")
