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
    LOG_FILE="${WORK_DIR}/main_${MODULE}_console.log"
    
    echo "Starting ${MODULE} module..."
    echo "Original log file: $LOG_FILE"

    # 后台执行任务并捕获PID
    {
        case $MODULE in
            EE)
                nohup python -m src.main_EE $WORK_DIR > "${WORK_DIR}/main_EE_console.log" 2>&1 &
                ;;
            CSP)
                nohup python -m src.main_CSP $WORK_DIR > "${WORK_DIR}/main_CSP_console.log" 2>&1 &
                ;;
        esac
        # 等待进程初始化与文件创建
        sleep 1
        echo $! > "${WORK_DIR}/pid.txt"
        sleep 1
    } &> "$LOG_FILE" &
    
    # 等待PID文件创建
    while [ ! -f "${WORK_DIR}/pid.txt" ]; do
        sleep 1
    done

    # 安全获取PID，并创建符号链接（带错误处理）
    PYTHON_PID=$(cat "${WORK_DIR}/pid.txt")

    # 生成新的日志文件名
    STANDARD_LOG_FILE="${LOG_BASE}/${MODULE}_${PYTHON_PID}.log"
    ln -sf "$LOG_FILE" "$STANDARD_LOG_FILE"
    rm -f "${WORK_DIR}/pid.txt"
    echo "Task started (PYTHON_PID: $PYTHON_PID)"
    echo "Normalized log file: $STANDARD_LOG_FILE"
    
    # 保持信息可见
    read -p "Press Enter to continue..." 
}

# ========================
# 主函数
# ========================
main() {
    detect_env
    normalize_path "$1"
    
    while true; do
        clear
        echo "===== Task Execution System ====="
        echo "Current Environment: $ENV"
        echo "Current Directory: $(pwd)"
        echo "Log Base Directory: $LOG_BASE"
        echo "==================================="
        echo "1) Run EE Module"
        echo "2) Run CSP Module"
        echo "3) Terminate Task"
        echo "4) View Logs"
        echo "q) Exit"
        
        read -p "Please select one of the operation: " choice
        
        case $choice in
            1)
                read -p "Enter EE working directory: " EE_WORK_DIR
                task_runner "EE" "$(normalize_path "$EE_WORK_DIR")"
                ;;
            2)
                read -p "Enter CSP working directory: " CSP_WORK_DIR
                task_runner "CSP" "$(normalize_path "$CSP_WORK_DIR")"
                ;;
            3)
                read -p "Enter PID to terminate: " TARGET_PID
                kill $TARGET_PID 2>/dev/null && echo "Target PID ${TARGET_PID} is killed" || echo "Process not found"
                read -p "Press Enter to continue..."
                ;;
            4)
                echo "Available logs:"
                ls -lt "$LOG_BASE"/*_*.log 2>/dev/null || echo "No logs found"
                read -p "Enter log file to view: " LOG_FILE
                less "$LOG_BASE/$LOG_FILE" 2>/dev/null || echo "File not found"
                ;;
            q)
                echo "Exiting system..."
                exit 0
                ;;
            *)
                echo "Invalid selection. Please try again."
                sleep 1
                ;;
        esac
    done
}

# 启动系统
main "$@"