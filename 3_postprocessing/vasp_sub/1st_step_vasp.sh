#!/bin/bash

BASE_DIR="test_vasp"
PARA_DIR="parameter"
INCAR_1="${PARA_DIR}/INCAR_1"
INCAR_2="${PARA_DIR}/INCAR_2"
POTCAR="${PARA_DIR}/POTCAR"
SUB_SCRIPT="${PARA_DIR}/sub.sh"

# 检查必要文件是否存在
if [[ ! -f "$INCAR_1" || ! -f "$INCAR_2" || ! -f "$POTCAR" || ! -f "$SUB_SCRIPT" ]]; then
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
        cp "$INCAR_1" "${sample_dir}/INCAR"
        cp "$POTCAR" "${sample_dir}/"
        cp "$SUB_SCRIPT" "${sample_dir}/"
        # 提交任务
        (cd "${sample_dir}" && sbatch sub.sh)
    fi
done

echo "所有任务已提交。"

