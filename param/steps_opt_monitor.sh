#!/bin/bash

# 设置变量
FOLDER=$1
BASE_DIR="$FOLDER/3_for_vasp_opt"
INCAR_1="INCAR_1"
INCAR_2="INCAR_2"
POTCAR_H="POTCAR_H"
POTCAR_C="POTCAR_C"
POTCAR_N="POTCAR_N"
POTCAR_O="POTCAR_O"
SUB_SCRIPT="JLU_184_sub.sh"
FLAG_FILE="${BASE_DIR}/flag_${FOLDER}.txt"

# 检查必要文件是否存在
if [[ ! -f "$INCAR_1" || ! -f "$INCAR_2" || ! -f "$POTCAR_H" || ! -f "$POTCAR_C" || ! -f "$POTCAR_N" || ! -f "$POTCAR_O" || ! -f "$SUB_SCRIPT" ]]; then
    echo "Necessary files are missing, please check the path."
    exit 1
fi

# 创建 POTCAR 文件
create_potcar_from_poscar() {
    poscar_file="$1"
    output_file="POTCAR"
    > "$output_file"  # 清空文件

    # 读取 POSCAR 文件的第六行（元素行）
    read -r element_line < <(sed -n '6p' "$poscar_file")

    # 将元素转换为数组
    IFS=' ' read -r -a elements <<< "$element_line"

    # 根据元素拼接 POTCAR
    for element in "${elements[@]}"; do
        case $element in
            H) cat "$POTCAR_H" >> "$output_file" ;;
            C) cat "$POTCAR_C" >> "$output_file" ;;
            N) cat "$POTCAR_N" >> "$output_file" ;;
            O) cat "$POTCAR_O" >> "$output_file" ;;
            *) echo "Warning: POTCAR for element $element not found." ;;
        esac
    done
}

# 提交第一步优化任务
for contcar in ${BASE_DIR}/CONTCAR_*; do
    if [[ $contcar =~ CONTCAR_(.*) ]]; then
        sample=${BASH_REMATCH[1]}
        sample_dir="${BASE_DIR}/${sample}"
        mkdir -p "$sample_dir"
        cp "$contcar" "${sample_dir}/POSCAR"
        cp "$INCAR_1" "${sample_dir}/INCAR"
        create_potcar_from_poscar "${sample_dir}/POSCAR"  # 根据 POSCAR 创建 POTCAR
        cp "POTCAR" "${sample_dir}/"  # 使用自动生成的 POTCAR
        cp "$SUB_SCRIPT" "${sample_dir}/"
        rm "POTCAR"
        (cd "${sample_dir}" && bsub -J "${FOLDER}_1" < "${SUB_SCRIPT}")
    fi
done

echo "All first step tasks have been submitted."

# 提交监控任务
monitor_job_id=$(bsub -J "m_$FOLDER" -n 1 -q normal -o /dev/null -e /dev/null <<EOF
#!/bin/bash

# 设置变量
FOLDER=$FOLDER
BASE_DIR="$BASE_DIR"
INCAR_2="$INCAR_2"
SUB_SCRIPT="$SUB_SCRIPT"
FLAG_FILE="$FLAG_FILE"

# 监控任务
while true; do
    # 检查第一步任务是否完成
    if ! bjobs -J "${FOLDER}_1" | grep -q "RUN\|PEND"; then
        echo "The first step task has been completed, ready to submit the second step task..."
        break
    fi
    sleep 60  # 每60秒检查一次
done

# 提交第二步优化任务
for sample in \$(ls \${BASE_DIR}); do
    if [ -f "\${BASE_DIR}/\${sample}/CONTCAR" ]; then
        sample_dir="\${BASE_DIR}/\${sample}"
        mkdir -p "\${sample_dir}/fine"
        cp "\${sample_dir}/CONTCAR" "\${sample_dir}/fine/POSCAR"
        cp "\$INCAR_2" "\${sample_dir}/fine/INCAR"
        cp "\${sample_dir}/POTCAR" "\${sample_dir}/fine/"
        cp "\$SUB_SCRIPT" "\${sample_dir}/fine/"
        (cd "\${sample_dir}/fine" && bsub -J "\${FOLDER}_2" < "\${SUB_SCRIPT}")
    fi
done

# 等待第二步任务完成
while true; do
    if ! bjobs -J "\${FOLDER}_2" | grep -q "RUN\|PEND"; then
        echo "All second step tasks have been completed, and the flag file is generated ..."
        touch "\$FLAG_FILE"
        break
    fi
    sleep 60  # 每60秒检查一次
done

EOF
)

echo "Monitoring task submitted, task ID: $monitor_job_id"
