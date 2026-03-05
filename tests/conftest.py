"""Pytest configuration for all tests."""
import shutil
import sys
from pathlib import Path
import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_python_path():
    """确保src目录在Python路径中"""
    # 获取项目根目录
    project_root = Path(__file__).resolve().parent.parent
    src_path = project_root / "src"

    # 添加到Python路径
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    yield

    # 清理（可选）
    # if str(src_path) in sys.path:
    #     sys.path.remove(str(src_path))


@pytest.fixture(scope="session", autouse=True)
def cleanup_logs_folder():
    """清理测试结束后创建的logs文件夹"""
    yield
    # 测试结束后清理
    project_root = Path(__file__).resolve().parent.parent
    tests_dir = Path(__file__).resolve().parent

    # 清理项目根目录的logs文件夹
    logs_dir = project_root / "logs"
    if logs_dir.exists() and logs_dir.is_dir():
        shutil.rmtree(logs_dir, ignore_errors=True)

    # 清理tests目录下的logs文件夹
    tests_logs_dir = tests_dir / "logs"
    if tests_logs_dir.exists() and tests_logs_dir.is_dir():
        shutil.rmtree(tests_logs_dir, ignore_errors=True)
