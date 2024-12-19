#!/bin/bash

export PATH=/data/apps/lsf/10.1/linux2.6-glibc2.3-x86_64/bin:$PATH
export PATH=/data/home/miwenhui/bin:$PATH
declare -x LD_LIBRARY_PATH="/data/apps/lsf/10.1/linux2.6-glibc2.3-x86_64/lib"                                              
declare -x LSF_BINDIR="/data/apps/lsf/10.1/linux2.6-glibc2.3-x86_64/bin"                                                   
declare -x LSF_ENVDIR="/data/apps/lsf/conf"                                                                                
declare -x LSF_LIBDIR="/data/apps/lsf/10.1/linux2.6-glibc2.3-x86_64/lib"                                                   
declare -x LSF_SERVERDIR="/data/apps/lsf/10.1/linux2.6-glibc2.3-x86_64/etc"                                                
declare -x MANPATH="/data/apps/lsf/10.1/man:"                                                                              
declare -x OLDPWD="/data/apps/lsf/10.1/linux2.6-glibc2.3-x86_64/bin"

BASE_DIR="to_be_opt/vasp_combo_11/primitive_cell"
INCAR_0="INCAR_0"
POTCAR="POTCAR"
SUB_SCRIPT="JLU_184.sh"

# 检查必要文件是否存在
if [[ ! -f "$INCAR_0" || ! -f "$POTCAR" || ! -f "$SUB_SCRIPT" ]]; then
    echo "必要文件缺失，请检查路径。"
    exit 1
fi

# 遍历CONTCAR_*文件
for contcar in "$BASE_DIR"/CONTCAR_*; do
    # 添加调试信息
    echo "处理文件：$contcar"
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
        (cd "${sample_dir}" && sbatch "${SUB_SCRIPT}")
    fi
done

echo "所有任务已提交。"

