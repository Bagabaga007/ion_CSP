# ion_CSP 使用指南

## 📖 目录

- [安装](#安装)
- [快速开始](#快速开始)
- [交互模式](#交互模式)
- [脚本调用](#脚本调用)
- [配置文件](#配置文件)
- [EE模块使用](#ee模块使用)
- [CSP模块使用](#csp模块使用)
- [高级功能](#高级功能)
- [故障排除](#故障排除)

## 安装

### 环境要求

- Python 3.11+
- Linux或Docker环境
- 必需的依赖包（详见 [README.md](../README.md#环境要求)）

### 安装方式

#### 方式一：从PyPI安装（推荐）

```bash
pip install ion-csp
```

#### 方式二：从源码安装

```bash
# 克隆仓库
git clone https://github.com/Bagabaga007/ion_CSP.git
cd ion_CSP

# 创建虚拟环境
python -m venv venv
source venv/bin/activate

# 安装依赖
pip install -e .
```

## 快速开始

### 验证安装

```bash
# 检查版本
python -c "import ion_CSP; print(ion_CSP.__version__)"

# 启动交互模式
ion-csp
```

### 第一个示例

使用EE模块从SMILES表格生成离子组合：

```bash
./scripts/main_EE.sh examples/example_1
```

使用CSP模块从离子组合生成晶体结构：

```bash
./scripts/main_CSP.sh examples/example_2
```

## 交互模式

### 启动交互模式

```bash
ion-csp
```

启动后会显示主菜单：

```
=== ion_CSP 主菜单 ===
1. 运行EE模块
2. 运行CSP模块
3. 查看日志
4. 管理进程
5. 退出
请选择操作 [1-5]:
```

### 功能说明

#### 1. 运行EE模块

选择此选项后，系统会提示输入：

- 工作目录路径
- 配置文件路径（可选）

程序将执行以下步骤：

1. SMILES分子式转换
2. 经验公式评估
3. 离子组合生成

#### 2. 运行CSP模块

选择此选项后，系统会提示输入：

- 工作目录路径
- 配置文件路径（可选）

程序将执行以下步骤：

1. 晶体结构生成
2. MLP优化
3. VASP分步优化

#### 3. 查看日志

提供日志浏览功能：

- 分页显示（10条/页）
- 模块过滤（CSP/EE）
- 软链接解析
- 实时更新

#### 4. 管理进程

提供进程管理功能：

- 查看运行中的进程
- 终止指定进程
- 查看进程状态
- 清理僵尸进程

## 脚本调用

### EE模块脚本

#### 基本用法

```bash
./scripts/main_EE.sh <工作目录>
```

#### 示例

```bash
# 使用默认配置
./scripts/main_EE.sh examples/example_1

# 使用自定义配置
./scripts/main_EE.sh examples/example_1 --config my_config.json
```

#### 工作目录结构

```
example_1/
├── config.json          # 配置文件（可选）
├── input.csv            # SMILES输入文件
└── output/              # 输出目录（自动创建）
    ├── converted/       # 转换后的分子结构
    ├── combinations/    # 生成的离子组合
    └── logs/            # 日志文件
```

### CSP模块脚本

#### 基本用法

```bash
./scripts/main_CSP.sh <工作目录>
```

#### 示例

```bash
# 使用默认配置
./scripts/main_CSP.sh examples/example_2

# 使用自定义配置
./scripts/main_CSP.sh examples/example_2 --config my_config.json
```

#### 工作目录结构

```
example_2/
├── config.json          # 配置文件（可选）
├── ions/                # 离子输入文件
│   ├── cation/          # 阳离子结构
│   └── anion/           # 阴离子结构
└── output/              # 输出目录（自动创建）
    ├── generated/       # 生成的晶体结构
    ├── mlp_optimized/   # MLP优化后的结构
    ├── vasp_optimized/  # VASP优化后的结构
    └── logs/            # 日志文件
```

## 配置文件

### 配置文件格式

配置文件使用JSON格式，包含各模块的参数设置。

### EE模块配置

```json
{
  "convert_SMILES": {
    "csv_file": "input.csv",
    "screen": true,
    "charge_screen": "1",
    "group_screen": "OH",
    "group_name": "hydroxyl",
    "group_screen_invert": false,
    "machine": "/path/to/machine/config",
    "resources": "/path/to/resources/config"
  },
  "empirical_estimate": {
    "folders": ["folder1", "folder2"],
    "ratios": [1, 1],
    "sort_by": "density",
    "make_combo_dir": true,
    "target_dir": "output/combinations",
    "num_combos": 100,
    "ion_numbers": [1, 1],
    "update": true
  }
}
```

#### 参数说明

**convert_SMILES**:

- `csv_file`: SMILES输入文件名
- `screen`: 是否进行筛选
- `charge_screen`: 电荷筛选条件
- `group_screen`: 官能团筛选条件
- `group_name`: 分组名称
- `group_screen_invert`: 是否反向筛选
- `machine`: 计算机器配置路径
- `resources`: 计算资源配置路径

**empirical_estimate**:

- `folders`: 输入文件夹列表
- `ratios`: 离子配比
- `sort_by`: 排序方式（density/energy）
- `make_combo_dir`: 是否创建组合目录
- `target_dir`: 目标输出目录
- `num_combos`: 生成组合数量
- `ion_numbers`: 离子数量
- `update`: 是否更新已有组合

### CSP模块配置

```json
{
  "gen_opt": {
    "num_per_group": 500,
    "space_groups_limit": 75,
    "machine": "/path/to/gpu/machine/config",
    "resources": "/path/to/gpu/resources/config",
    "nodes": 1
  },
  "read_mlp_density": {
    "n_screen": 10,
    "molecules_screen": true,
    "detail_log": false
  },
  "vasp_processing": {
    "machine": "/path/to/cpu/machine/config",
    "resources": "/path/to/cpu/resources/config",
    "nodes": 2,
    "molecules_prior": true
  }
}
```

#### 参数说明

**gen_opt**:

- `num_per_group`: 每个空间群生成的结构数量
- `space_groups_limit`: 空间群搜索限制
- `machine`: GPU机器配置路径（建议使用GPU）
- `resources`: GPU资源配置路径
- `nodes`: 占用GPU节点数

**read_mlp_density**:

- `n_screen`: 筛选密度最大的n个结构
- `molecules_screen`: 是否排除离子改变的结构
- `detail_log`: 是否生成详细日志

**vasp_processing**:

- `machine`: CPU机器配置路径
- `resources`: CPU资源配置路径
- `nodes`: 占用CPU节点数
- `molecules_prior`: 是否检查离子结构

### 配置文件示例

完整的配置文件示例请参考：

- [EE模块示例](example_usage_EE.py)
- [CSP模块示例](example_usage_CSP.py)

## EE模块使用

### 工作流程

```
SMILES输入 → 分子式转换 → 结构优化 → 经验评估 → 离子组合生成
```

### 步骤详解

#### 1. 准备输入文件

创建CSV文件，包含SMILES分子式：

```csv
SMILES,Name
CCO,Ethanol
CC(=O)O,Acetic acid
```

#### 2. 配置参数

创建配置文件或使用默认配置。

#### 3. 运行模块

```bash
./scripts/main_EE.sh <工作目录>
```

#### 4. 查看结果

输出文件位于 `<工作目录>/output/` 目录：

- `converted/`: 转换后的分子结构文件
- `combinations/`: 生成的离子组合
- `logs/`: 运行日志

### Python API使用

```python
from ion_CSP.log_and_time import merge_config, get_work_dir_and_config
from src.main_EE import main as main_EE

# 获取工作目录和配置
work_dir, config = get_work_dir_and_config()

# 合并配置
modules = ["convert_SMILES", "empirical_estimate"]
for module in modules:
    config[module] = merge_config(
        default_config=DEFAULT_CONFIG,
        user_config=config,
        key=module
    )

# 运行EE模块
main_EE("my_script.py", work_dir, config)
```

## CSP模块使用

### 工作流程

```
离子输入 → 晶体生成 → MLP优化 → 密度筛选 → VASP优化 → 最终结构
```

### 步骤详解

#### 1. 准备输入文件

准备离子结构文件（POSCAR或XYZ格式）：

```
ions/
├── cation/
│   └── Li.vasp
└── anion/
    └── F.vasp
```

#### 2. 配置参数

创建配置文件，指定：

- 晶体生成参数
- MLP优化参数
- VASP优化参数

#### 3. 运行模块

```bash
./scripts/main_CSP.sh <工作目录>
```

#### 4. 查看结果

输出文件位于 `<工作目录>/output/` 目录：

- `generated/`: 初始生成的晶体结构
- `mlp_optimized/`: MLP优化后的结构
- `vasp_optimized/`: VASP优化后的最终结构
- `logs/`: 运行日志

### Python API使用

```python
from ion_CSP.log_and_time import merge_config, get_work_dir_and_config
from src.main_CSP import main as main_CSP

# 获取工作目录和配置
work_dir, config = get_work_dir_and_config()

# 合并配置
modules = ["gen_opt", "read_mlp_density", "vasp_processing"]
for module in modules:
    config[module] = merge_config(
        default_config=DEFAULT_CONFIG,
        user_config=config,
        key=module
    )

# 运行CSP模块
main_CSP("my_script.py", work_dir, config)
```

## 高级功能

### 任务管理

#### 查看运行中的任务

```python
from ion_CSP.task_manager import TaskManager

tm = TaskManager()
tasks = tm.list_tasks()
for task in tasks:
    print(f"PID: {task['pid']}, Status: {task['status']}")
```

#### 终止任务

```python
tm.terminate_task(pid=12345)
```

### 日志管理

#### 查看日志

```python
from ion_CSP.log_and_time import LogViewer

viewer = LogViewer()
logs = viewer.get_logs(module="CSP", page=1, per_page=10)
for log in logs:
    print(log)
```

#### 过滤日志

```python
# 按模块过滤
csp_logs = viewer.filter_by_module("CSP")

# 按时间过滤
recent_logs = viewer.filter_by_time(hours=24)
```

### 自定义工作流

#### 组合多个模块

```python
# 先运行EE模块生成离子组合
main_EE("step1.py", work_dir_ee, config_ee)

# 再运行CSP模块优化晶体结构
main_CSP("step2.py", work_dir_csp, config_csp)
```

#### 批量处理

```python
import os

# 批量处理多个输入
input_dirs = ["input1", "input2", "input3"]

for input_dir in input_dirs:
    work_dir = os.path.join("batch_output", input_dir)
    main_EE(f"batch_{input_dir}.py", work_dir, config)
```

## 故障排除

### 常见问题

#### 1. 导入错误

**问题**: `ModuleNotFoundError: No module named 'ion_CSP'`

**解决方案**:

```bash
# 确保已安装
pip install -e .

# 或添加到Python路径
export PYTHONPATH="${PYTHONPATH}:/path/to/ion_CSP/src"
```

#### 2. 配置文件错误

**问题**: `ConfigError: Invalid configuration`

**解决方案**:

- 检查JSON格式是否正确
- 验证所有必需参数是否存在
- 参考示例配置文件

#### 3. 计算资源不足

**问题**: `ResourceError: Insufficient resources`

**解决方案**:

- 减少 `num_per_group` 参数
- 增加 `nodes` 数量
- 使用更强大的计算节点

#### 4. 进程卡住

**问题**: 进程长时间无响应

**解决方案**:

```bash
# 查看进程状态
ps aux | grep ion-csp

# 终止进程
kill -9 <PID>

# 清理临时文件
rm -rf logs/
```

### 调试模式

启用详细日志输出：

```python
import logging

logging.basicConfig(level=logging.DEBUG)
```

### 性能优化

#### 1. 使用GPU加速

在配置文件中指定GPU资源：

```json
{
  "gen_opt": {
    "machine": "/path/to/gpu/machine",
    "nodes": 2
  }
}
```

#### 2. 并行处理

增加节点数量以并行处理：

```json
{
  "vasp_processing": {
    "nodes": 4
  }
}
```

#### 3. 减少生成数量

对于快速测试，减少生成数量：

```json
{
  "gen_opt": {
    "num_per_group": 100
  }
}
```

## 最佳实践

### 1. 配置管理

- 为不同项目创建独立的配置文件
- 使用版本控制管理配置文件
- 定期备份重要配置

### 2. 数据管理

- 使用清晰的目录结构
- 定期清理临时文件
- 备份重要结果

### 3. 资源管理

- 根据任务规模合理分配资源
- 监控计算节点使用情况
- 避免同时运行过多任务

### 4. 日志管理

- 定期查看日志文件
- 及时处理错误信息
- 保留关键运行日志

## 更多资源

- [项目主页](https://github.com/Bagabaga007/ion_CSP)
- [测试报告](TEST_REPORT.md)
- [API文档](index.md)
- [问题反馈](https://github.com/Bagabaga007/ion_CSP/issues)

## 联系支持

如有问题或建议，请联系：

- **邮箱**: yangze1995007@163.com
- **GitHub Issues**: https://github.com/Bagabaga007/ion_CSP/issues
