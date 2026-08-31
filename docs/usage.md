# ion_CSP 使用指南

本文档对应当前 2.3.4 源码。ion_CSP 是一套计算工作流编排软件，而不是单一的
晶体预测模型。它包含两条可以串联的工作流：

~~~text
EE:
ions.csv
  → RDKit 生成离子结构
  → Gaussian 几何优化
  → Multiwfn 静电势分析
  → 经验公式排序
  → 生成 combo_n

CSP:
combo_n 中的离子 GJF
  → PyXtal 随机生成晶体
  → Phonopy 原胞化
  → MLP 优化
  → 密度/能量筛选
  → VASP 分步优化与无约束弛豫
  → 导出最佳 POSCAR
~~~

## 安装

### Python 环境

主项目推荐 Python 3.11。使用仓库内 Conda 环境文件安装：

~~~bash
cd /path/to/ion_CSP
conda env create -f environment.yml
conda activate ion-csp-env
python -m pip install -e .
python -c "import ion_CSP; print(ion_CSP.__version__)"
~~~

也可以使用虚拟环境：

~~~bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
~~~

仓库内的 dist 目录可能保留旧版本构建产物。开发或使用当前工作树功能时，应从
项目根目录执行可编辑安装，而不是安装旧 wheel。

### 外部程序

Python 安装不会提供以下程序：

| 程序 | 调用位置 | 用途 |
|---|---|---|
| Gaussian 16：g16、formchk | Gaussian 任务所在计算节点 | EE 离子几何优化 |
| Multiwfn 或 Multiwfn_noGUI | 启动 EE 主进程的环境 | 静电势分析及 LOG→GJF |
| phonopy | 启动 CSP 主进程的环境 | 晶体原胞化与对称化 |
| VASP：vasp_std、mpirun | VASP 任务所在计算节点 | 结构精细优化与最终弛豫 |
| DeePMD/DPA4/MatterSim | MLP 任务所在计算节点 | 初筛阶段的结构优化 |

使用 LocalContext 时，主进程和计算任务在同一台机器上运行，以上程序都必须在
该机器可用。使用 SSHContext 时，Gaussian、MLP、VASP 可以在远程计算节点执行，
但 Multiwfn 和 Phonopy 仍由启动主工作流的环境直接调用。

## 快速开始

### 验证安装

~~~bash
python -c "import ion_CSP; print(ion_CSP.__version__)"
python -m ion_CSP.run.main_EE --help
python -m ion_CSP.run.main_CSP --help
~~~

EE 和 CSP 主入口都只接受一个可选的位置参数 work_dir。未提供时会交互式询问。
主配置固定读取：

~~~text
<work_dir>/config.yaml
~~~

主工作流配置必须是 YAML，并且不支持 --config 参数。dpdispatcher 的 machine
和 resources 文件可以是 YAML 或 JSON。

### 最短运行路径

先将模板复制为工作目录内的 config.yaml，并替换 machine/resources 路径：

~~~bash
mkdir -p runs/ee_demo
cp examples/example_1/ions.csv runs/ee_demo/
cp config/complete_config.yaml runs/ee_demo/config.yaml

# 编辑 runs/ee_demo/config.yaml 后运行
python -m ion_CSP.run.main_EE "$(realpath runs/ee_demo)"

# 对 EE 排名第一的组合运行 CSP
python -m ion_CSP.run.main_CSP   "$(realpath runs/ee_demo/2_density_combos/combo_1)"
~~~

如果只测试 CSP，可以使用已经准备好的离子组合：

~~~bash
mkdir -p runs
cp -a examples/example_2/combo_1 runs/csp_demo

# 必须先替换 runs/csp_demo/config.yaml 中作者机器上的路径
python -m ion_CSP.run.main_CSP "$(realpath runs/csp_demo)"
~~~

不要直接在 examples/example_1_results 或 examples/example_2_results 中启动新任务；
这些目录是用于理解产物格式的历史结果样例。

## 交互模式

安装后运行：

~~~bash
ion-csp
~~~

也可以运行：

~~~bash
python -m ion_CSP
~~~

交互菜单提供：

1. 启动 EE 后台任务
2. 启动 CSP 后台任务
3. 分页查看任务日志
4. 终止本程序启动的任务

任务管理器会在启动命令的当前目录创建 logs/，并建立
CSP_<PID>.log 或 EE_<PID>.log 软链接。若希望日志集中在某个目录，应先切换到该
目录再启动 ion-csp。

ion-csp 本身是交互式菜单，不是带子命令的参数型 CLI；不要使用
ion-csp --help 来期待 argparse 帮助页面。

## 脚本调用

项目提供两个 nohup 后台脚本：

~~~bash
./scripts/main_EE.sh /absolute/path/to/ee_work_dir
./scripts/main_CSP.sh /absolute/path/to/combo_1
~~~

它们分别写入：

~~~text
<work_dir>/main_EE_console.log
<work_dir>/main_CSP_console.log
~~~

脚本只使用第一个参数，不支持 --config。脚本内部使用固定的 python 命令，并且
没有为传给 Python 的工作目录添加 shell 引号，因此带空格的路径应改用前台的
python -m 命令或交互式任务管理器。

首次配置新机器、调度器或外部程序时，推荐前台运行：

~~~bash
python -m ion_CSP.run.main_EE /absolute/path/to/work_dir
python -m ion_CSP.run.main_CSP /absolute/path/to/combo_1
~~~

前台运行更容易定位环境、路径和调度错误。

## 配置文件

### 模板

项目提供：

- config/complete_config.yaml：列出全部常用参数
- config/simple_config.yaml：保留 EE→CSP 串联所需的配置段，其他参数使用默认值
- examples/server/example_local_machine.yaml：本地 dpdispatcher 机器配置
- examples/server/example_local_resources.yaml：本地资源配置
- examples/server/example_remote_machine.yaml：远程 SSH 配置
- examples/server/example_remote_resources.yaml：远程调度资源配置

复制模板后必须替换所有 /your/... 占位路径。examples/example_1 和
examples/example_2 也使用这些占位路径，不能在实际计算中原样使用。

### EE 配置段

~~~yaml
convert_SMILES:
  csv_file: ions.csv
  screen: false
  charge_screen: -1
  group_screen: '[N+](=O)[O-]'
  group_name: nitro
  group_screen_invert: false

  # 没有中央预优化离子库时保持为空
  database_dir: ''

  machine: /absolute/path/cpu_machine.yaml
  resources: /absolute/path/cpu_resources.yaml
  nodes: 1

empirical_estimate:
  folders:
    - charge_2
    - charge_-1
  ratios:
    - 1
    - 2

  # density、nitrogen 或 NC_ratio
  sort_by: density
  make_combo_dir: true

  # 留空使用默认目录；也可以填写相对 EE 工作目录的路径或绝对路径
  target_dir: ''

  num_combos: 20
  ion_numbers:
    - 2
    - 4
  update: true
~~~

参数含义：

| 参数 | 说明 |
|---|---|
| csv_file | 位于 EE 工作目录中的 CSV 文件名 |
| screen | 是否按电荷和官能团进行额外筛选 |
| database_dir | 中央预优化离子库根目录；空字符串表示禁用 |
| folders | 参与组合的 charge_* 文件夹，顺序很重要 |
| ratios | 经验公式使用的各文件夹离子计量比 |
| sort_by | density、nitrogen 或 NC_ratio |
| make_combo_dir | 是否根据排序结果创建 combo_n |
| target_dir | 留空使用默认目录；相对路径按 EE 工作目录解析 |
| num_combos | 最多创建多少个 combo_n |
| ion_numbers | 后续 PyXtal 晶胞中各类离子的实际数量 |
| update | 每次运行 EE 时是否重新生成组合 |

folders、ratios、ion_numbers 必须长度一致、顺序一致，并保持所构造晶体电荷中性。
ion_numbers 可以是 ratios 的整数倍。

target_dir 留空时，默认输出目录为：

~~~text
<EE work_dir>/2_<sort_by>_combos/
~~~

target_dir 也可以填写非空字符串：相对路径按 EE 工作目录解析，绝对路径保持不变。
Python API 同时接受 pathlib.Path 和字符串。

### CSP 配置段

~~~yaml
gen_opt:
  # EE 创建 combo_n 时会自动写入这两个字段
  species:
    - ACEGUL.gjf
    - AGIDOM.gjf
  ion_numbers:
    - 2
    - 4

  num_per_group: 10
  space_groups_limit: 15

  # deepmd、dpa4、dpa4_ion_ft 或 mattersim
  mlp_backend: deepmd
  mlp_python: python
  mlp_model: model.pt
  mlp_device:
  mlp_workers: 0

  machine: /absolute/path/gpu_machine.yaml
  resources: /absolute/path/gpu_resources.yaml
  nodes: 1

read_mlp_density:
  n_screen: 2

  # density：密度从高到低；energy：能量从低到高
  sort_by: density
  molecules_screen: true
  detail_log: false

vasp_processing:
  machine: /absolute/path/cpu_machine.yaml
  resources: /absolute/path/cpu_resources.yaml
  nodes: 1
  molecules_prior: true
~~~

参数含义：

| 参数 | 说明 |
|---|---|
| species | combo 根目录中的离子 GJF 文件名 |
| ion_numbers | 与 species 对应的晶胞离子数量 |
| num_per_group | 每个空间群最多尝试生成的结构数 |
| space_groups_limit | 从空间群 1 搜索到该编号，范围 1–230 |
| mlp_backend | deepmd、dpa4、dpa4_ion_ft 或 mattersim |
| mlp_python | MLP 计算节点上用于运行 mlp_opt.py 的 Python |
| mlp_model | DPA4/MatterSim 模型名或文件；deepmd 使用内置 model.pt |
| mlp_device | 例如 cuda；留空时由后端选择 |
| mlp_workers | 0 表示自动；DPA4/MatterSim 通常设置 1 |
| n_screen | 送入 VASP 的候选结构数 |
| sort_by | density 或 energy |
| molecules_screen | 是否剔除离子结构发生变化的 MLP 结果 |
| molecules_prior | VASP 排序时是否优先保留离子结构完整的结果 |
| nodes | 工作流拆分出的 pop0/pop1/... 并行任务组数量 |

nodes 是工作流层面的任务分组数，不等同于 resources 文件中的 number_node。

### 本地 machine/resources

本地机器配置示例：

~~~yaml
batch_type: Shell
local_root: ./
remote_root: /absolute/path/to/ion-csp-tasks
context_type: LocalContext
remote_profile:
  symlink: false
~~~

CPU 资源示例：

~~~yaml
number_node: 1
cpu_per_node: 16
gpu_per_node: 0
group_size: 1
queue_name: local
~~~

GPU 资源示例：

~~~yaml
number_node: 1
cpu_per_node: 8
gpu_per_node: 1
group_size: 1
queue_name: local
~~~

建议为 CPU 和 GPU 分别保存资源文件。Shell + LocalContext 会直接在当前机器执行
任务；运行前必须确认该机器允许重计算任务，并且相关可执行程序已加入 PATH。

### 远程集群

远程集群通常使用 SSHContext，并将 batch_type 设置为实际调度器，例如 Slurm、
LSF、PBS 或 SGE。不要把真实密码提交到仓库；优先使用 SSH 密钥。

Shell + SSHContext 会通过 SSH 直接在远程主机执行任务，不经过调度器。如果远程
主机是集群登录/主节点，这会把 Gaussian、VASP 或 MLP 直接压在登录节点上。当前
代码会记录警告，但不会强制阻止。共享集群应选择正确的调度器 batch_type。

## EE模块使用

### 输入 CSV

CSV 至少需要以下列：

- SMILES
- Charge
- Refcode 或 Number，至少一个

示例：

~~~csv
SMILES,Refcode,Charge
[NH4+],AMMONIUM,1
O=C([O-])O,FORMATE,-1
~~~

Charge 必须是数值。程序会根据 SMILES 去重，并以 Refcode 或 Number 作为文件名。

### EE 阶段

EE 依次执行：

1. 0_convertion
   - RDKit 将 SMILES 转为 GJF
   - 按电荷建立 charge_* 文件夹
   - 可选复用中央数据库中的同 SMILES、同电荷离子
   - 通过 dpdispatcher 调用 Gaussian
2. 0_estimation
   - Multiwfn 读取 FCHK 进行静电势分析
   - 生成同名 JSON
   - 将 Gaussian LOG 最后一帧转为优化后的 GJF
3. 0_update_combo
   - 按 density、nitrogen 或 NC_ratio 排序
   - 复制候选 GJF/JSON 到 combo_n
   - 自动写入 combo_n/config.yaml 中的 gen_opt.species 和 ion_numbers

运行：

~~~bash
python -m ion_CSP.run.main_EE /absolute/path/to/ee_work_dir
~~~

### EE 输出

~~~text
<EE work_dir>/
├── ions.csv
├── config.yaml
├── 1_1_SMILES_gjf/
│   └── <csv stem>/
│       └── charge_*/
├── 1_2_Gaussian_optimized/
│   ├── charge_*/
│   ├── Optimized/
│   ├── sorted_density.csv
│   ├── sorted_nitrogen.csv
│   └── specific_NC_ratio.csv
├── 2_density_combos/       # sort_by=density
│   └── combo_n/
│       ├── <ion>.gjf
│       ├── <ion>.json
│       └── config.yaml
├── main_EE_output.log
├── workflow_status.yaml
├── workflow_status.log
└── dpdispatcher.log
~~~

sort_by=nitrogen 或 NC_ratio 时，组合目录名相应变为
2_nitrogen_combos 或 2_NC_ratio_combos。

database_dir 为空时禁用中央库。如果启用，它应指向包含
3_For_CSP_module/charge_* 的数据库根目录。命中规范化 SMILES 和电荷的离子会
复用已有 GJF/JSON，从而跳过 Gaussian。

## CSP模块使用

### 输入目录

CSP 工作目录通常是 EE 生成的 combo_n：

~~~text
combo_1/
├── cation.gjf
├── anion.gjf
└── config.yaml
~~~

species 中的每个文件都必须位于 combo 根目录，并且能被 ASE/PyXtal 读取。

运行：

~~~bash
python -m ion_CSP.run.main_CSP /absolute/path/to/combo_1
~~~

### CSP 阶段和输出

| 状态名 | 功能 | 主要输出 |
|---|---|---|
| 1_generation | PyXtal 随机生成并用 Phonopy 原胞化 | 1_generated/primitive_cell/POSCAR_n |
| 1_optimization | MLP 批量优化 | 2_mlp_optimized/CONTCAR_n、OUTCAR_n |
| 2_read_mlp_density | 分子完整性检查和排序 | max_density/ 或 min_energy/ |
| 3_vasp_optimization | VASP 粗优化与精细优化 | 4_vasp_optimized/<结构>/fine/ |
| 3_vasp_relaxation | VASP 最终弛豫和导出 | vasp_density_energy.csv、POSCAR |

完整目录：

~~~text
<combo>/
├── 1_generated/
│   ├── generation.csv
│   └── primitive_cell/
├── 2_mlp_optimized/
│   ├── CONTCAR_n
│   ├── OUTCAR_n
│   ├── max_density/       # density 模式
│   ├── min_energy/        # energy 模式
│   └── primitive_cell/
├── 3_for_vasp_opt/
├── 4_vasp_optimized/
│   └── <筛选值_编号>/
│       └── fine/
│           └── final/
├── vasp_density_energy.csv
├── POSCAR
├── main_CSP_output.log
├── workflow_status.yaml
├── workflow_status.log
└── dpdispatcher.log
~~~

generation.csv 记录各空间群成功生成数及异常。实际生成结构数可能小于
num_per_group × space_groups_limit，因为部分空间群与给定分子/配比不兼容。

MLP 筛选会计算密度并读取 OUTCAR 中的 TOTEN。sort_by=density 时密度从高到低；
sort_by=energy 时能量从低到高。molecules_screen=true 时，只保留能够识别出原始
离子组成的结构。

VASP 阶段依次使用 INCAR_1、INCAR_2 和 INCAR_3。最终 POSCAR 从
Final_Ions_Check=true 的结构中按 Final_Density 选取最大值，并复制：

~~~text
4_vasp_optimized/<结构>/fine/final/CONTCAR
  → <combo>/POSCAR
~~~

若根目录已有 POSCAR，程序会先将其备份为 POSCAR.bak.<时间戳>。

## 日志与断点续跑

每个工作目录包含：

| 文件 | 内容 |
|---|---|
| main_EE_output.log / main_CSP_output.log | 主工作流详细日志和异常栈 |
| main_EE_console.log / main_CSP_console.log | 后台脚本或任务管理器的 stdout/stderr |
| workflow_status.log | 阶段状态变化 |
| workflow_status.yaml | 各阶段状态和运行次数 |
| dpdispatcher.log | 本地/远程任务提交、同步和调度详情 |

阶段状态包括 INITIAL、RUNNING、SUCCESS、FAILURE 和 KILLED。再次运行同一工作流时：

- SUCCESS 阶段自动跳过
- FAILURE、KILLED、RUNNING 或 INITIAL 阶段重新执行
- 某阶段失败后，修复环境或输入，再运行同一命令即可续跑

如果修改了已成功阶段的配置，工作流不会自动发现配置变化。最安全的做法是使用
新的工作目录。确需在原目录重跑时，应先备份 workflow_status.yaml，再把目标阶段
及其所有下游阶段设为非 SUCCESS。

EE 的 update=true 会在常规阶段循环结束后再次运行组合更新，因此首次执行时
0_update_combo 的 run_count 可能为 2，这是当前实现行为。

## Python API使用

### EE

~~~python
from ion_CSP.log_and_time import get_work_dir_and_config, merge_config
from ion_CSP.run.main_EE import DEFAULT_CONFIG, main

work_dir, config = get_work_dir_and_config()
for module in ["convert_SMILES", "empirical_estimate"]:
    config[module] = merge_config(
        default_config=DEFAULT_CONFIG,
        user_config=config,
        key=module,
    )

main(work_dir, config)
~~~

### CSP

~~~python
from ion_CSP.log_and_time import get_work_dir_and_config, merge_config
from ion_CSP.run.main_CSP import DEFAULT_CONFIG, main

work_dir, config = get_work_dir_and_config()
for module in ["gen_opt", "read_mlp_density", "vasp_processing"]:
    config[module] = merge_config(
        default_config=DEFAULT_CONFIG,
        user_config=config,
        key=module,
    )

main(work_dir, config)
~~~

当前 main 签名是 main(work_dir, config)。不要导入 src.main_EE/src.main_CSP，也
不要使用旧的 main(script_name, work_dir, config) 三参数形式。可直接运行：

~~~bash
python docs/example_usage_EE.py /absolute/path/to/ee_work_dir
python docs/example_usage_CSP.py /absolute/path/to/combo_1
~~~

## MLP 后端与元素限制

默认 deepmd 后端使用包内 model/model.pt。该模型只支持 H、C、N、O。当前 deepmd
任务实现固定发送包内 model.pt；配置中的 mlp_model 主要用于 DPA4 和 MatterSim
后端。

包含其他元素的结构可以考虑：

- dpa4
- dpa4_ion_ft
- mattersim

这些后端通常使用独立 Python 环境，并通过 mlp_python 指定计算节点上的解释器。
DPA4/MatterSim 一般将 mlp_workers 设置为 1，避免同一 GPU 重复加载多个大模型。
详见 DPA4_BACKEND.md 和 MATTERSIM_BACKEND.md。

当前 VASP 提交脚本只包含 H、C、N、O 的 POTCAR 拼接逻辑。因此，即使通用 MLP
能够处理 B 等其他元素，在扩展 POTCAR 资源和 VASP shell 脚本之前，完整 CSP
工作流仍只支持 CHNO 体系。

## 故障排除

### ModuleNotFoundError: ion_CSP

确认已经在当前 Python 环境安装项目：

~~~bash
cd /path/to/ion_CSP
python -m pip install -e .
python -c "import ion_CSP; print(ion_CSP.__file__)"
~~~

### 找不到 config.yaml

配置文件必须准确位于：

~~~text
<work_dir>/config.yaml
~~~

文件名不是 config.json，也不能通过 --config 指向其他位置。

### Multiwfn not found

EE 初始化 EmpiricalEstimation 时会检查 Multiwfn_noGUI 或 Multiwfn。确认启动 EE
的环境 PATH 中存在其中一个：

~~~bash
command -v Multiwfn_noGUI
command -v Multiwfn
~~~

即使离子已经预优化，当前 EE 类初始化仍会进行该检查。

### phonopy not found

Phonopy 由 CSP 主进程直接调用：

~~~bash
command -v phonopy
python -m pip show phonopy
~~~

### Gaussian/VASP 无输出

优先检查：

1. dpdispatcher.log
2. main_EE_output.log 或 main_CSP_output.log
3. machine/resources 的 context_type、batch_type 和 remote_root
4. 计算节点 PATH 中的 g16、formchk、mpirun、vasp_std
5. 调度器队列、CPU/GPU 和 module_list 配置

### MLP 候选少于 n_screen

molecules_screen=true 时，离子结构改变的晶体会被排除。如果有效结构少于 n_screen，
当前阶段会报错。应先检查详细日志和结构质量，再根据情况增加生成数量、降低
n_screen，或在明确接受结构变化的前提下关闭 molecules_screen。

### 修改配置后没有重新计算

workflow_status.yaml 中的 SUCCESS 阶段会被跳过。使用新工作目录，或备份状态文件
后将受影响阶段及其下游阶段改为非 SUCCESS。

## 最佳实践

1. 首次接入机器或集群时使用前台 python -m 命令。
2. 对 EE、每个 combo 和每次模型实验使用独立工作目录。
3. 提交前检查 folders、ratios、ion_numbers 的顺序和电荷平衡。
4. 小规模设置 num_per_group、space_groups_limit、n_screen 验证全链路后再放大。
5. 不在共享集群登录节点使用 Shell + SSHContext 跑重计算。
6. 不把 SSH 密码、私钥或许可证信息写入仓库配置。
7. 保留 workflow_status.yaml、主日志、dpdispatcher.log 和排序 CSV。
8. MLP 只用于预筛选；最终结构和排序应以 VASP/DFT 结果及人工检查为准。
