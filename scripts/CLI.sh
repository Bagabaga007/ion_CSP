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
    local CONSOLE_LOG=""
    
    mkdir -p "$WORK_DIR"
    CONSOLE_LOG="${WORK_DIR}/main_${MODULE}_console.log"
    
    echo "Starting ${MODULE} module..."

    # 后台执行任务并捕获PID
    {
        case $MODULE in
            EE)
                nohup python -m run.main_EE $WORK_DIR > "${WORK_DIR}/main_EE_console.log" 2>&1 &
                ;;
            CSP)
                nohup python -m run.main_CSP $WORK_DIR > "${WORK_DIR}/main_CSP_console.log" 2>&1 &
                ;;
        esac
        # 等待进程初始化与文件创建
        sleep 1
        echo $! > "${WORK_DIR}/pid.txt"
        sleep 1
    } &> "$CONSOLE_LOG" &
    
    # 等待PID文件创建
    while [ ! -f "${WORK_DIR}/pid.txt" ]; do
        sleep 1
    done

    # 安全获取PID，并创建符号链接（带错误处理）
    PYTHON_PID=$(cat "${WORK_DIR}/pid.txt")

    # 生成新的日志文件名
    OUTPUT_LOG="${WORK_DIR}/main_${MODULE}_console.log"
    echo "Original log file: $OUTPUT_LOG"
    STANDARD_LOG_FILE="${LOG_BASE}/${MODULE}_${PYTHON_PID}.log"
    ln -sf "$OUTPUT_LOG" "$STANDARD_LOG_FILE"
    rm -f "${WORK_DIR}/pid.txt"
    echo "Task started (PYTHON_PID: $PYTHON_PID)"
    echo "Normalized log file: $STANDARD_LOG_FILE"
    
    # 保持信息可见
    read -p "Press Enter to continue..." 
}

# ========================
# 日志分页显示函数
# ========================
view_logs() {
    local PAGE_SIZE=10
    local LOG_FILES=($(ls -t "$LOG_BASE"/*_*.log 2>/dev/null))
    local TOTAL_FILES=${#LOG_FILES[@]}
    local TOTAL_PAGES=$(( (TOTAL_FILES + PAGE_SIZE - 1) / PAGE_SIZE ))

    if [ $TOTAL_FILES -eq 0 ]; then
        echo "No logs found"
        return
    fi

    local current_page=0

    while true; do
        local start_index=$(( current_page * PAGE_SIZE ))
        local end_index=$(( start_index + PAGE_SIZE ))
        
        echo -e "\nAvailable logs:"
        
        for (( i=start_index; i<end_index && i<TOTAL_FILES; i++ )); do
            FILE_NAME=${LOG_FILES[$i]}
            MOD_TIME=$(stat -c %y "$FILE_NAME" | cut -d'.' -f1)
            echo "$(( i + 1 ))) $FILE_NAME ($MOD_TIME)"
        done

        echo -e "\nPage $(( current_page + 1 )) of $TOTAL_PAGES"
        if [ $current_page -gt 0 ]; then
            echo "Enter 'p' to go to the previous page."
        fi
        if [ $current_page -lt $(( TOTAL_PAGES - 1 )) ]; then
            echo "Enter 'n' to go to the next page."
        fi
        echo "Enter log number to view (q to cancel): "

        read -r choice

        if [[ $choice =~ ^[0-9]+$ ]]; then
            choice_index=$(( choice - 1 ))
            if [ $choice_index -ge 0 ] && [ $choice_index -lt $TOTAL_FILES ]; then
                less "${LOG_FILES[$choice_index]}"
            else
                echo "Invalid selection"
            fi
        elif [[ $choice == "n" && $current_page -lt $(( TOTAL_PAGES - 1 )) ]]; then
            current_page=$(( current_page + 1 ))
        elif [[ $choice == "p" && $current_page -gt 0 ]]; then
            current_page=$(( current_page - 1 ))
        elif [[ $choice == "q" ]]; then
            break
        else
            echo "Invalid command"
        fi
    done
}

# ========================
# 主函数
# ========================
main() {
    detect_env
    normalize_path "$1"
    
    while true; do
        clear
        echo "========== Task Execution System =========="
        echo "Current Environment: $ENV"
        echo "Current Directory: $(pwd)"
        echo "Log Base Directory: $LOG_BASE"
        echo "=================================================="
        echo "1) Run EE Module"
        echo "2) Run CSP Module"
        echo "3) View Logs"
        echo "4) Terminate Task"
        echo "q) Exit"
        echo "=================================================="
        
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
                view_logs
                ;;
            4)
                read -p "Enter PID to terminate: " TARGET_PID
                kill $TARGET_PID 2>/dev/null && echo "Target PID ${TARGET_PID} is killed" || echo "Process not found"
                read -p "Press Enter to continue..."
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