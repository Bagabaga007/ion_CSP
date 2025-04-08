# Makefile

# 编译器
CC = python3

# 源文件
SRC = $(wildcard src/*.py) $(wildcard src/ion_CSP/*.py)

# 输出文件（可以根据需要修改）
OUT = app

# 虚拟环境目录
VENV = venv

# 默认目标
.PHONY: all
all: install run

# 创建虚拟环境
.PHONY: venv
venv:
	$(CC) -m venv $(VENV)

# 安装依赖
.PHONY: install
install: venv
	$(VENV)/bin/pip install -r requirements.txt

# 运行应用程序
.PHONY: run
run:
	$(VENV)/bin/python $(SRC)

# 清理
.PHONY: clean
clean:
	rm -rf $(VENV)
