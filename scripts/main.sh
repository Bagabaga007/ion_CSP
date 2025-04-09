#!/bin/bash

# 参数校验
if [ $# -ne 2 ]; then
    echo "Usage: $0 {EE|CSP} WORKING_DIR"
    exit 1
fi

# 选择执行模块
case $1 in
    EE)
        MODULE="src.main_EE"
        LOG_FILE="main_EE_console.log"
        ;;
    CSP)
        MODULE="src.main_CSP"
        LOG_FILE="main_CSP_console.log"
        ;;
    *)
        echo "Error: Invalid module $1"
        exit 1
        ;;
esac


# 执行命令（保持前台运行）
nohup python -m ${MODULE} "$2" > "$2/${LOG_FILE}" 2>&1 &