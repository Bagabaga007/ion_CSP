#!/bin/bash

# 定义变量
BASE_DIR="vasp_combo_1/primitive"
INCAR_2="INCAR_2"
POTCAR="POTCAR"
SUB_SCRIPT="sub.sh"
# 等待所有第一步任务完成
echo "等待所有第一步任务完成..."
for sample in $(ls ${BASE_DIR}; do
    while true; do
        if [ -f "${BASE_DIR}/${sample}/CONTCAR" ]; then
            echo "第一步优化完成，准备进行第二步优化..."
            sample_dir="${BASE_DIR}/${sample}"
            # 创建文件夹
            mkdir -p "${sample_dir}/fine"
            # 将CONTCAR重命名为POSCAR
            cp "${sample_dir}/CONTCAR" "${sample_dir}/fine/POSCAR"
            # 在fine文件夹中准备第二步的文件
            cp "${INCAR_2}" "${sample_dir}/fine/INCAR"
            cp "$POTCAR" "${sample_dir}/fine/"
            cp "$SUB_SCRIPT" "${sample_dir}/fine/"
            (cd "${sample_dir}/fine" && sbatch "${SUB_SCRIPT}")
            break
        fi
        sleep 10  # 每10秒检查一次
    done
done

echo "所有第二步任务已提交。"

