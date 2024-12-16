#!/bin/bash

BASE_DIR="vasp_combo_1/primitive"
PARA_DIR="parameter"
INCAR_0="${PARA_DIR}/INCAR_0"
POTCAR="${PARA_DIR}/POTCAR"
SUB_SCRIPT="sch9797_sub.sh"
SCRIPT_PATH="${PARA_DIR}/${SUB_SCRIPT}"

# 检查必要文件是否存在
if [[ ! -f "$INCAR_0" || ! -f "$POTCAR" || ! -f "$SCRIPT_PATH" ]]; then
    echo "必要文件缺失，请检查路径。"
    exit 1
fi

# 遍历CONTCAR_*文件
for contcar in ${BASE_DIR}/CONTCAR_*; do
    # 提取数字
    if [[ $contcar =~ CONTCAR_(.*) ]]; then
        sample=${BASH_REMATCH[1]}
        sample_dir="${BASE_DIR}/${sample}"
        mkdir -p "$sample_dir"
        # 拷贝CONTCAR文件并重命名
        cp "$contcar" "${sample_dir}/POSCAR"
        # 拷贝统一文件
        cp "$INCAR_0" "${sample_dir}/INCAR"
        cp "$POTCAR" "${sample_dir}/"
        cp "$SUB_SCRIPT" "${sample_dir}/"
        # 提交任务
        (cd "${sample_dir}" && sbatch {SUB_SCRIPT})
    fi
done

echo "所有任务已提交。"

