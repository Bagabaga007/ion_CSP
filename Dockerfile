# 第一阶段：构建环境
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    libssl-dev \
    python3-dev \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# 创建虚拟环境并安装依赖
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 第二阶段：运行环境
FROM python:3.11-slim

# 系统基础依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 创建非root用户并设置权限
RUN useradd -ms /bin/bash appuser && \
    mkdir -p /app/data /app/logs /app/scripts && \
    chown -R appuser:appuser /app

# 设置工作目录
WORKDIR /app

# 先复制脚本并设置权限
COPY scripts/main.sh /app/scripts/main.sh
RUN chmod 755 /app/scripts/main.sh && \
    chown appuser:appuser /app/scripts/main.sh

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 复制其余项目文件（最后执行以避免覆盖）
COPY --chown=appuser:appuser . /app/

# 环境变量配置
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    LOG_DIR=/app/logs \
    ENV=DOCKER \
    WORKSPACE=/app/data

# 创建日志目录
RUN mkdir -p ${LOG_DIR} && chmod 755 ${LOG_DIR}

# 安装当前 Python 项目
RUN pip install -e /app/

# 切换用户为appuser
USER appuser

# 默认命令
CMD ["/bin/bash", "/app/scripts/main.sh"]