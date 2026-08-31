# 测试分层说明

本项目采用四层测试金字塔结构，确保代码质量和系统稳定性。

## 目录结构

```
tests/
├── conftest.py              # 全局pytest配置和清理fixture
├── unit/                    # 单元测试（70%）
│   ├── test_gen_opt.py
│   ├── test_mlp_opt.py
│   ├── test_task_manager.py
│   ├── test_vasp_processing.py
│   └── ...
├── integration/             # 集成测试（20%）
│   ├── test_run_main_CSP.py
│   ├── test_run_main_EE.py
│   └── ...
├── ci/                      # 配置项测试（5%）
│   ├── test_configuration_item.py
│   └── test_config_validation.py
└── system/                  # 系统测试（5%）
    └── test_csp_workflow.py
```

## 测试层级

### 1. 单元测试（Unit Tests）- 70%

**目标**：测试单个函数或类的功能，快速、独立、可重复

**位置**：`tests/unit/`

**运行方式**：

```bash
# 运行所有单元测试
pytest tests/unit/

# 运行特定模块的单元测试
pytest tests/unit/test_gen_opt.py

# 使用标记运行
pytest -m unit
```

**特点**：

- ✅ 执行速度快（< 1秒/测试）
- ✅ 不依赖外部系统
- ✅ 使用 mock 隔离依赖
- ✅ 覆盖率目标：> 95%

### 2. 集成测试（Integration Tests）- 20%

**目标**：测试多个模块之间的交互和数据流

**位置**：`tests/integration/`

**运行方式**：

```bash
# 运行所有集成测试
pytest tests/integration/

# 运行特定工作流的集成测试
pytest tests/integration/test_run_main_CSP.py

# 使用标记运行
pytest -m integration
```

**特点**：

- ⏱️ 执行速度中等（1-5秒/测试）
- 🔗 测试模块间协作
- 🎭 部分使用 mock
- ✅ 覆盖率目标：> 85%

### 3. 配置项测试（Configuration Item Tests / CI Tests）- 5%

**目标**：对独立软件模块进行黑盒测试，覆盖文档审查、静态分析、内存测试、功能测试、性能测试、兼容性测试、维护性测试

**位置**：`tests/ci/`

**运行方式**：

```bash
# 运行所有配置项测试
pytest tests/ci/

# 使用标记运行
pytest -m ci
```

**测试类别**：

1. **文档审查测试**：验证README、LICENSE、CHANGELOG等文档存在
2. **静态分析测试**：检查Python语法、导入结构
3. **内存使用测试**：检测内存泄漏、大数据处理
4. **功能测试**：验证所有必需模块可导入
5. **性能测试**：测试关键操作的性能指标
6. **兼容性测试**：验证Python版本、依赖包、平台兼容性
7. **维护性测试**：检查代码文档字符串、版本号、日志配置
8. **可移植性测试**：验证无硬编码路径、使用相对导入

**特点**：

- ⚡ 执行速度快到中等
- 🔍 黑盒测试方法
- 📋 覆盖软件质量管理要求

### 4. 系统测试（System Tests）- 5%

**目标**：端到端测试整个工作流，使用真实或接近真实的数据

**位置**：`tests/system/`

**运行方式**：

```bash
# 运行所有系统测试（跳过需要真实环境的测试）
pytest tests/system/

# 运行包括慢速测试
pytest tests/system/ --run-slow

# 使用标记运行
pytest -m system
```

**特点**：

- 🐌 执行速度慢（> 10秒/测试）
- 🌐 可能需要真实环境
- 📊 验证完整工作流
- ⚠️ 通常在CI/CD中单独运行

## 测试清理机制

测试过程中会在以下位置创建临时的 `logs/` 文件夹：

- 项目根目录：项目根目录下的 `logs/`
- 测试目录：项目根目录下的 `tests/logs/`

**自动清理**：

- ✅ 所有测试结束后自动清理（通过 `tests/conftest.py`）
- ✅ logs/ 文件夹已在 `.gitignore` 中，不会提交到版本控制
- ✅ 使用 `shutil.rmtree(ignore_errors=True)` 安全删除

**手动清理**（如需要）：

```bash
# 清理项目根目录的logs
rm -rf logs/

# 清理tests目录的logs
rm -rf tests/logs/
```

## 快速命令

```bash
# 运行所有测试（不包括慢速测试）
pytest

# 运行所有测试（包括慢速测试）
pytest --run-slow

# 只运行快速测试（单元测试 + 配置项测试）
pytest -m "unit or ci"

# 运行除系统测试外的所有测试
pytest -m "not system"

# 查看测试覆盖率
pytest --cov=src/ion_CSP --cov-report=html

# 运行特定标记的测试
pytest -m integration
pytest -m "not slow"
```

## 测试标记说明

| 标记 | 说明 | 示例 |
| ------ | ------ | ------ |
| `@pytest.mark.unit` | 单元测试 | `@pytest.mark.unit` |
| `@pytest.mark.integration` | 集成测试 | `@pytest.mark.integration` |
| `@pytest.mark.ci` | 配置项测试 | `@pytest.mark.ci` |
| `@pytest.mark.system` | 系统测试 | `@pytest.mark.system` |
| `@pytest.mark.slow` | 慢速测试 | `@pytest.mark.slow` |

## CI/CD 建议

```yaml
# .github/workflows/test.yml 示例
jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - run: pytest -m "unit or ci" --cov

  integration-tests:
    runs-on: ubuntu-latest
    steps:
      - run: pytest -m integration --cov

  system-tests:
    runs-on: ubuntu-latest
    # 只在主分支运行
    if: github.ref == 'refs/heads/main'
    steps:
      - run: pytest -m system --run-slow
```

## 当前测试统计（V2.3.0 历史基线）

> 本节数字来自 V2.3.0 测试报告，不代表当前工作树的最新收集结果；最新修复和验证记录见 CHANGELOG.md。

- **总测试数**：420个
- **整体覆盖率**：99.39%
- **单元测试**：338个（80%）
- **集成测试**：48个（11%）
- **配置项测试**：31个（7%）
- **系统测试**：3个（1%）
- **跳过测试**：0个 ✅（所有测试都能实际运行）

### 测试执行时间

- 单元测试：~15秒
- 集成测试：~12秒
- 配置项测试：~75秒（包含性能测试）
- 系统测试：~3秒
- **总计**：~60秒

### 测试质量指标

- ✅ **无跳过测试** - 所有测试都能实际运行
- ✅ **高覆盖率** - 99.39%的代码覆盖率
- ✅ **快速执行** - 完整测试套件1分钟内完成
- ✅ **自动清理** - 测试后自动清理临时文件
- ✅ **跨平台兼容** - 支持命令行和VSCode运行

## 添加新测试

### 单元测试

```python
# tests/unit/test_my_module.py
import pytest

@pytest.mark.unit
def test_my_function():
    result = my_function(input_data)
    assert result == expected_output
```

### 集成测试

```python
# tests/integration/test_my_workflow.py
import pytest
from unittest.mock import patch

@pytest.mark.integration
@patch("module.dependency")
def test_workflow_integration(mock_dep):
    # 测试多个模块协作
    pass
```

### 配置项测试

```python
# tests/ci/test_my_ci.py
import pytest

@pytest.mark.ci
class TestMyConfigurationItem:
    """配置项测试类"""

    def test_functionality(self):
        # 黑盒功能测试
        pass

    def test_performance(self):
        # 性能测试
        pass
```

### 系统测试

```python
# tests/system/test_my_workflow.py
import pytest

@pytest.mark.system
@pytest.mark.slow
def test_end_to_end_workflow():
    # 端到端测试
    pass
```
