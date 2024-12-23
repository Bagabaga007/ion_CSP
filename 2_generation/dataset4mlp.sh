#!/bin/bash

# 源根目录和目标根目录
SOURCE_DIR="batch_results"  # 替换为您的源目录路径
TARGET_DIR="mlp_dataset_for_vasp"  # 替换为您的目标目录路径

# 创建目标根目录
mkdir -p "$TARGET_DIR"

# 遍历源目录中的一级目录
for combo_dir in "$SOURCE_DIR"/combo_*; do
    if [ -d "$combo_dir" ]; then
        # 创建相应的目标一级目录
        combo_name=$(basename "$combo_dir")
        mkdir -p "$TARGET_DIR/$combo_name/optimized"

        # 复制以CONTCAR_开头的文件到目标目录
        cp "$combo_dir/optimized/CONTCAR_"* "$TARGET_DIR/$combo_name/optimized/" 2>/dev/null
    fi
done

echo "复制完成！"

