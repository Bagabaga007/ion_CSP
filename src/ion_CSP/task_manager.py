import os
import re
import sys
import time
import psutil
import logging
import subprocess
import importlib.util
from pathlib import Path
from datetime import datetime


class TaskManager:
    """任务管理器类 - Manages task execution and monitoring"""

    def __init__(self):
        """初始化任务管理器 - Initialize task manager"""
        self.env = "LOCAL"
        self.workspace = Path.cwd()
        self.log_base = self.workspace / "logs"
        self._detect_env()
        self._setup_logging()

    def _detect_env(self):
        """检测运行环境 - Detect execution environment"""
        if Path("/.dockerenv").exists() or "DOCKER" in os.environ:
            self.env = "DOCKER"
            self.workspace = Path("/app")
            self.log_base = Path("/app/logs")
        self.workspace.mkdir(exist_ok=True)
        self.log_base.mkdir(exist_ok=True)

    def _setup_logging(self):
        """配置日志系统 - Configure logging system"""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(self.log_base / "system.log"),
                logging.StreamHandler(),
            ],
        )

    def normalize_path(self, path):
        """路径标准化 - Normalize file path"""
        path = Path(path).resolve()
        if self.env == "DOCKER":
            return str(path.relative_to(self.workspace))
        return str(path)

    def _get_pid(self, module, work_dir):
        """从日志获取PID - Extract PID from log file"""
        log_file = Path(work_dir) / f"main_{module}_console.log"
        if not log_file.exists():
            return None
        try:
            with open(log_file, "r") as f:
                for line in f:
                    if "PYTHON_PID:" in line:
                        return int(line.split(":")[-1].strip())
        except Exception as e:
            logging.error(f"Error reading PID from log: {e}")
        return None

    def task_runner(self, module, work_dir):
        """任务执行器 - Task execution handler"""
        work_dir = Path(work_dir)
        work_dir.mkdir(exist_ok=True)

        console_log = work_dir / f"main_{module}_console.log"
        pid_file = work_dir / "pid.txt"

        # 启动子进程
        cmd = ["python", "-m", f"src.main_{module}", str(work_dir)]

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
        output_log = work_dir / f"main_{module}.py_output.log"
        print(f"Original log file: {output_log}")
        std_log = self.log_base / f"{module}_{process.pid}.log"
        try:
            output_log = output_log.resolve()
            std_log.symlink_to(output_log)
            os.remove(pid_file)
        except FileExistsError:
            os.remove(std_log)
            std_log.symlink_to(output_log)

        logging.info(f"Started {module} module (PID: {process.pid})")
        print(f"Task started (PID: {process.pid})")
        print(f"Normalized log file: {std_log}")

    def view_logs(self, page_size=10):
        """查看日志 - View task logs"""
        log_files = sorted(
            self.log_base.glob("**/*.log"), key=os.path.getmtime, reverse=True
        )
        if not log_files:
            print("No logs found")
            return
        total_files = len(log_files)
        total_pages = (total_files + page_size - 1) // page_size  # 计算总页数

        current_page = 0
        log_pattern = re.compile(r"(CSP|EE)_\d+$")  # 正则匹配规范文件名

        while True:
            # 显示当前页日志
            start_idx = current_page * page_size
            end_idx = start_idx + page_size
            page_logs = log_files[start_idx:end_idx]

            print(f"\nAvailable logs ({total_files} total):")
            for i, log_file in enumerate(page_logs, 1):
                if not log_pattern.match(log_file.stem):
                    continue  # 跳过非标准日志文件
                # 获取实际日志路径
                real_log_path = log_file.resolve(strict=True)
                print(
                    f"{i}) {log_file.name} ({datetime.fromtimestamp(log_file.stat().st_mtime).strftime('%Y-%m-%d %H:%M')}) - {real_log_path}"
                )

            print("\nPage {} of {}".format(current_page + 1, total_pages))
            print("\nOptions:")
            print("n) Next page | p) Previous page | q) Quit")
            print("Enter log number to view: ")

            choice = input().strip()
            if choice == "n" and current_page < total_pages - 1:
                current_page += 1
            elif choice == "p" and current_page > 0:
                current_page -= 1
            elif choice == "q":
                break
            elif choice.isdigit():
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(page_logs):
                    os.system(f"less {page_logs[choice_idx]}")
                else:
                    print("Invalid selection")
            else:
                print("Invalid command")

    def safe_terminate(self):
        """安全终止任务 - Safe task termination"""
        tasks = self.get_related_tasks()
        if not tasks:
            print("No running tasks found")
            return

        # 分页显示任务列表
        total_pages = (len(tasks) + 9) // 10
        current_page = 0
        filter_bool = False

        while True:
            # 显示当前页任务
            start_idx = current_page * 10
            end_idx = start_idx + 10
            page_tasks = tasks[start_idx:end_idx]
            if not filter_bool:
                for i, task in enumerate(page_tasks, 1):
                    status = "Running" if self._is_pid_running(task['pid']) else "Terminated"
                    print(f"{i:2}) [{task['module']}] PID: {task['pid']:5} [{status}] - {task['log']}")

            print("\nPage {} of {}".format(current_page + 1, total_pages))
            print("\nOptions:")
            print("n) Next page | p) Previous page | q) Quit")
            print("Enter module (CSP/EE) to filter | Enter number to terminate process")

            choice = input("Enter action: ").strip().upper()

            # 分页控制
            if choice == 'N' and current_page < total_pages-1:
                current_page += 1
            elif choice == 'P' and current_page > 0:
                current_page -= 1
            elif choice == 'Q':
                break
            elif choice in ('CSP', 'EE'):
                self.view_filtered_tasks(choice)
                filter_bool = True
            elif choice.isdigit():
                try:
                    task_num = int(choice)
                    # 计算全局任务索引
                    global_index = current_page * 10 + (task_num - 1)
                    if 0 <= global_index < len(tasks):
                        selected_index = global_index
                        confirm = input(f"Confirm termination of {tasks[selected_index]['module']} PID {tasks[selected_index]['pid']}? (y/n): ").lower()
                        if confirm == 'y':
                            self._safe_kill(
                                module=tasks[selected_index]["module"],
                                pid=tasks[selected_index]["pid"],
                            )
                            break
                    else:
                        print("Invalid task number")
                except ValueError:
                    print("Please enter a valid number")
            else:
                print("Invalid input")

    def view_filtered_tasks(self, module_filter):
        """应用模块过滤并显示任务 - Filter and display tasks according to module type"""
        all_tasks = self.get_related_tasks()
        filtered = [t for t in all_tasks if t["module"] == module_filter.upper()]

        print("\033c", end="")  # 清屏指令

        print(f"\nFiltered Tasks ({len(filtered)}):")
        for i, task in enumerate(filtered, 1):
            status = "Running" if task["status"] == "Running" else "Terminated"
            print(
                f"{i:2}) [{task['module']}] PID: {task['pid']} [{status}] - {task['log']}"
            )

    def _cleanup_task_files(self, module, pid):
        """清理任务相关文件"""
        log_file = self.log_base / f"{module}_{pid}.log"
        if log_file.exists():
            log_file.unlink()
            print(f"Cleaned up orphaned log: {log_file.name}")

    def _safe_kill(self, module, pid):
        """安全终止进程并清理残留资源 - Safely kill process and cleanup orphan resources"""
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            print(f"Termination signal sent to PID {pid}")

            # 正确处理进程退出状态
            try:
                exit_code = proc.wait(timeout=5)
                print(f"PID {pid} exited with code {exit_code}")
            except psutil.TimeoutExpired:
                print(f"PID {pid} did not exit gracefully, forcing termination...")
                proc.kill()
                exit_code = -1  # 强制终止标记

            # 清理残留文件
            self._cleanup_task_files(module, pid)
            return exit_code
        except psutil.NoSuchProcess:
            print(f"PID {pid} already terminated")
            self._remove_orphaned_log(pid)
            return -2  # 进程不存在标记
        except Exception as e:
            print(f"Error terminating process: {str(e)}")
            return -3  # 其他错误标记

    def _is_pid_running(self, pid):
        """检查进程是否仍在运行 - Check the process status according to PID"""
        try:
            proc = psutil.Process(pid)
            return proc.status() in (psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING)
        except psutil.NoSuchProcess:
            return False

    def get_related_tasks(self):
        """获取实时任务列表并验证状态 - Get relatd tasks list and validate the status"""
        tasks = []
        log_pattern = re.compile(r"(CSP|EE)_\d+$")  # 正则匹配规范文件名

        for log_file in self.log_base.glob("**/*.log"):
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
                    real_log_path = log_file.resolve(strict=True)
                    status = "Running" if self._is_pid_running(pid) else "Terminated"
                    tasks.append(
                        {
                            "pid": pid,
                            "module": module,
                            "log": str(real_log_path),
                            "status": status,
                        }
                    )
            except (ValueError, IndexError) as e:
                logging.error(f"Error parsing log file {log_file}: {e}")
                continue
        return tasks

    def _is_valid_task_pid(self, pid):
        """验证PID是否属于当前程序的任务进程 - Valid the task PID according to log file"""
        try:
            proc = psutil.Process(pid)
            cmdline = " ".join(proc.cmdline())
            
            # 检查模块标识和Python环境
            return (
                "python" in proc.name().lower() and
                ("main_CSP" in cmdline or "main_EE" in cmdline)
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        
    def get_version(self):
        spec = importlib.util.spec_from_file_location("ion_CSP", "src/__init__.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.__version__
    
    def main_menu(self):
        """主菜单循环 - Main menu loop"""
        while True:
            os.system("clear" if os.name == "posix" else "cls")
            print("========== Task Execution System ==========")
            print(f'Current Version: {self.get_version()}')
            print(f"Current Environment: {self.env}")
            print(f"Current Directory: {self.workspace}")
            print(f"Log Base Directory: {self.log_base}")
            print("=" * 50)
            print("1) Run EE Module")
            print("2) Run CSP Module")
            print("3) View Logs")
            print("4) Terminate Task")
            print("q) Exit")
            print("=" * 50)

            choice = input("Please enter operation: ").strip()
            if choice == "1":
                work_dir = input("Enter EE working directory: ").strip()
                self.task_runner("EE", work_dir)
            elif choice == "2":
                work_dir = input("Enter CSP working directory: ").strip()
                self.task_runner("CSP", work_dir)
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
