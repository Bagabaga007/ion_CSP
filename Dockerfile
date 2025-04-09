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
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 创建非root用户
RUN useradd -m appuser && mkdir /app/data && chown appuser/appuser /app/data
USER appuser

# 设置工作目录
WORKDIR /app

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 复制项目文件
COPY . /app/

# 环境变量配置
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    LOG_DIR=/app/logs

# 创建日志目录
RUN mkdir -p ${LOG_DIR} && chmod 755 ${LOG_DIR}

# 暴露端口（根据需求调整）
EXPOSE 8000

# 默认命令（可通过参数覆盖）
CMD ["/bin/bash", "/app/scripts/main.sh"]
