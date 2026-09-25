# ion_CSP 三端生产交付指南

ion_CSP 不是一个可以用单一 Python 环境完整承载的程序。生产交付明确分成三个端点，
避免把主控解析依赖、GPU ABI 和授权软件混在一起：

| 端点 | 推荐目录 | 职责 | 必需软件/资产 |
|---|---|---|---|
| core 主控端 | `/srv/ion_CSP/core` | EE/CSP 入口、配置解析、RDKit/PyXtal、结果门禁 | Python 3.11、`uv.lock`、Multiwfn（EE）、Phonopy（CSP） |
| MLP 计算端 | `/srv/ion_CSP/mlp` | MLP 结构优化 | Python 3.12、DeepMD 3.2.0、Torch 2.11.0、模型、可选 CUDA |
| external 外部软件端 | `/srv/ion_CSP/external` 与节点 scratch | Gaussian/VASP 计算 | `g16`+`formchk` 或 `vasp_std`+MPI；许可证不随本项目发布 |

`core` 可以通过 dpdispatcher 的 SSHContext 把计算任务发到另外两端。预检命令只检查
目录、解释器、包版本和 PATH 可见性，不启动 Gaussian、VASP 或 MLP 作业。

## 1. 固定 core 与 MLP 环境

### Core

`uv.lock` 是 core 的完整、带工件哈希的 Python 解析结果，
`delivery/locks/core.lock.json` 是其交付索引。GPU 包被明确排除。

~~~bash
cd /srv/ion_CSP/core
conda env create -f environment.yml
conda activate ion-csp-env
python -m pip install uv
uv sync --frozen --no-dev
~~~

`environment.yml` 只承担 Conda/Python 启动环境；依赖冻结以 `uv.lock` 为准。文件使用
完整 channel URL 和 `nodefaults`，避免目标机器的隐式 defaults/channel 配置改变解析。

### MLP

默认且受支持的二进制单元为：

~~~text
Python 3.12.x
deepmd-kit[torch] 3.2.0
torch 2.11.0
ASE 3.23.0
~~~

创建环境：

~~~bash
conda env create -f environment-mlp.yml
conda activate ion-csp-mlp
python -m pip check
~~~

Torch、DeepMD 和 Torch wheel 内的 CUDA runtime 构成一个 ABI 单元。禁止只升级 Torch，
也不能把某台节点的 CUDA wheel 当作所有节点通用锁。每个实际 MLP 节点必须保存预检
receipt，其中记录 `torch.version.cuda`、`torch.cuda.is_available()` 和 GPU/驱动可见性。
DPA4 使用自有模型时还要绑定模型哈希；MatterSim 属于 BYO profile，必须另外冻结其
MatterSim/Torch/CUDA 组合，不能借用默认 DeepMD receipt。

## 2. 三端预检

所有命令都向 stdout 输出 JSON；有任一 required 检查失败时返回码为 1。`--json-output`
同时保存可归档 receipt，内容不读取或输出 machine 配置中的密码。

### Core + EE

~~~bash
python scripts/release_preflight.py \
  --target core \
  --workflow ee \
  --project-root /srv/ion_CSP/core \
  --work-dir /data/ion_CSP/runs/ee_001 \
  --writable-dir /data/ion_CSP/runs \
  --json-output /data/ion_CSP/receipts/core-ee.json
~~~

该检查明确要求 `Multiwfn_noGUI` 或 `Multiwfn` 在主控端 PATH 中。Gaussian 不在此处
检查，因为它在 Gaussian 节点执行。

### Core + CSP

~~~bash
python scripts/release_preflight.py \
  --target core \
  --workflow csp \
  --project-root /srv/ion_CSP/core \
  --work-dir /data/ion_CSP/runs/combo_1 \
  --json-output /data/ion_CSP/receipts/core-csp.json
~~~

该检查要求 `phonopy` 可执行文件，并校验组合目录中的所有 `gen_opt.species` 文件。

### MLP 节点

在 dpdispatcher 最终运行 `mlp_opt.py` 的相同节点和环境执行：

~~~bash
python scripts/release_preflight.py \
  --target mlp \
  --backend deepmd \
  --model /srv/ion_CSP/mlp/model.pt \
  --require-gpu \
  --writable-dir /scratch/ion_CSP \
  --json-output /srv/ion_CSP/receipts/mlp.json
~~~

CPU 验证环境可以去掉 `--require-gpu`；生产 GPU profile 不应去掉。

### Gaussian 节点

~~~bash
python scripts/release_preflight.py \
  --target external \
  --external-profile gaussian \
  --writable-dir /scratch/ion_CSP \
  --json-output /srv/ion_CSP/receipts/gaussian.json
~~~

要求 `g16` 与 `formchk` 同时在实际批处理环境的 PATH 中。

### VASP 节点

~~~bash
python scripts/release_preflight.py \
  --target external \
  --external-profile vasp \
  --writable-dir /scratch/ion_CSP \
  --json-output /srv/ion_CSP/receipts/vasp.json
~~~

要求 `vasp_std` 和 `mpirun`/`mpiexec` 可见。预检不证明许可证授权、赝势许可或数值
收敛；这些仍属于集群与科学验收。

## 3. 发布 wrapper 与配置合同

新入口把两个工作流明确分开：

~~~bash
ion-csp-workflow ee  /absolute/path/to/ee_work_dir --check-only
ion-csp-workflow csp /absolute/path/to/combo_1 --check-only

# 去掉 --check-only 才会真正启动工作流
ion-csp-workflow ee  /absolute/path/to/ee_work_dir
ion-csp-workflow csp /absolute/path/to/combo_1
~~~

wrapper 固定读取且只读取：

~~~text
<work_dir>/config.yaml
~~~

它先执行 core 环境和配置合同检查，通过后才调用现有
`python -m ion_CSP.run.main_EE` 或 `main_CSP`。完整机器可读合同位于
`delivery/config-contract.schema.json`，额外执行以下语义检查：

- EE：CSV 存在；`folders/ratios/ion_numbers` 非空且等长；nodes 为正整数；
- CSP：species 文件存在且与 ion_numbers 等长；MLP backend 有效；
- machine/resources 必须是绝对 `.yaml` 或 `.json` 路径并能解析；
- resources 的 `number_node`、`cpu_per_node`、`group_size` 必须为正整数；
- SSHContext + Shell 会给出警告，因为它绕过集群调度器。

旧的 `ion-csp` 交互式任务管理器仍保留；生产自动化推荐使用新 wrapper。

## 4. 构建、manifest 与验签

构建到隔离目录，避免把历史 `dist/` 文件误当成当前发布物：

~~~bash
python -m build --outdir dist/release
python scripts/build_release_manifest.py create \
  --project-root . \
  --artifact-dir dist/release \
  --output delivery/release-manifest.json
~~~

manifest 至少绑定：

- Git revision、dirty 状态和发布源文件聚合 SHA-256；
- 全部 `src/ion_CSP` Python 文件的 AST 解析结果；
- wheel、sdist 的路径、大小、角色和 SHA-256；
- core `uv.lock` 的 SHA-256 与解析包清单；
- DeepMD/Torch/ASE 配对、CUDA receipt 边界和默认模型 SHA-256；
- Gaussian/Multiwfn/Phonopy/VASP/MPI 外部软件清单；
- EE/CSP 配置合同哈希。

在交付前复验：

~~~bash
python scripts/build_release_manifest.py verify \
  --project-root . \
  --manifest delivery/release-manifest.json
~~~

任一源文件或构建产物改变都会导致验签失败。修改源码后必须重新构建并重新生成
manifest，不能手工修改哈希。

`release-manifest.json` 是与 wheel/sdist 并列的验签文件，不嵌入它所描述的 sdist，
从而避免 manifest 对自身形成不可收敛的哈希循环。

## 5. 发布门禁

生产发布至少满足：

1. 完整 pytest 回归通过；
2. wheel/sdist 构建成功，manifest verify 返回 0；
3. core、MLP、Gaussian、VASP 的实际目标节点 receipt 均返回 `ok: true`；
4. receipt 使用的模型、解释器、PATH 与 dpdispatcher machine/resources 指向同一环境；
5. 许可证、POTCAR 和真实计算数值由其各自负责人独立验收。

源码工作区为 dirty 时 manifest 会如实记录 dirty 文件。它仍可用于内部复现实验包，但
不应被误标为冻结正式版本；正式发布应从已审查的提交重新构建。
