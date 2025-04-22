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
        log_dir = work_dir / "logs"
        log_dir.mkdir(exist_ok=True)

        console_log = work_dir / f"main_{module}_console.log"
        pid_file = work_dir / "pid.txt"

        # 启动子进程
        cmd = ["python", "-m", f"src.main_{module.lower()}", str(work_dir)]

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
        log_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        std_log = self.log_base / f"{module.upper()}_{log_time}.log"
        try:
            std_log.symlink_to(console_log)
        except FileExistsError:
            os.remove(std_log)
            std_log.symlink_to(console_log)

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

    def view_logs(self):
        """查看日志"""
        log_files = sorted(
            self.log_base.glob("**/*.log"), key=os.path.getmtime, reverse=True
        )
        if not log_files:
            print("No logs found")
            return

        print("\nAvailable logs:")
        for i, f in enumerate(log_files[:10], 1):
            print(
                f"{i}) {f.name} ({datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')})"
            )

        choice = input("\nEnter log number to view (q to cancel): ")
        if choice.isdigit() and 1 <= int(choice) <= len(log_files):
            os.system(f"less {log_files[int(choice) - 1]}")
        else:
            print("Invalid selection")

    def main_menu(self):
        """主菜单循环"""
        while True:
            os.system("clear" if os.name == "posix" else "cls")
            print("=" * 50)
            print(f"Current Environment: {self.env}")
            print(f"Current Directory: {self.workspace}")
            print(f"Log Base Directory: {self.log_base}")
            print("=" * 50)
            print("1) Run EE Module")
            print("2) Run CSP Module")
            print("3) Terminate Task")
            print("4) View Logs")
            print("q) Exit")
            print("=" * 50)

            choice = input("Please select an operation: ").strip().lower()
            if choice == "1":
                work_dir = input("Enter EE working directory: ").strip()
                self.task_runner("EE", work_dir)
            elif choice == "2":
                work_dir = input("Enter CSP working directory: ").strip()
                self.task_runner("CSP", work_dir)
            elif choice == "3":
                pid = input("Enter PID to terminate: ").strip()
                if pid.isdigit():
                    self.terminate_task(int(pid))
                else:
                    print("Invalid PID format")
            elif choice == "4":
                self.view_logs()
            elif choice == "q":
                print("Exiting system...")
                sys.exit(0)
            else:
                print("Invalid selection")
            input("\nPress Enter to continue...")


if __name__ == "__main__":
    manager = TaskManager()
    manager.main_menu()
