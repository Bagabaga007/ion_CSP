# ion_CSP Docker 使用指南

本文档说明如何使用 Docker 容器运行 ion_CSP。

## 快速开始

### 1. 构建镜像

**CPU 版（默认，适合大多数场景）：**
```bash
docker build -t ion-csp:latest .
```

**GPU 版（MLP 优化加速，需要 nvidia-docker）：**
```bash
docker build -f Dockerfile.gpu -t ion-csp:gpu .
```

### 2. 运行容器

**交互式开发环境（推荐）：**
```bash
docker run -it --rm \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  ion-csp:latest
```

进入容器后：
- 运行交互菜单：`bash scripts/CLI.sh`
- 直接运行工作流：`python -m ion_CSP.run.main_CSP /app/data/your_workdir`
- 使用命令行工具：`ion-csp --help`

**直接运行特定工作流：**
```bash
# CSP 工作流
docker run --rm \
  -v /path/to/your/workdir:/app/data/workdir \
  ion-csp:latest \
  python -m ion_CSP.run.main_CSP /app/data/workdir

# EE 工作流
docker run --rm \
  -v /path/to/your/workdir:/app/data/workdir \
  ion-csp:latest \
  python -m ion_CSP.run.main_EE /app/data/workdir
```

### 3. 使用 docker-compose（开发推荐）

```bash
# 启动（后台运行）
docker-compose up -d

# 进入容器
docker-compose exec ion-csp bash

# 停止
docker-compose down
```

## GPU 支持

**前提条件：**
- 安装 [nvidia-docker](https://github.com/NVIDIA/nvidia-docker)
- CUDA 11.8+ 驱动

**运行 GPU 容器：**
```bash
docker run -it --rm --gpus all \
  -v $(pwd)/data:/app/data \
  ion-csp:gpu bash
```

**验证 GPU 可用：**
```bash
# 在容器内
python -c "import torch; print(torch.cuda.is_available())"
```

## 数据持久化

容器内有两个数据卷挂载点：
- `/app/data` — 工作目录，存放输入/输出数据
- `/app/logs` — 日志目录

**建议映射：**
```bash
docker run -it --rm \
  -v $(pwd)/my_project:/app/data \
  -v $(pwd)/logs:/app/logs \
  ion-csp:latest
```

## 环境变量配置

可通过 `-e` 覆盖环境变量：

```bash
docker run -it --rm \
  -e OMP_NUM_THREADS=16 \
  -e DP_INTRA_OP_PARALLELISM_THREADS=16 \
  -v $(pwd)/data:/app/data \
  ion-csp:latest
```

**常用变量：**
- `OMP_NUM_THREADS` — OpenMP 线程数（默认 4）
- `DP_INTRA_OP_PARALLELISM_THREADS` — deepmd-kit 内部并行（默认 4）
- `WORKSPACE` — 工作目录路径（默认 /app/data）
- `LOG_DIR` — 日志目录（默认 /app/logs）

## 资源限制

**限制 CPU 和内存：**
```bash
docker run -it --rm \
  --cpus=8 \
  --memory=16g \
  -v $(pwd)/data:/app/data \
  ion-csp:latest
```

**或在 docker-compose.yml 中配置：**
```yaml
deploy:
  resources:
    limits:
      cpus: '8'
      memory: 16G
```

## 健康检查

容器内置健康检查，每 30 秒验证 Python 环境和 ion-csp CLI：

```bash
# 查看健康状态
docker ps --format "table {{.Names}}\t{{.Status}}"
```

## 故障排查

### 1. 依赖安装失败

**症状：** `pip install` 报错

**解决：** 确保使用 `python:3.11` 完整镜像（不是 slim）

### 2. phonopy 命令找不到

**症状：** `subprocess: phonopy: command not found`

**解决：** phonopy Python 包已安装且带 CLI 入口点，确认 `pip show phonopy` 正常

### 3. deepmd-kit 运行慢

**症状：** MLP 优化非常慢

**解决：** 使用 GPU 版 Dockerfile 或增加 CPU 核数

### 4. 权限问题

**症状：** `Permission denied` 写入 /app/data

**解决：** 
```bash
# 主机创建目录并设置权限
mkdir -p data logs
chmod 777 data logs
```

或在 docker run 时指定用户：
```bash
docker run -it --rm --user $(id -u):$(id -g) ...
```

## 生产部署建议

1. **多阶段构建已优化** — 镜像大小已最小化
2. **使用特定版本标签** — 不要用 `latest`
3. **挂载只读配置** — 敏感配置用 `-v config.yaml:/app/config.yaml:ro`
4. **日志外部化** — 挂载 `/app/logs` 到宿主机或日志收集系统
5. **资源监控** — 配合 Prometheus + cAdvisor 监控容器资源

## 更新镜像

```bash
# 重新构建
docker build -t ion-csp:v2.0 .

# 清理旧镜像
docker image prune -f
```

## 参考

- [Docker 官方文档](https://docs.docker.com/)
- [nvidia-docker](https://github.com/NVIDIA/nvidia-docker)
- [deepmd-kit 文档](https://docs.deepmodeling.com/projects/deepmd/)
