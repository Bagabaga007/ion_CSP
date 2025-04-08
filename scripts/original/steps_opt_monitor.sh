#!/bin/bash

# 设置变量
FOLDER=$1
BASE_DIR="$FOLDER/3_for_vasp_opt"
INCAR_1="INCAR_1"
INCAR_2="INCAR_2"
POTCAR="POTCAR"
SUB_SCRIPT="JLU_184_sub.sh"
FLAG_FILE="${BASE_DIR}/flag_${FOLDER}.txt"

# 检查必要文件是否存在
if [[ ! -f "$INCAR_1" || ! -f "$INCAR_2" || ! -f "$POTCAR" || ! -f "$SUB_SCRIPT" ]]; then
    echo "Necessary files are missing, please check the path."
    exit 1
fi

# 提交第一步优化任务
for contcar in ${BASE_DIR}/CONTCAR_*; do
    if [[ $contcar =~ CONTCAR_(.*) ]]; then
        sample=${BASH_REMATCH[1]}
        sample_dir="${BASE_DIR}/${sample}"
        mkdir -p "$sample_dir"
        cp "$contcar" "${sample_dir}/POSCAR"
        cp "$INCAR_1" "${sample_dir}/INCAR"
        cp "$POTCAR" "${sample_dir}/"
        cp "$SUB_SCRIPT" "${sample_dir}/"
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
POTCAR="$POTCAR"
SUB_SCRIPT="$SUB_SCRIPT"
FLAG_FILE="$FLAG_FILE"

# 监控任务
while true; do
    # 检查第一步任务是否完成
    if ! bjobs -J "${FOLDER}_1" | grep -q "RUN\|PEND"; then
        echo "The first step tasks have been completed, ready to submit the second step tasks"
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
        cp "\$POTCAR" "\${sample_dir}/fine/"
        cp "\$SUB_SCRIPT" "\${sample_dir}/fine/"
        (cd "\${sample_dir}/fine" && bsub -J "\${FOLDER}_2" < "\${SUB_SCRIPT}")
    fi
done

# 等待第二步任务完成
while true; do
    if ! bjobs -J "\${FOLDER}_2" | grep -q "RUN\|PEND"; then
        touch "\$FLAG_FILE"
        echo "All second step tasks have been completed, flag file generated"
        break
    fi
    sleep 60  # 每60秒检查一次
done

EOF
)

echo "Monitoring task ID: $monitor_job_id"
