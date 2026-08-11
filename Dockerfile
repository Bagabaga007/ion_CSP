# ============================================
# ion_CSP Production-Ready Dockerfile (CPU)
# ============================================
# 多阶段构建，优化缓存，生产就绪
# GPU 支持见 Dockerfile.gpu 或文末注释

# ========== 第一阶段：构建环境 ==========
FROM python:3.11 AS builder

# 避免交互式安装
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /build

# 安装编译依赖（torch/deepmd 的 wheels 已预编译，但部分包需编译扩展）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ gfortran \
    libopenblas-dev \
    liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制打包所需的完整内容（pip install . 需要完整源码才能打包所有子包）
COPY pyproject.toml setup.py README.md ./
COPY src/ src/

# 创建虚拟环境并安装（含项目本身的所有模块与 run 子包）
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    # 先从 PyTorch CPU 索引安装 torch（不含 ~2GB CUDA 库），
    # 后续 pip install . 时 torch==2.5.0 已满足，不会再拉 CUDA 版本。
    pip install --no-cache-dir torch==2.5.0 --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir --prefer-binary .

# ========== 第二阶段：运行环境 ==========
FROM python:3.11-slim

# 运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 科学计算运行时库
    libopenblas0 liblapack3 libgomp1 \
    # phonopy subprocess 需要的基础工具
    ca-certificates \
    # 交互式 CLI 需要
    bash procps \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户
RUN useradd -m -u 1000 -s /bin/bash appuser && \
    mkdir -p /app/data /app/logs && \
    chown -R appuser:appuser /app

WORKDIR /app

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 复制项目文件（.dockerignore 会排除 __pycache__, tests, .git 等）
COPY --chown=appuser:appuser . /app/

# 环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    LOG_DIR=/app/logs \
    WORKSPACE=/app/data \
    # deepmd-kit CPU 线程优化（根据容器 CPU 核数调整）
    OMP_NUM_THREADS=4 \
    DP_INTRA_OP_PARALLELISM_THREADS=4 \
    DP_INTER_OP_PARALLELISM_THREADS=2

# 声明数据卷（便于挂载工作目录）
VOLUME ["/app/data", "/app/logs"]

# 健康检查（确认 Python 环境和 ion-csp CLI 可用）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import ion_CSP; from ion_CSP.task_manager import main" || exit 1

# 切换到非 root 用户
USER appuser

# 暴露端口（如果后续添加 Web API）
# EXPOSE 8000

# 默认命令：交互式 shell（用户可以运行 CLI.sh 或 ion-csp）
CMD ["/bin/bash"]

# ============================================
# 使用说明
# ============================================
#
# 构建:
#   docker build -t ion-csp:cpu .
#
# 交互式运行（推荐开发/调试）:
#   docker run -it --rm \
#     -v $(pwd)/data:/app/data \
#     -v $(pwd)/logs:/app/logs \
#     ion-csp:latest
#   # 进入容器后运行: bash scripts/CLI.sh 或 ion-csp
#
# 直接运行工作流（CSP 或 EE）:
#   docker run --rm \
#     -v /path/to/workdir:/app/data/workdir \
#     ion-csp:latest \
#     python -m ion_CSP.run.main_CSP /app/data/workdir
#
# GPU 支持:
#   - 需要 nvidia-docker 和 CUDA 11.8+ 基础镜像
#   - 参考 Dockerfile.gpu（或替换 FROM 为 nvidia/cuda:11.8.0-runtime-ubuntu22.04）
#   - 运行时加 --gpus all
#
# ============================================
