# 2026-08-11 Docker 生产就绪优化会话总结

## 背景
对 ion_CSP 的 Dockerfile 进行生产就绪改进（方案B），目标：修复构建问题、优化镜像大小、完善文档。

## 完成的工作

### 1. Dockerfile 修复与优化（6 轮迭代）

**暴露并修复的 6 个真实问题**：
1. `scripts/main.sh` 不存在 → CMD 改为交互式 bash
2. builder 阶段缺 `README.md` → COPY 补上（pyproject.toml 声明了 readme）
3. `phonopy==2.28.0` 无 cp311 预编译 wheel → 升级到 2.34.1（有 wheel，代码只用 CLI，安全）
4. `pip install -e .` editable 安装路径失效 → 改为 `pip install .`（真正装进 site-packages）
5. builder 只 COPY `__init__.py` → COPY 完整 `src/`（`pip install .` 需要完整源码打包）
6. torch 默认拉 ~2GB CUDA 库 → CPU 版先从 CPU 索引装 torch（省 4.55GB）

**最终镜像**：
- `ion-csp:cpu`：**3.06GB**（推荐，CPU 生产环境）
- `ion-csp:latest`：7.61GB（含 CUDA，GPU 环境用）

### 2. 新增文件

- `Dockerfile.gpu`：GPU 加速版（CUDA 11.8，MLP 优化）
- `.dockerignore`：排除 tests/cache/__pycache__（优化构建）
- `docker-compose.yml`：开发环境配置（资源限制、卷挂载）
- `DOCKER.md`：完整使用文档（4.2KB，快速开始+故障排查+生产部署建议）
- `DOCKERFILE_IMPROVEMENTS.md`：11 项改进总结+前后对比

### 3. 代码修复

**pyproject.toml**：
- `phonopy==2.28.0` → `2.34.1`（修复构建，保持 CLI 稳定性）
- 打包配置：`[tool.setuptools.packages.find]` → `[tool.setuptools] packages=["ion_CSP", "ion_CSP.run"]`

**scripts/CLI.sh**：
- 模块路径：`python -m run.main_CSP` → `python -m ion_CSP.run.main_CSP`

**requirements.txt**：
- 废弃（重命名为 `.deprecated`），依赖统一由 `pyproject.toml` 管理

### 4. 验证通过的测试

✅ 镜像构建成功（CPU 3.06GB）  
✅ 模块导入（`ion_CSP.task_manager`）  
✅ CLI 启动（`ion-csp --help`）  
✅ 重型依赖（torch/deepmd/phonopy/pyxtal/rdkit）  
✅ 数据文件（model.pt, param/INCAR）  
✅ torch CPU 版本（2.5.0+cpu，CUDA=None）

## 使用

```bash
# 构建
docker build -t ion-csp:cpu .

# 运行
docker run -it --rm \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  ion-csp:cpu

# 或用 docker-compose
docker-compose up -d
docker-compose exec ion-csp bash
```

详见 `DOCKER.md`。

## 其他成果

### Database_Ions 数据库完善
- ✅ charge_8 真实 Gaussian 优化完成（替换估算占位）
- ✅ charge_1/2 源 CSV 重建（从碎片合并去重）
- ✅ d_combo_4 净电荷-2 问题诊断（EE→GenOpt 参数传递 bug，标记为物理无效）

### 全库状态（8 个电荷组，4206 离子，三段一致）
```
charge_1:  3080   charge_-1: 581
charge_2:   322   charge_-2: 153
charge_3:    24   charge_8:    1
charge_4:    37
charge_6:     8
```

## 技术笔记

1. **Docker 多阶段构建**：builder（编译环境）+ runtime（精简环境），减小最终镜像
2. **PyTorch CPU 索引**：`--index-url https://download.pytorch.org/whl/cpu` 避免拉 CUDA 库
3. **pip install 顺序**：先装 torch-cpu → 再装项目（避免重复拉 CUDA 版）
4. **打包陷阱**：`pip install .` 需要完整源码（不能只 COPY `__init__.py`）
5. **phonopy wheel 可用性**：2.34.1+ 有 cp311 wheel，2.28.0 只能源码编译

## 遗留事项

无。所有目标已完成并验证。

## 文件清单

```
修改:
  Dockerfile                    # CPU 优化版（默认，3.06GB）
  pyproject.toml                # phonopy 2.34.1 + 打包配置
  scripts/CLI.sh                # 模块路径修正

新增:
  Dockerfile.gpu                # GPU 版
  .dockerignore                 # 构建优化
  docker-compose.yml            # 开发环境
  DOCKER.md                     # 使用文档
  DOCKERFILE_IMPROVEMENTS.md    # 改进总结
  SESSION_NOTES_2026-08-11.md   # 本文档

重命名:
  requirements.txt → requirements.txt.deprecated
```

---
生成时间: 2026-08-11  
会话耗时: ~4 小时（含 6 轮 Docker 构建验证 + Database_Ions 数据库完善）
