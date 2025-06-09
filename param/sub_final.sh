#!/bin/bash

# 设置变量
BASE_DIR="./"
INCAR_1="INCAR_1"
INCAR_2="INCAR_2"
INCAR_3="INCAR_3"
POTCAR_H="POTCAR_H"
POTCAR_C="POTCAR_C"
POTCAR_N="POTCAR_N"
POTCAR_O="POTCAR_O"

# 检查必要文件是否存在
if [[ ! -f "$INCAR_1" || ! -f "$INCAR_2" || ! -f "$INCAR_3" || ! -f "$POTCAR_H" || ! -f "$POTCAR_C" || ! -f "$POTCAR_N" || ! -f "$POTCAR_O" ]]; then
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
        mv "POTCAR" "${sample_dir}/"  # 使用自动生成的 POTCAR
        original_dir=$(pwd)  # 保存当前目录
        cd "${sample_dir}" && mpirun -n ${DPDISPATCHER_CPU_PER_NODE} vasp_std > vasp.log 2>&1
        cd $original_dir  # 返回原始工作目录
    fi
done

echo "All first step tasks have been submitted."

# 提交第二步优化任务
for sample in $(ls ${BASE_DIR}); do
    if [ -f "${BASE_DIR}/${sample}/CONTCAR" ]; then
        sample_dir="${BASE_DIR}/${sample}"
        mkdir -p "${sample_dir}/fine"
        cp "${sample_dir}/CONTCAR" "${sample_dir}/fine/POSCAR"
        cp "$INCAR_2" "${sample_dir}/fine/INCAR"
        cp "${sample_dir}/POTCAR" "${sample_dir}/fine/POTCAR"
        original_dir=$(pwd)  # 保存当前目录
        cd "${sample_dir}/fine" && mpirun -n ${DPDISPATCHER_CPU_PER_NODE} vasp_std > vasp.log 2>&1
        cd $original_dir  # 返回原始工作目录
    fi
done

echo "All second step tasks have been submitted."

# 提交第三步优化任务
for sample in $(ls ${BASE_DIR}); do
    if [ -f "${BASE_DIR}/${sample}/fine/CONTCAR" ]; then
        sample_dir="${BASE_DIR}/${sample}"
        mkdir -p "${sample_dir}/fine/final"
        cp "${sample_dir}/fine/CONTCAR" "${sample_dir}/fine/final/POSCAR"
        cp "$INCAR_3" "${sample_dir}/fine/final/INCAR"
        cp "${sample_dir}/fine/POTCAR" "${sample_dir}/fine/final/POTCAR"
        original_dir=$(pwd)  # 保存当前目录
        cd "${sample_dir}/fine/final" && mpirun -n ${DPDISPATCHER_CPU_PER_NODE} vasp_std > vasp.log 2>&1
        cd $original_dir  # 返回原始工作目录
    fi
done

echo "All third step tasks have been submitted."