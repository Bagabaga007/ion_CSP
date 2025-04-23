#!/usr/bin/env python3
import os
import sys
import time
import signal
import logging
import subprocess
from pathlib import Path
from datetime import datetime


class TaskManager:
    def __init__(self):
        self.env = "LOCAL"
        self.workspace = Path.cwd()
        self.log_base = self.workspace / "logs"
        self._detect_env()
        self._setup_logging()

    def _detect_env(self):
        """环境检测"""
        if Path("/.dockerenv").exists() or "DOCKER" in os.environ:
            self.env = "DOCKER"
            self.workspace = Path("/app")
            self.log_base = Path("/app/logs")
        self.workspace.mkdir(exist_ok=True)
        self.log_base.mkdir(exist_ok=True)

    def _setup_logging(self):
        """日志配置"""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(self.log_base / "system.log"),
                logging.StreamHandler(),
            ],
        )

    def normalize_path(self, path):
        """路径标准化"""
        path = Path(path).resolve()
        if self.env == "DOCKER":
            return str(path.relative_to(self.workspace))
        return str(path)

    def _get_pid(self, module, work_dir):
        """获取进程PID"""
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
        """任务执行器"""
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
            std_log.symlink_to(output_log)
            os.remove(pid_file)
        except FileExistsError:
            os.remove(std_log)
            std_log.symlink_to(output_log)

        logging.info(f"Started {module} module (PID: {process.pid})")
        print(f"Task started (PID: {process.pid})")
        print(f"Normalized log file: {std_log}")

    def terminate_task(self, pid):
        """终止任务"""
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            print(f"Successfully terminated PID {pid}")
        except ProcessLookupError:
            print(f"No process found with PID {pid}")
        except Exception as e:
            print(f"Error terminating process: {e}")

    def view_logs(self, page_size=10):
        """查看日志"""
        log_files = sorted(
            self.log_base.glob("**/*.log"), key=os.path.getmtime, reverse=True
        )
        if not log_files:
            print("No logs found")
            return
        total_files = len(log_files)
        total_pages = (total_files + page_size - 1) // page_size  # 计算总页数
        
        current_page = 0
        while True:
            start_index = current_page * page_size
            end_index = start_index + page_size
            print("\nAvailable logs:")

            # 显示当前页的日志文件
            for i, f in enumerate(log_files[start_index:end_index], start_index + 1):
                print(
                    f"{i}) {f.name} ({datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')})"
                )

            print("\nPage {} of {}".format(current_page + 1, total_pages))
            if current_page > 0:
                print("Enter 'p' to go to the previous page.")
            if current_page < total_pages - 1:
                print("Enter 'n' to go to the next page.")
            print("Enter log number to view (q to cancel): ")

            choice = input().strip()
            if choice.isdigit():
                choice_index = int(choice) - 1
                if 0 <= choice_index < total_files:
                    os.system(f"less {log_files[choice_index]}")
                else:
                    print("Invalid selection")
            elif choice == "n" and current_page < total_pages - 1:
                current_page += 1
            elif choice == "p" and current_page > 0:
                current_page -= 1
            elif choice == "q":
                break
            else:
                print("Invalid command")

    def main_menu(self):
        """主菜单循环"""
        while True:
            os.system("clear" if os.name == "posix" else "cls")
            print("========== Task Execution System ==========")
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

            choice = input("Please select one of the operation: ").strip()
            if choice == "1":
                work_dir = input("Enter EE working directory: ").strip()
                self.task_runner("EE", work_dir)
            elif choice == "2":
                work_dir = input("Enter CSP working directory: ").strip()
                self.task_runner("CSP", work_dir)
            elif choice == "3":
                self.view_logs()
            elif choice == "4":
                pid = input("Enter PID to terminate: ").strip()
                if pid.isdigit():
                    self.terminate_task(int(pid))
                else:
                    print("Invalid PID format")
            elif choice == "q":
                print("Exiting system...")
                sys.exit(0)
            else:
                print("Invalid selection")
            input("\nPress Enter to continue...")


if __name__ == "__main__":
    manager = TaskManager()
    manager.main_menu()
