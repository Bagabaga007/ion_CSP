# EE 自动软链接功能使用指南

## 功能说明

**已实现**：main_EE.py 现在会自动创建到 Database_Ions 的软链接，无需手动复制离子文件。

### 智能判断逻辑

1. **项目自有离子**：如果 `charge_X/` 文件夹已有非软链接的 gjf 文件，认为是项目自己的离子（如 N8、N10），**不会链接**
2. **需要配对离子**：如果文件夹不存在或为空，自动从 Database_Ions 创建软链接
3. **节省空间**：软链接只占几 KB，实际数据仍在 Database_Ions

### 示例

#### N10+（阳离子配阴离子）

```
N10+_1val_single_salt/
├── charge_1/           # N10 自己（convert_SMILES 生成）→ 不链接
│   ├── N10.gjf
│   └── N10.json
└── charge_-1/          # 需要阴离子配对 → 自动软链接
    ├── 1000.gjf -> /path/to/Database_Ions/3_For_CSP_module/charge_-1/1000.gjf
    ├── 1001.gjf -> ...
    └── ... (581 个软链接)
```

#### N8-（阴离子配阳离子）

```
N8-_1val_single_salt/
├── charge_-1/          # N8 自己 → 不链接
│   ├── N8-_1.gjf
│   └── N8-_2.gjf
└── charge_1/           # 需要阳离子配对 → 自动软链接
    ├── 1000.gjf -> /path/to/Database_Ions/3_For_CSP_module/charge_1/1000.gjf
    └── ... (3080 个软链接)
```

---

## 使用方法

### 无需任何改动！

保持现有 config.yaml 格式：

```yaml
convert_SMILES:
  csv_file: 'N10_ions.csv'
  database_dir: '/path/to/Database_Ions'  # 必须配置

empirical_estimate:
  folders: ['charge_1', 'charge_-1']  # 会自动链接缺失的文件夹
  ratios: [1, 1]
  ion_numbers: [2, 2]
  make_combo_dir: True
  num_combos: 50
  sort_by: 'NC_ratio'
```

运行 main_EE 时，会在日志中看到：

```
2026-08-13 17:00:00 - INFO - ✓ Linked charge_-1 from Database_Ions: 581 files (symlink)
2026-08-13 17:00:00 - INFO - charge_1 already has local ions (1 files), skipping linking
```

---

## 测试验证

### 步骤 1：清理旧的硬拷贝（可选）

如果之前手动复制过离子文件，可以清理：

```bash
cd /path/to/N10+_1val_single_salt
rm -rf charge_-1  # 删除硬拷贝
rm -f workflow_status.yaml  # 清理状态，重新运行 EE
```

### 步骤 2：运行 main_EE

```bash
cd /path/to/N10+_1val_single_salt
/path/to/conda/envs/ion_CSP/bin/python -m ion_CSP.run.main_EE $(pwd)
```

### 步骤 3：验证软链接

```bash
# 检查 charge_-1 是否是软链接
ls -lh charge_-1/*.gjf | head -5

# 应该看到类似：
# lrwxrwxrwx ... charge_-1/1000.gjf -> /path/to/Database_Ions/3_For_CSP_module/charge_-1/1000.gjf

# 检查空间占用
du -sh charge_-1
# 软链接只占几 KB，硬拷贝会占几十 MB
```

---

## 优势

| 对比项 | 硬拷贝（旧方案） | 软链接（新方案） |
|--------|-----------------|-----------------|
| **空间占用** | ~50MB/项目 | ~5KB/项目 |
| **同步性** | Database 更新需手动重新复制 | 自动同步 |
| **部署速度** | 需复制，慢 | 创建链接，快 |
| **维护成本** | 多份副本，不一致风险 | 单一数据源 |

### 节省空间示例

假设有 20 个 EE 项目，每个都需要配对 500 个离子（~50MB）：

- **硬拷贝**：20 × 50MB = **1GB**
- **软链接**：20 × 5KB = **~100KB**

**节省 99.99%！**

---

## 向后兼容

### 旧项目无影响

已有的硬拷贝项目**不会被改动**，智能判断会跳过：

```
2026-08-13 17:00:00 - INFO - charge_-1 already has local ions (581 files), skipping linking
```

### 新项目自动享受

新创建的项目自动使用软链接，无需任何配置改动。

---

## 故障排查

### Q1: 软链接失效（文件找不到）

**原因**：Database_Ions 被移动或删除

**解决**：
1. 确认 Database_Ions 路径正确：`ls /path/to/Database_Ions`
2. 更新 config.yaml 中的 `database_dir` 路径
3. 重新创建链接：
   ```bash
   rm -rf charge_-1
   /path/to/conda/envs/ion_CSP/bin/python -m ion_CSP.run.main_EE $(pwd)
   ```

### Q2: 想恢复硬拷贝

```bash
cd /path/to/N10+_1val_single_salt
# 删除软链接
rm -rf charge_-1
# 硬拷贝
cp -r /path/to/Database_Ions/3_For_CSP_module/charge_-1 ./
```

### Q3: 如何禁用自动链接

在 config.yaml 中移除或留空 `database_dir`：

```yaml
convert_SMILES:
  database_dir: ''  # 空字符串 = 禁用自动链接
```

---

## 技术细节

### 实现位置

- **文件**：`/path/to/ion_CSP/src/ion_CSP/run/main_EE.py`
- **函数**：`setup_ion_links(work_dir, config)`
- **调用时机**：`main()` 中的 SMILES 转换任务完成后、经验估算任务开始前

### 判断流程

```python
for folder in config["empirical_estimate"]["folders"]:
    if folder 已存在 and 有非软链接的 gjf:
        跳过（项目自有离子）
    else:
        创建软链接到 Database_Ions
```

### 日志标识

- `✓ Linked <folder> from Database_Ions: X files (symlink)` - 成功创建链接
- `<folder> already has local ions` - 跳过（已有文件）
- `Database folder not found` - 数据库缺少该 charge 文件夹

---

## 相关文件

- 实现代码：`/path/to/ion_CSP/src/ion_CSP/run/main_EE.py`
- 方案文档：`/tmp/EE_linking_solutions.md`
- 本指南：`/path/to/ion_CSP/AUTO_LINKING_GUIDE.md`

---

生成时间：2026-08-13
版本：v1.0
