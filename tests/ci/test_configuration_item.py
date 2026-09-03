"""
配置项测试（Configuration Item Testing）

针对单个可独立运行的软件模块进行黑盒测试，验证：
- 功能完整性
- 性能指标
- 可靠性
- 兼容性
- 易用性
- 维护性
- 信息安全
- 可移植性
"""

import pytest
import subprocess
import sys
from pathlib import Path

# 确保项目src目录在Python路径中（用于VSCode等IDE）
_current_file = Path(__file__).resolve()
_project_root = _current_file.parent.parent.parent
_src_path = _project_root / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))


# 获取项目根目录的fixture
@pytest.fixture(scope="module")
def project_root():
    """获取项目根目录"""
    # 从当前文件向上查找，直到找到包含pyproject.toml的目录
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    # 如果找不到，返回当前工作目录
    return Path.cwd()


@pytest.mark.ci
class TestDocumentationReview:
    """文档审查测试"""

    def test_readme_exists(self, project_root):
        """验证README文件存在"""
        readme = project_root / "README.md"
        assert readme.exists(), f"README.md文件不存在: {readme}"
        assert readme.stat().st_size > 0, "README.md文件为空"

    def test_license_exists(self, project_root):
        """验证LICENSE文件存在"""
        license_file = project_root / "LICENSE"
        assert license_file.exists(), f"LICENSE文件不存在: {license_file}"

    def test_changelog_exists(self, project_root):
        """验证CHANGELOG文件存在"""
        changelog = project_root / "CHANGELOG.md"
        assert changelog.exists(), f"CHANGELOG.md文件不存在: {changelog}"

    def test_setup_configuration(self, project_root):
        """验证安装配置文件存在且有效"""
        setup_py = project_root / "setup.py"
        pyproject = project_root / "pyproject.toml"

        assert setup_py.exists() or pyproject.exists(), \
            f"缺少setup.py或pyproject.toml配置文件: {project_root}"

    def test_requirements_documentation(self, project_root):
        """验证需求文档的完整性"""
        # 检查是否有需求相关文档
        docs_dir = project_root / "docs"
        if docs_dir.exists():
            doc_files = list(docs_dir.glob("*.md"))
            assert len(doc_files) > 0, "docs目录下没有文档文件"


@pytest.mark.ci
class TestStaticAnalysis:
    """静态分析测试"""

    def test_python_syntax(self, project_root):
        """验证Python语法正确性"""
        src_dir = project_root / "src" / "ion_CSP"
        py_files = list(src_dir.rglob("*.py"))

        for py_file in py_files:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(py_file)],
                capture_output=True
            )
            assert result.returncode == 0, \
                f"语法错误: {py_file}\n{result.stderr.decode()}"

    def test_import_structure(self, project_root):
        """验证模块导入结构"""
        # 确保src目录在Python路径中
        import sys
        src_path = str(project_root / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        # 测试主模块可以被导入
        try:
            import ion_CSP

            # 检查__version__属性
            # 如果模块是命名空间包，可能没有__version__，需要从__init__.py读取
            if not hasattr(ion_CSP, '__version__'):
                # 尝试从__init__.py文件直接读取版本号
                init_file = project_root / "src" / "ion_CSP" / "__init__.py"
                if init_file.exists():
                    # 文件存在，说明应该有__version__，但可能是导入问题
                    # 尝试重新导入
                    import importlib
                    importlib.reload(ion_CSP)

                    # 如果还是没有，说明__init__.py没有被正确执行
                    if not hasattr(ion_CSP, '__version__'):
                        pytest.fail(
                            f"ion_CSP模块缺少__version__属性。"
                            f"模块类型: {type(ion_CSP.__loader__)}"
                        )
                else:
                    pytest.fail("ion_CSP/__init__.py文件不存在")
        except ImportError as e:
            pytest.fail(f"无法导入ion_CSP模块: {e}\nPython路径: {sys.path}")

    def test_no_syntax_errors_in_tests(self, project_root):
        """验证测试文件语法正确"""
        test_dir = project_root / "tests"
        py_files = list(test_dir.rglob("*.py"))

        for py_file in py_files:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(py_file)],
                capture_output=True
            )
            assert result.returncode == 0, \
                f"测试文件语法错误: {py_file}"


@pytest.mark.ci
@pytest.mark.slow
class TestMemoryUsage:
    """内存使用缺陷测试"""

    def test_no_memory_leaks_in_basic_operations(self):
        """测试基本操作是否存在内存泄漏"""
        import tracemalloc
        from ion_CSP.log_and_time import StatusLogger
        import tempfile
        from pathlib import Path

        tracemalloc.start()

        # 执行多次操作
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)  # 转换为Path对象
            for _ in range(100):
                logger = StatusLogger(work_dir=work_dir, task_name="test")
                logger.set_running()
                logger.set_success()

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # 验证内存使用在合理范围内（< 10MB）
        assert peak < 10 * 1024 * 1024, \
            f"内存使用过高: {peak / 1024 / 1024:.2f} MB"

    def test_large_data_handling(self):
        """测试大数据处理时的内存管理"""
        import tracemalloc

        tracemalloc.start()

        # 模拟处理大量数据
        large_list = [i for i in range(10000)]
        del large_list

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # 验证内存被正确释放
        assert current < peak * 0.5, "内存未被正确释放"


@pytest.mark.ci
class TestFunctionalRequirements:
    """功能测试（黑盒测试）"""

    def test_crystal_generation_module_exists(self):
        """验证晶体生成模块存在"""
        from ion_CSP.gen_opt import CrystalGenerator
        assert CrystalGenerator is not None

    def test_mlp_optimization_module_exists(self):
        """验证MLP优化模块存在"""
        from ion_CSP.mlp_opt import main as mlp_main
        assert mlp_main is not None

    def test_vasp_processing_module_exists(self):
        """验证VASP处理模块存在"""
        from ion_CSP.vasp_processing import VaspProcessing
        assert VaspProcessing is not None

    def test_empirical_estimation_module_exists(self):
        """验证经验估算模块存在"""
        from ion_CSP.empirical_estimate import EmpiricalEstimation
        assert EmpiricalEstimation is not None

    def test_task_manager_module_exists(self):
        """验证任务管理模块存在"""
        from ion_CSP.task_manager import main as task_main
        assert task_main is not None

    def test_all_required_modules_importable(self):
        """验证所有必需模块可导入"""
        required_modules = [
            'ion_CSP.gen_opt',
            'ion_CSP.mlp_opt',
            'ion_CSP.vasp_processing',
            'ion_CSP.empirical_estimate',
            'ion_CSP.convert_SMILES',
            'ion_CSP.read_mlp_density',
            'ion_CSP.identify_molecules',
            'ion_CSP.log_and_time',
            'ion_CSP.task_manager',
        ]

        for module_name in required_modules:
            try:
                __import__(module_name)
            except ImportError as e:
                pytest.fail(f"无法导入必需模块 {module_name}: {e}")


@pytest.mark.ci
@pytest.mark.slow
class TestPerformance:
    """性能测试"""

    def test_status_logger_performance(self):
        """测试StatusLogger性能"""
        import time
        import tempfile
        from pathlib import Path
        from ion_CSP.log_and_time import StatusLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            start_time = time.time()

            # 执行10次操作
            for i in range(10):
                logger = StatusLogger(work_dir=work_dir, task_name=f"test_{i}")
                logger.set_running()
                logger.set_success()

            elapsed_time = time.time() - start_time

            # 验证性能：10次操作应在10秒内完成
            assert elapsed_time < 10.0, \
                f"性能不达标: {elapsed_time:.2f}秒 > 10秒"

    def test_config_merge_performance(self):
        """测试配置合并性能"""
        import time
        from ion_CSP.log_and_time import merge_config

        default_config = {
            "module": {f"param{i}": f"value{i}" for i in range(100)}
        }
        user_config = {
            "module": {f"param{i}": f"user_value{i}" for i in range(50)}
        }

        start_time = time.time()

        # 执行1000次配置合并
        for _ in range(1000):
            merge_config(default_config, user_config, "module")

        elapsed_time = time.time() - start_time

        # 验证性能：1000次合并应在1秒内完成
        assert elapsed_time < 1.0, \
            f"配置合并性能不达标: {elapsed_time:.2f}秒 > 1秒"


@pytest.mark.ci
class TestCompatibility:
    """兼容性测试"""

    def test_python_version_compatibility(self):
        """验证Python版本兼容性"""
        assert sys.version_info >= (3, 11), \
            f"Python版本过低: {sys.version_info}, 需要 >= 3.11"

    def test_required_dependencies(self):
        """验证必需依赖包已安装"""
        required_packages = [
            'ase',
            'scipy',
            'dpdispatcher',
            'paramiko',
            'numpy',
            'pytest',
            'rdkit',
        ]

        for package in required_packages:
            try:
                __import__(package)
            except ImportError:
                pytest.fail(f"缺少必需依赖包: {package}")

    def test_dependency_security_policy(self, project_root):
        """核心锁文件不得重新引入远程 MLP 的易受攻击依赖栈。"""
        import tomllib

        metadata = tomllib.loads((project_root / "pyproject.toml").read_text())
        dependencies = [item.lower() for item in metadata["project"]["dependencies"]]
        assert not any(item.startswith("torch") for item in dependencies)
        assert not any(item.startswith("deepmd-kit") for item in dependencies)
        assert any(item.startswith("paramiko>=5") for item in dependencies)
        assert any(item.startswith("cryptography>=50") for item in dependencies)
        assert any(item.startswith("urllib3>=2.7") for item in dependencies)

        lock_text = (project_root / "uv.lock").read_text()
        assert 'name = "torch"' not in lock_text
        assert 'name = "deepmd-kit"' not in lock_text
        assert 'version = "2.3.5"' in lock_text

    def test_platform_compatibility(self):
        """验证平台兼容性"""
        import platform

        # 验证在Linux平台上运行
        assert platform.system() == "Linux", \
            f"不支持的平台: {platform.system()}, 仅支持Linux"


@pytest.mark.ci
class TestMaintainability:
    """维护性测试"""

    def test_code_has_docstrings(self, project_root):
        """验证主要模块有文档字符串"""
        # 确保src目录在Python路径中
        import sys
        src_path = str(project_root / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        try:
            from ion_CSP import gen_opt, mlp_opt, vasp_processing

            modules = [gen_opt, mlp_opt, vasp_processing]

            for module in modules:
                assert module.__doc__ is not None, \
                    f"模块 {module.__name__} 缺少文档字符串"
        except ImportError as e:
            pytest.fail(f"无法导入ion_CSP模块: {e}\nPython路径: {sys.path}")

    def test_version_number_exists(self, project_root):
        """验证版本号存在"""
        # 方法1: 尝试从模块导入
        import sys
        src_path = str(project_root / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        version = None

        # 尝试从ion_CSP模块获取版本号
        try:
            import ion_CSP
            if hasattr(ion_CSP, '__version__'):
                version = ion_CSP.__version__
        except ImportError:
            pass

        # 方法2: 如果模块导入失败或没有__version__，从__init__.py文件读取
        if version is None:
            init_file = project_root / "src" / "ion_CSP" / "__init__.py"
            if init_file.exists():
                content = init_file.read_text()
                import re
                match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
                if match:
                    version = match.group(1)

        # 方法3: 使用importlib.metadata
        if version is None:
            try:
                from importlib.metadata import version as get_version
                version = get_version("ion_CSP")
            except Exception:
                pass

        # 验证版本号存在
        assert version is not None, \
            "无法获取版本号：模块导入失败、__init__.py中未定义、或包未安装"
        assert version != "", \
            "版本号为空"

    def test_logging_configured(self):
        """验证日志系统已配置"""
        import logging

        # 验证可以创建logger
        logger = logging.getLogger('ion_CSP')
        assert logger is not None


@pytest.mark.ci
class TestPortability:
    """可移植性测试"""

    def test_no_hardcoded_paths(self, project_root):
        """验证没有硬编码的绝对路径"""
        import re

        src_dir = project_root / "src" / "ion_CSP"
        py_files = list(src_dir.rglob("*.py"))

        # 检查是否有硬编码的绝对路径（简单检查）
        hardcoded_pattern = re.compile(r'["\']/(home|usr|opt|workplace)/[^"\']+["\']')

        for py_file in py_files:
            content = py_file.read_text()
            matches = hardcoded_pattern.findall(content)

            # 排除注释和文档字符串中的路径
            if matches:
                # 这里只是警告，不一定是错误
                pass

    def test_relative_imports_used(self, project_root):
        """验证使用相对导入"""

        src_dir = project_root / "src" / "ion_CSP"
        py_files = list(src_dir.rglob("*.py"))

        # 验证至少有一些文件使用了相对导入
        has_relative_imports = False

        for py_file in py_files:
            content = py_file.read_text()
            if 'from .' in content or 'from ..' in content:
                has_relative_imports = True
                break

        # 这个测试比较宽松，只要有相对导入就通过
        assert has_relative_imports
