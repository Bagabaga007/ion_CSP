import os
import re
import sys
import time
import psutil
import logging
import subprocess
import importlib.util
from pathlib import Path


class TaskManager:
    """任务管理器类 - Manages task execution and monitoring"""

    def __init__(self):
        """初始化任务管理器 - Initialize task manager"""
        self.env = "LOCAL"
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.workspace = Path.cwd()
        self.log_base = "logs"
        self.log_dir = self.workspace / self.log_base
        self.version = self._get_version()
        self._detect_env()
        self._setup_logging()

    def __repr__(self):
        return f"Taskmanager(version={self.version}, env={self.env}, project_root={self.project_root}, workspace={self.workspace}, log_base={self.log_base}, log_dir={self.log_dir})"

    def _get_version(self):
        """版本获取"""
        try:
            return importlib.metadata.version("ion_CSP")
        except importlib.metadata.PackageNotFoundError:
            logging.error("Package not found")
            return "unknown"
        except Exception as e:
            logging.error(f"Version detection failed: {e}")
            return "unknown"


    def _detect_env(self):
        """检测运行环境 - Detect execution environment"""
        if Path("/.dockerenv").exists() or "DOCKER" in os.environ:
            self.env = "DOCKER"
            self.workspace = Path("/app")
            self.log_dir = Path("/app/logs")
        conda_env = os.getenv("CONDA_DEFAULT_ENV")
        env_msg = conda_env if conda_env else "Not Conda Env"
        self.envs = f"{self.env} ({env_msg})"
        self.workspace.mkdir(exist_ok=True)

    def _setup_logging(self):
        """配置日志系统 - Configure logging system"""
        self.log_dir = self.workspace / self.log_base
        self.log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(self.log_dir / "system.log"),
                logging.StreamHandler(),
            ],
        )

    def _cleanup_task_files(self, module: str, pid: int):
        """清理任务相关文件"""
        log_file = self.log_dir / f"{module}_{pid}.log"
        if log_file.exists():
            log_file.unlink()
            print(f"Cleaned up orphaned log: {log_file.name}")
            
    def _safe_kill(self, module: str, pid: int):
        """安全终止进程并清理残留资源 - Safely kill process and cleanup orphan resources"""
        try:
            proc = psutil.Process(pid)
            # 先尝试优雅终止并等待进程推出
            proc.terminate()
            print(f"Termination signal sent to PID {pid}")

            # 正确处理进程退出状态
            try:
                exit_code = proc.wait(timeout=5)
                print(f"PID {pid} exited with code {exit_code}")
                exit_code = 0  # 正常退出
            except psutil.TimeoutExpired:
                print(f"PID {pid} did not exit gracefully, forcing termination...")
                proc.kill()
                exit_code = -1  # 强制终止标记
            # 清理残留文件
            self._cleanup_task_files(module, pid)
            input("\nPress Enter to continue...")
            return exit_code

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            print(f"PID {pid} already terminated")
            self._cleanup_task_files(module, pid)
            input("\nPress Enter to continue...")
            return -2  # 进程不存在标记
        except Exception as e:
            print(f"Error terminating process: {str(e)}")
            input("\nPress Enter to continue...")
            return -3  # 其他错误标记

    def _is_pid_running(self, pid: int):
        """检查进程是否仍在运行 - Check the process status according to PID"""
        try:
            proc = psutil.Process(pid)
            return proc.status() in (psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING)
        except psutil.NoSuchProcess:
            return False

    def _is_valid_task_pid(self, pid: int):
        """验证PID是否属于当前程序的任务进程 - Valid the task PID according to log file"""
        try:
            proc = psutil.Process(pid)
            cmdline = " ".join(proc.cmdline())

            # 检查模块标识和Python环境
            return "python" in proc.name().lower() and (
                "main_CSP" in cmdline or "main_EE" in cmdline
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _display_tasks(self, tasks, total, current_page, total_pages, function):
        """标准化任务显示 - Standardized tasks display"""
        display = "logs" if function == "view" else "tasks" 
        print(f"\033cPage {current_page}/{total_pages} ({total} {display})\n")
        if function == "kill":
            for i, task in enumerate(tasks, 1):
                print(
                    f"{i:3}) [{task['module']}] PID:{task['pid']:5} [{task['status']}] - {task['real_log']}"
                )
        elif function == "view":
            for i, task in enumerate(tasks, 1):
                print(
                    f"{i:3}) [{task['module']}] - {task['real_log']}"
                )
        else:
            raise ValueError(f"Not supported function {function}. Available function: 'view' and 'kill' ")
        # 分页控制
        print("\nOptions:")
        if function == "view":
            print("n) Next page | p) Previous page | f) Filter | q) Quit")
            print("Enter number to view log in detail")
        elif function == "kill":
            print("n) Next page | p) Previous page | f) Filter | k) Kill | q) Quit")
        else:
            raise ValueError("Unexpected function parameter")

    def _paginate_tasks(self, tasks, function, page_size=10):
        """通用分页函数 - Universal tasks pagination"""
        total = len(tasks)
        pages = (total + page_size - 1) // page_size
        current_page = 0
        filter_bool = False
        
        while True:
            start = current_page * page_size
            end = start + page_size
            page_tasks = tasks[start:end]
            if not filter_bool:
            # 显示当前页内容
                self._display_tasks(page_tasks, total, current_page+1, pages, function)
            
            choice = input().strip().upper()
            if choice == 'N' and current_page < pages-1:
                current_page += 1
            elif choice == 'P' and current_page > 0:
                current_page -= 1
            elif function == "kill" and choice == "K":
                try:
                    task_num = input("Enter task number to kill: ").strip()
                    if task_num.isdigit() and 1 <= int(task_num) <= 10:
                        # 计算全局任务索引
                        global_index = current_page * 10 + (int(task_num) - 1)
                    else: 
                        raise ValueError
                    if 0 <= global_index < len(tasks):
                        selected_index = global_index
                        confirm = input(
                            f"Confirm termination of {tasks[selected_index]['module']} PID {tasks[selected_index]['pid']}? (y/n): "
                        ).lower()
                        if confirm == "y":
                            self._safe_kill(
                                module=tasks[selected_index]["module"],
                                pid=tasks[selected_index]["pid"],
                            )
                            break
                    else:
                        print("Invalid task number")
                        input("\nPress Enter to continue...")
                except (ValueError, TypeError):
                    print("Please enter a valid number")
                    input("\nPress Enter to continue...")
            elif function == "view" and choice.isdigit() and 1<= int(choice) <=10:
                # 计算全局任务索引
                global_index = current_page * 10 + (int(choice) - 1)
                if 0 <= global_index < len(tasks):
                    selected_index = global_index
                    os.system(f"less {tasks[selected_index]['real_log']}")
                else:
                    print("Invalid selection")
                    input("\nPress Enter to continue...")
            elif choice == 'Q':
                break
            elif choice == 'F':
                filter_module = input("Please enter a valid module name (CSP or EE)\n")
                if filter_module in ("CSP", "EE"):
                    self.view_filtered_tasks(filter_module, function)
                    break
                else:
                    print("Invalid task number")
                    input("\nPress Enter to continue...")
            else:
                print("Invalid command")
                input("\nPress Enter to continue...")

    def task_runner(self, module: str, work_dir: str):
        """任务执行器 - Task execution handler"""
        work_dir = Path(work_dir)
        if not os.path.exists(work_dir):
            print(f"Work directory {work_dir} does not exist")
            return

        console_log = work_dir / f"main_{module}_console.log"
        pid_file = work_dir / "pid.txt"

        # 动态加载模块
        module_name = f"ion_CSP.run.main_{module}"
        spec = importlib.util.find_spec(module_name)
        if not spec:
            raise ImportError(f"Module {module_name} not found")
        # 启动子进程
        cmd = [sys.executable, "-m", module_name, str(work_dir)]

        with open(console_log, "w") as f:
            process = subprocess.Popen(
                cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid if os.name != "nt" else None,
            )

        # 等待PID文件创建
        time.sleep(1)
        try:
            with open(pid_file, "w") as f:
                f.write(str(process.pid))
        except Exception as e:
            logging.error(f"Error writing PID file: {e}")
            process.terminate()
            return
        # 创建符号链接
        output_log = work_dir / f"main_{module}_output.log"
        print(f"Original log file: {output_log}")
        std_log = Path(self.log_dir) / f"{module}_{process.pid}.log"
        try:
            output_log = output_log.resolve()
            std_log.symlink_to(output_log)
            os.remove(pid_file)
        except FileExistsError:
            os.remove(std_log)
            std_log.symlink_to(output_log)

        print('Starting task ......')
        time.sleep(6)
        logging.info(f"Started {module} module (PID: {process.pid})")
        print(f"Task started (PID: {process.pid})")
        print(f"Normalized log file: {std_log}")

    def view_logs(self, page_size: int = 10):
        """查看日志 - View task logs"""
        log_tasks = []
        log_pattern = re.compile(r"(CSP|EE)_\d+$")  # 正则匹配规范文件名
        for log_file in self.log_dir.glob("**/*.log"):
            if not log_pattern.match(log_file.stem):
                continue
            try:
                file_path = log_file.resolve(strict=True)
                if not os.path.exists(file_path):
                    os.remove(log_file)
                    continue
                mtime = file_path.stat().st_mtime
                log_tasks.append({
                    "pid": 0,  # 日志无PID
                    "module": log_file.stem.split("_")[0].upper(),
                    "real_log": str(file_path),
                    "mtime": mtime,
                    "log_name": log_file.name,
                    "status": "Static"
                })
            except Exception as e:
                logging.error(f"Error processing {log_file}: {e}")
                continue
        self._paginate_tasks(log_tasks, function="view", page_size=page_size)

    def safe_terminate(self):
        """安全终止任务 - Safe task termination"""
        tasks = self.get_related_tasks()
        if not tasks:
            print("No running tasks found")
            input("\nPress Enter to continue...")
            return

        # 分页显示任务列表
        filter_bool = False
        while True:
            print("\033c", end="")  # 清屏指令
            # 显示当前页任务
            print(f"\nRunning tasks ({len(tasks)} in total):")
            if not filter_bool:
                self._paginate_tasks(tasks, function="kill")
                break

    def view_filtered_tasks(self, module_filter: str, function: str):
        """带分页的过滤任务显示"""
        all_tasks = self.get_related_tasks()
        filtered = [t for t in all_tasks if t["module"] == module_filter.upper()]
        if not filtered:
            print("No matching tasks found")
            input("\nPress Enter to continue...")
            return
        print(f"\033cFiltered Tasks ({len(filtered)}):")
        self._paginate_tasks(filtered, function)  # 复用通用分页逻辑

    def get_related_tasks(self):
        """获取实时任务列表并验证状态 - Get relatd tasks list and validate the status"""
        tasks = []
        log_pattern = re.compile(r"(CSP|EE)_\d+$")  # 正则匹配规范文件名

        for log_file in self.log_dir.glob("**/*.log"):
            if not log_pattern.match(log_file.stem):
                continue  # 跳过非标准日志文件
            try:
                # 使用正则提取模块和PID
                match = re.match(r"(CSP|EE)_(\d+)$", log_file.stem)
                module = match.group(1).upper()
                pid = int(match.group(2))

                # 验证进程状态
                if self._is_valid_task_pid(pid):
                    # 获取实际日志路径
                    real_log_path = str(log_file.resolve(strict=True))
                    # 获取文件修改时间戳
                    mtime = log_file.stat().st_mtime
                    status = "Running" if self._is_pid_running(pid) else "Terminated"
                    task_info = (
                        {
                            "pid": pid,
                            "module": module,
                            "real_log": real_log_path,
                            "mtime": mtime,
                            "status": status,
                        }
                    )
                    tasks.append(task_info)
            except (ValueError, IndexError) as e:
                logging.error(f"Error parsing log file {log_file}: {e}")
                continue
        # 按修改时间降序排列（最新在前）
        tasks.sort(key=lambda t: t["mtime"], reverse=True)
        return tasks

    def main_menu(self):
        """主菜单循环 - Main menu loop"""
        while True:
            os.system("clear" if os.name == "posix" else "cls")
            print("========== Task Execution System ==========")
            print(f"Current Version: {self.version}")
            print(f"Current Environment: {self.envs}")
            print(f"Current Directory: {self.workspace}")
            print(f"Log Base Directory: {self.log_dir}")
            print("=" * 50)
            print("1) Run EE Module")
            print("2) Run CSP Module")
            print("3) View Logs")
            print("4) Terminate Tasks")
            print("q) Exit")
            print("=" * 50)

            choice = input("Please select an operation: ").strip().lower()
            if choice == "1":
                work_dir = input("Enter EE working directory: ").strip()
                self.task_runner("EE", work_dir)
                input("\nPress Enter to continue...")
            elif choice == "2":
                work_dir = input("Enter CSP working directory: ").strip()
                self.task_runner("CSP", work_dir)
                input("\nPress Enter to continue...")
            elif choice == "3":
                self.view_logs()
            elif choice == "4":
                self.safe_terminate()
            elif choice == "q":
                print("\033c", end="")  # 清屏指令
                sys.exit(0)
            else:
                print("Invalid selection")
                input("\nPress Enter to continue...")


def main():
    manager = TaskManager()
    manager.main_menu()


if __name__ == "__main__":
    main()
