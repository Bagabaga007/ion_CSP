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

INCAR_0="INCAR_0"
POTCAR="POTCAR"
SUB_SCRIPT="JLU_184_sub.sh"

# 检查必要文件是否存在
if [[ ! -f "$INCAR_0" || ! -f "$POTCAR" || ! -f "$SUB_SCRIPT" ]]; then
    echo "必要文件缺失，请检查路径。"
    exit 1
fi

# 定义根目录
root_dir="mlp_dataset_for_vasp"

# 遍历一级目录 combo_n
for combo_dir in "$root_dir"/combo_*; do
    # 确保是目录
    if [ -d "$combo_dir" ]; then
        # 进入 optimized 目录
        optimized_dir="$combo_dir/optimized"
        if [ -d "$optimized_dir" ]; then
            # 遍历 optimized 目录下的 CONTCAR_N 文件
            for contcar_file in "$optimized_dir"/CONTCAR_*; do
                # 添加调试信息
                echo "处理文件：$contcar_file"
                # 确保文件存在
                if [ -f "$contcar_file" ]; then
                    # 提取 N 的数字
                    N=$(basename "$contcar_file" | sed 's/CONTCAR_//')
                    # 创建以 N 命名的新文件夹
                    new_dir="$optimized_dir/$N"
                    mkdir -p "$new_dir"
                    # 移动并重命名文件
                    cp "$contcar_file" "$new_dir/POSCAR"
                    # 拷贝统一文件
                    cp "$INCAR_0" "${new_dir}/INCAR"
                    cp "$POTCAR" "${new_dir}/"
                    cp "$SUB_SCRIPT" "${new_dir}/"
                    # 提交任务
                    (cd "${new_dir}" && sbatch "${SUB_SCRIPT}")
                fi
            done
        fi
    fi
done

echo "所有任务已提交。"

