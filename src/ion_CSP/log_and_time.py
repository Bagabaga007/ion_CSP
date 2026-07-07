import os
import sys
import time
import yaml
import signal
import inspect
import logging
import argparse
import functools
from pathlib import Path
from dpdispatcher.dlog import dlog
from dpdispatcher import Machine, Resources


def log_and_time(func):
    """
    Decorator for recording log information and script runtime

    :params
        func: The function to be decorated
        
    :return: The decorated function with logging and timing capabilities
    """
    @functools.wraps(func)
    def wrapper(work_dir: Path, *args, **kwargs):
        # 使用inspect获取真实脚本文件名
        module = inspect.getmodule(func)
        script_path = Path(module.__file__ if module else __file__)
        script_name = script_path.stem
        # 获取脚本所在目录, 在该目录下生成日志
        log_file_path = work_dir / f"{script_name}_output.log"
        print(f"Log file path: {log_file_path}")
        # 配置日志记录：为每次调用单独挂载 FileHandler。
        # 不用 logging.basicConfig —— 它在 root logger 已有 handler 后是 no-op，
        # 会导致同一进程内后续被装饰函数的日志错误地写入首个调用的文件。
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        file_handler = logging.FileHandler(str(log_file_path))
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        root_logger.addHandler(file_handler)
        # 获取程序开始执行时的CPU时间和Wall Clock时间
        start_cpu, start_clock = time.process_time(), time.perf_counter()
        # 记录程序开始信息
        logging.info(f"Start running: {script_name}")
        # 调用实际的函数, 如果出现错误, 报错的同时也将错误信息记录到日志中
        result = None
        try:
            result = func(work_dir, *args, **kwargs)
            print(
                f"The script {script_name} has run successfully, and the output content has been recorded in the {script_name}_output.log file in the same directory."
            )
            # 获取程序结束时的CPU时间和Wall Clock时间
            end_cpu, end_clock = time.process_time(), time.perf_counter()
            # 计算CPU时间和Wall Clock时间的差值
            cpu_time, wall_time = end_cpu - start_cpu, end_clock - start_clock
            # 记录程序结束信息
            logging.info(
                f"End running: {script_name}\nWall time: {wall_time:.4f} sec, CPU time: {cpu_time:.4f} sec\n"
            )
            return result
        except Exception as e:
            logging.error(f"Error occurred: {e}", exc_info=True)
            raise
        finally:
            # 无论成功失败都要卸载并关闭本次调用的 handler，避免句柄泄漏与跨调用串写
            file_handler.close()
            root_logger.removeHandler(file_handler)
    return wrapper


def merge_config(default_config, user_config, key):
    """
    Merge default configuration with user-provided configuration for a specific key.

    :params 
        default_config: The default configuration dictionary.
        user_config: The user-provided configuration dictionary.
        key: The key for which the configuration should be merged.

    :return: A merged configuration dictionary for the specified key.
    """
    if key not in default_config:
        raise KeyError(f"Key '{key}' not found in default configuration.")
    if key not in user_config:
        raise KeyError(f"Key '{key}' not found in user configuration.")
    if not isinstance(default_config[key], dict) or not isinstance(user_config.get(key, {}), dict):
        raise TypeError(f"Both default and user configurations for '{key}' must be dictionaries.")
    # 合并两个参数配置，优先使用用户参数配置
    return {**default_config[key], **user_config.get(key, {})}


class StatusLogger:
    """
    A singleton class to log the status of a workflow, including RUNNING, SUCCESS, FAILURE, and KILLED.
    It initializes a logger that writes to a log file and a YAML file to record the status of the workflow.
    The logger captures the process ID and handles termination signals (SIGINT, SIGTERM).
    """
    _name = "WorkflowLogger"
    _instance = None


    def __new__(cls, *args, **kwargs):
        """Ensure that only one instance of StatusLogger is created (Singleton Pattern)"""
        if not cls._instance:
            cls._instance = super(StatusLogger, cls).__new__(cls)
            cls._instance.__init__(*args, **kwargs)
        return cls._instance


    def __init__(self, work_dir: Path, task_name: str):
        """
        Initialize workflow status logger and generate the .log and .yaml file to record the status

        :params
            work_dir: The working directory where the log and yaml files will be created
            task_name: The name of the task to be logged"""
        # 更新task_name（即使是单例，也要更新当前任务名）
        self.task_name = task_name
        self.work_dir = work_dir.resolve()
        log_file = self.work_dir / "workflow_status.log"
        yaml_file = self.work_dir / "workflow_status.yaml"
        log_file.touch(exist_ok=True)
        self.yaml_file = yaml_file

        # 如果已经初始化过，只需要加载当前任务的状态
        if hasattr(self, "initialized"):
            # work_dir 可能变化：若变化则把 FileHandler 指向新的 log 文件，
            # 否则 .log 仍写入首个 work_dir 而 .yaml 写入新目录，导致二者分叉
            current_files = [
                getattr(h, "baseFilename", None) for h in self.logger.handlers
            ]
            if str(log_file) not in current_files:
                for h in list(self.logger.handlers):
                    h.close()
                    self.logger.removeHandler(h)
                file_handler = logging.FileHandler(str(log_file))
                file_handler.setFormatter(
                    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
                )
                self.logger.addHandler(file_handler)
            self._load_task_status()
            return

        # 首次初始化：创建 logger 对象
        self.logger = logging.getLogger("WorkflowLogger")
        self.logger.setLevel(logging.INFO)
        # 清除已有的handlers，避免重复
        self.logger.handlers.clear()
        # 创建文件处理器
        file_handler = logging.FileHandler(str(log_file))
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        file_handler.setFormatter(formatter)
        # 添加处理器到 logger
        self.logger.addHandler(file_handler)
        # 控制日志信息的传播，不将logger的日志信息传播到全局
        self.logger.propagate = False
        # 注册信号处理器（只在首次初始化时注册）
        signal.signal(signal.SIGINT, self._signal_handler)  # 捕捉 Ctrl + C
        signal.signal(signal.SIGTERM, self._signal_handler)  # 捕捉 kill 命令
        # 记录当前进程的 PID
        self.logger.info(f"Process started with PID: {os.getpid()}")
        # 初始化工作状态
        self.initialized = True
        self._init_yaml()


    def _load_task_status(self):
        """Load the status of the current task from yaml file"""
        if os.path.exists(self.yaml_file):
            with open(self.yaml_file, "r") as yaml_file:
                status_info = yaml.safe_load(yaml_file) or {}
                if self.task_name in status_info:
                    self.run_count = status_info[self.task_name]["run_count"]
                    self.current_status = status_info[self.task_name]["current_status"]
                    return

        # 如果任务不存在，初始化新任务
        self.run_count = 0
        self.current_status = "INITIAL"
        self._init_yaml()


    def set_running(self):
        """
        Set the current task status to RUNNING and log the event.
        This method increments the run count and updates the YAML file.
        """
        self.current_status = "RUNNING"
        self.logger.info(f"{self.task_name} Status: {self.current_status}")
        self.run_count += 1
        self._update_yaml()


    def set_success(self):
        """Set the current task status to SUCCESS and log the event"""
        self.current_status = "SUCCESS"
        self.logger.info(f"{self.task_name} Status: {self.current_status}\n")
        self._update_yaml()


    def is_successful(self):
        """Check if the current task status is SUCCESS"""
        return self.current_status == "SUCCESS"


    def set_failure(self):
        """Set the current task status to FAILURE and log the event"""
        self.current_status = "FAILURE"
        self.logger.error(f"{self.task_name} Status: {self.current_status}\n")
        self._update_yaml()


    def _signal_handler(self, signum, _):
        """
        Handle termination signals and log the event
        :params
            signum: The signal number received (e.g., SIGINT, SIGTERM)"""
        if signum == 2:
            self.logger.warning(
                f"Process {os.getpid()} has been interrupted by 'Ctrl + C'\n"
            )
        elif signum == 15:
            self.logger.warning(
                f"Process {os.getpid()} has been killed by 'kill <pid>' order\n"
            )
        else:
            self.logger.warning(
                f"Process {os.getpid()} received signal {signum}. Exiting ...\n"
            )
        self._set_killed()
        sys.exit(0)


    def _set_killed(self):
        """Set the current task status to KILLED and log the event"""
        self.current_status = "KILLED"
        self.logger.warning(f"{self.task_name} Status: {self.current_status}\n")
        self._update_yaml()


    def _init_yaml(self):
        """Initialize the workflow_status.yaml file"""
        # 初始化状态信息
        status_info = {}
        # 读取现有的 .yaml 文件
        if os.path.exists(self.yaml_file):
            with open(self.yaml_file, "r") as yaml_file:
                status_info = yaml.safe_load(yaml_file) or {}
        # 更新或添加当前任务的信息
        if self.task_name not in status_info:
            self.run_count = 0
            self.current_status = "INITIAL"
            status_info[self.task_name] = {
                "run_count": self.run_count,
                "current_status": self.current_status,
            }
        else:
            self.run_count = status_info[self.task_name]["run_count"]
            self.current_status = status_info[self.task_name]["current_status"]
        # 写回更新后的内容
        self._write_yaml(status_info=status_info)


    def _update_yaml(self):
        """Update the workflow_status.yaml file"""
        with open(self.yaml_file, "r") as yaml_file:
            status_info = yaml.safe_load(yaml_file)
        status_info[self.task_name]["run_count"] = self.run_count
        status_info[self.task_name]["current_status"] = self.current_status
        # 写回更新后的内容
        self._write_yaml(status_info=status_info)


    def _write_yaml(self, status_info):
        """Write the status_info into the workflow_status.yaml file"""
        with open(self.yaml_file, "w") as yaml_file:
            yaml.dump(status_info, yaml_file)


def redirect_dpdisp_logging(custom_log_path):
    # 移除所有文件处理器
    for handler in list(dlog.handlers):
        if isinstance(handler, logging.FileHandler):
            dlog.removeHandler(handler)
    # 创建新文件处理器并继承原始格式
    new_handler = logging.FileHandler(custom_log_path)
    # 复制原始处理器的格式
    for h in dlog.handlers:
        if type(h) is logging.StreamHandler:
            new_handler.setFormatter(h.formatter)
            break
    else:
        # 如果没有找到 StreamHandler，设置默认格式
        default_formatter = logging.Formatter("%(message)s")
        new_handler.setFormatter(default_formatter)
    # 添加新处理器
    dlog.addHandler(new_handler)
    dlog.info(f"LOG INIT:dpdispatcher log direct to {custom_log_path}")


def get_work_dir_and_config():
    """
    Get the working directory and user configuration from command line arguments or interactive input.
    If the working directory is not specified, it prompts the user to input it interactively.
    It also reads the configuration from a 'config.yaml' file in the specified directory.
    
    :return: A tuple containing the working directory and the user configuration dictionary.
    """
    parser = argparse.ArgumentParser(
        description="The full workflow of ionic crystal design for a certain ion combination, including generation, mlp optimization, screening, vasp optimization and analysis."
    )
    parser.add_argument(
        "work_dir",
        type=Path,
        nargs="?",  # 使参数变为可选
        default=None,
        help="The working directory to run. If not specified, interactive input will be used",
    )
    args = parser.parse_args()

    # 交互式输入逻辑
    if args.work_dir is None:
        while True:
            raw_path = input(
                "Please enter the working directory: "
            ).strip()
            # 处理 ~ 和解析绝对路径
            work_dir = Path(raw_path).expanduser().resolve()
            if work_dir.exists() and work_dir.is_dir():
                args.work_dir = work_dir
                break
            print(f"Error: Directory '{work_dir}' does not exist. Please try again.")

    # 配置文件读取逻辑
    config_path = os.path.join(args.work_dir, "config.yaml")
    user_config = {}
    try:
        with open(config_path, "r") as file:
            content = file.read()
            user_config = yaml.safe_load(content) or {}
    except FileNotFoundError:
        print(f"Error: config.yaml not found in {args.work_dir}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print("Error parsing YAML file:", file=sys.stderr)
        # 并非所有 YAMLError 都带 problem_mark，缺失时不再二次抛 AttributeError
        problem_mark = getattr(e, "problem_mark", None)
        if problem_mark is not None:
            print(
                f"  Line {problem_mark.line + 1}, Column {problem_mark.column + 1}",
                file=sys.stderr,
            )
        print(f"  Details: {str(e)}", file=sys.stderr)
        sys.exit(1)

    return args.work_dir, user_config


def machine_resources_prep(machine_path: str, resources_path: str):
    """
    Prepare machine and resources configuration files for dpdispatcher.
    :params
        machine_path: The path to save the machine configuration file, which can be in JSON or YAML format.
        resources_path: The path to save the resources configuration file, which can be in JSON or YAML format.
    :return: machine, resources, parent
    1. machine: The machine configuration object.
    2. resources: The resources configuration object.
    3. parent: The parent directory prefix based on the context type (SSHContext or LocalContext).
    """
    # 读取machine.json和resources.json的参数
    if machine_path.endswith(".json"):
        machine = Machine.load_from_json(machine_path)
    elif machine_path.endswith(".yaml"):
        machine = Machine.load_from_yaml(machine_path)
    else:
        raise KeyError("Unsupported machine file type")
    if resources_path.endswith(".json"):
        resources = Resources.load_from_json(resources_path)
    elif resources_path.endswith(".yaml"):
        resources = Resources.load_from_yaml(resources_path)
    else:
        raise KeyError("Unsupported resources file type")
    # 由于dpdispatcher对于远程服务器以及本地运行的forward_common_files的默认存放位置不同，因此需要预先进行判断，从而不改动优化脚本
    machine_inform = machine.serialize()
    context_type = machine_inform.get("context_type")
    batch_type = machine_inform.get("batch_type")
    # Shell + SSHContext 意味着 dpdispatcher 会 ssh 到远程主机后直接运行脚本、
    # 不经过任何作业调度器。若该主机是集群的登录/主节点，计算任务(Gaussian/VASP/MLP)
    # 会直接压在主节点上——这是共享 HPC 上通常被禁止的行为。此处给出明确警告，
    # 但不强制拒绝，因为独立计算服务器(无调度器)使用该组合是合法的。
    if (
        isinstance(context_type, str)
        and context_type == "SSHContext"
        and isinstance(batch_type, str)
        and batch_type.lower() == "shell"
    ):
        logging.warning(
            "Machine config uses batch_type='Shell' with context_type='SSHContext': "
            "tasks will run DIRECTLY on the remote host via ssh without any job scheduler. "
            "If this host is a cluster login/head node, heavy compute (Gaussian/VASP/MLP) "
            "will overload it. Use a scheduler batch_type (Slurm/LSF/PBS/SGE/...) for cluster "
            "submission; only keep 'Shell' when the target is a standalone compute server."
        )
    if context_type == "SSHContext":
        # 如果调用远程服务器，则创建二级目录
        parent = "data/"
    elif context_type == "LocalContext":
        # 如果在本地运行作业，则只在后续创建一级目录
        parent = ""
    else:
        raise KeyError("Unsupported context type in machine configuration")
    return machine, resources, parent
