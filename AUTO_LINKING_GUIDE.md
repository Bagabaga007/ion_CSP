# EE 中央离子库相对软链接与拓扑门禁

## 当前行为

EE 主流程在 SMILES 转换之后调用
ion_CSP.run.main_EE.setup_ion_links。该函数为配对电荷目录建立到
Database_Ions/3_For_CSP_module/charge_* 的相对软链接。

相对链接随整个 results 目录迁移时仍然有效，不再绑定某台服务器上的
/workplace/yz 或其他绝对根路径。

## 配置

convert_SMILES:

- database_dir：中央 Database_Ions 根目录。
- migrate_database_copies：为 true 时，把与中央库逐字节相同的旧普通副本迁移为软链接。
- validate_topology：为 true 时，组合生成前比较原始 SMILES 与 Gaussian 优化后 GJF 的元素标记邻接图。

推荐配置：

    convert_SMILES:
      csv_file: ions.csv
      database_dir: /absolute/path/to/results/Database_Ions
      migrate_database_copies: true
      validate_topology: true

## 项目输入和数据库配对目录

CSV 中出现的电荷目录被视为项目输入目录。例如 N8+ CSV 只有 charge_1：

- charge_1：只允许项目自己的 N8+；历史数据库链接会被移除。
- charge_-1：同步中央库阴离子的相对软链接。

N8- 则相反：

- charge_-1：项目输入。
- charge_1：数据库配对阳离子。

如果配对目录含有与中央库不同的本地 GJF，整个目录会作为本地数据集保留，
不会混入数据库链接。如果普通文件与中央库逐字节相同，则可以安全迁移为链接。

## 自愈规则

每次运行 setup_ion_links 时会：

1. 修复断开的数据库链接；
2. 把绝对数据库链接改成相对链接；
3. 删除数据库中已不存在的旧链接；
4. 在项目输入电荷目录中移除历史数据库链接；
5. 保留未知本地普通文件和非数据库链接；
6. 可选地迁移与中央库完全相同的硬拷贝。

函数返回 linked、repaired、migrated_copies、removed_project_links、
removed_stale_links 和 conflicts 统计。

## Optimized 目录

数据库软链接进入 1_2_Gaussian_optimized 和 Optimized 时继续保持相对链接。
项目自己的 Gaussian 产物仍是普通文件。最终 combo_n 只复制被选中的少量
GJF/JSON，使每个 CSP 工作目录保持独立。

## mixed-ion 过滤

组合生成会读取同名 JSON 的 ion_type：

- 正电荷目录接受 cation；
- 负电荷目录接受 anion；
- mixed_ion 或目录类型不匹配的条目不进入组合；
- 缺少 ion_type 的旧数据保持兼容，但应尽快补全元数据。

## Gaussian 拓扑质量门禁

组合生成前，validate_project_ion_topologies 会：

1. 从项目 CSV 读取 SMILES、Refcode 和 Charge；
2. 用 RDKit 构造期望元素标记邻接图；
3. 从优化后 GJF 几何和共价半径构造观测图；
4. 用图同构比较，允许原子重排但不允许断键、成键或碎裂；
5. 将不合格 GJF/JSON 移入
   1_2_Gaussian_optimized/Bad/topology_changed/charge_*；
6. 写入 topology_validation.json；
7. 若没有任何项目输入离子通过，则禁止生成组合。

该门禁比较邻接拓扑，不声称从坐标恢复严格的单双键或芳香键级。
如需键级验证，应额外分析 Gaussian 的 Wiberg 或 Mayer bond order。

## CSP 后分子检查

identify_molecules 现在比较完整的元素标记分子图和离子数量，而不再只比较
分子式集合。它可以检测：

- 同分子式但拓扑不同；
- 断键和新键；
- 离子碎裂或合并；
- 重复离子数量错误。

## 迁移建议

对旧项目执行迁移前应整体归档 charge_*、Optimized 数据库视图和组合目录。
不要用 rm -rf 直接清理。迁移后验证：

    find PROJECT/charge_-1 -type l ! -exec test -e {} \; -print

正常情况下不应输出断链。也可检查链接目标不是绝对路径：

    find PROJECT/charge_-1 -type l -printf '%p -> %l\n'
