#!/bin/bash

BASE_DIR="./"

# 遍历所有.gjf文件
for gjf in "$BASE_DIR"/*.gjf; do
    # 安全校验文件存在性
    [ -f "$gjf" ] || { echo "Skipping invalid file: $gjf"; continue; }
    
    # 提取带路径的文件名
    full_name=$(basename "$gjf")
    
    # 安全提取基名（严格去除最后一个.gjf后缀）
    base_name="${full_name%.*}"
    
    # 执行命令（示例）
    g16 "$gjf" && formchk "${base_name}.chk"
    if [ $? -ne 0 ] ; then 
        touch "${base_name}.fchk"
    fi
done