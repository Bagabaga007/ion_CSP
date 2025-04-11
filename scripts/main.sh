#!/bin/bash

# ========================
# 全局配置
# ========================
declare -A ENV_CONFIG
ENV_CONFIG=(
    ["DOCKER"]="DOCKER"
    ["LOCAL"]="LOCAL"
)

# ========================
# 环境检测函数
# ========================
detect_env() {
    if [ -f /.dockerenv ] || [ -n "$DOCKER" ]; then
        ENV="DOCKER"
        WORKSPACE="/app"
        LOG_BASE="/app/logs"
    else
        ENV="LOCAL"
        WORKSPACE=$(pwd)
        LOG_BASE="$WORKSPACE/logs"
    fi
    mkdir -p $LOG_BASE
}

# ========================
# 路径标准化函数
# ========================
normalize_path() {
    local path="$1"
    if [ "$ENV" = "DOCKER" ]; then
        echo "$(realpath -m "${WORKSPACE}${path}")"
    else
        echo "$(realpath -m "${path}")"
    fi
}

# ========================
# 任务执行器
# ========================
task_runner() {
    local MODULE=$1
    local WORK_DIR=$2
    local LOG_FILE=""
    
    mkdir -p "$WORK_DIR"
    LOG_FILE="${WORK_DIR}/${MODULE}_console.log"
    
    echo "正在启动 ${MODULE} 模块..."
    echo "日志文件: $LOG_FILE"
    
    # 后台执行任务并捕获PID
    {
        case $MODULE in
            EE)
                python -m src.main_EE "$WORK_DIR"
                ;;
            CSP)
                python -m src.main_CSP "$WORK_DIR"
                ;;
            *)
                echo "无效模块: $MODULE"
                exit 1
                ;;
        esac
    } &> "$LOG_FILE" &
    
    local PID=$!
    
    # 生成新的日志文件名
    STANDARD_LOG_FILE="${LOG_BASE}/${MODULE}_console_${PID}.log"
    ln -sf "$LOG_FILE" "$STANDARD_LOG_FILE"
    
    echo "任务已启动 (PID: $PID)"
    echo "日志文件: $STANDARD_LOG_FILE"
    
    # 保持信息可见
    read -p "按回车键继续..." 
}

# ========================
# 主函数
# ========================
main() {
    detect_env
    normalize_path "$1"
    
    while true; do
        clear
        echo "===== 任务执行系统 ====="
        echo "当前环境: $ENV"
        echo "工作目录: $(pwd)"
        echo "日志基目录: $LOG_BASE"
        echo "======================="
        echo "1) 运行EE模块"
        echo "2) 运行CSP模块"
        echo "3) 终止任务"
        echo "4) 查看日志"
        echo "q) 退出"
        
        read -p "请选择操作: " choice
        
        case $choice in
            1)
                read -p "请输入EE工作目录: " EE_WORK_DIR
                task_runner "EE" "$(normalize_path "$EE_WORK_DIR")"
                read -p "任务已启动，按回车键继续..."
                ;;
            2)
                read -p "请输入CSP工作目录: " CSP_WORK_DIR
                task_runner "CSP" "$(normalize_path "$CSP_WORK_DIR")"
                read -p "任务已启动，按回车键继续..."
                ;;
            3)
                read -p "请输入要终止的PID: " TARGET_PID
                kill $TARGET_PID || echo "进程不存在"
                ;;
            4)
                less "$LOG_BASE"/*_console_*.log
                ;;
            q)
                echo "退出系统..."
                exit 0
                ;;
            *)
                echo "无效选择，请重新输入"
                sleep 1
                ;;
        esac
    done
}

# 启动系统
main "$@"