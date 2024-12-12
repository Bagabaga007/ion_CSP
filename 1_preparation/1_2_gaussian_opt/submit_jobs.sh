#!/bin/bash
#SBATCH -p v6_384
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -c 1

# 检查是否提供了节点数参数
if [ "$#" -ne 1 ]; then
    echo "No node count provided, using default value of 10."
    required_nodes=10  # 默认值
else  
    required_nodes=$1  #获取所需的节点数
fi
# 遍历当前目录下的所有 .gjf 文件
files=(*.gjf)  # 将所有 .gjf 文件存入数组
total_files=${#files[@]}  # 获取文件总数
for ((i=0; i<total_files; )); do
    # 提交作业，直到达到所需的节点数
    while [ "$(squeue -u $USER | grep -c "g16")" -lt "$required_nodes" ] && [ "$i" -lt "$total_files" ]; do
        # 获取不带后缀的文件名
        base_name="${files[i]%.*}"
        sbatch g16_sub.sh "${files[i]}" "$base_name"
        echo "Submitted job for ${files[i]}"
        ((i++))  # 增加索引以提交下一个作业
        sleep 5
    done

    # 等待当前批次的作业完成
    while [ "$(squeue -u $USER | grep -c "g16")" -ge "$required_nodes" ]; do
        sleep 20
    done
done

