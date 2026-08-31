# 模板配置文件使用

`complete_config.yaml` 列出 EE 和 CSP 的全部常用参数；`simple_config.yaml`
保留完整串联 EE→CSP 所需的配置段，其余参数使用代码默认值。

使用时：

1. 复制其中一个模板到工作目录并命名为 `config.yaml`。
2. 将所有 `machine`、`resources` 占位路径替换为本机或集群上的实际路径。
3. 不使用中央预优化离子库时，将 `database_dir` 保持为空字符串。
4. `folders`、`ratios` 和 `ion_numbers` 必须按相同的离子顺序填写。
5. `target_dir` 留空时，组合输出到工作目录下的 `2_<sort_by>_combos/`；
   非空相对路径按 EE 工作目录解析，也可以填写绝对路径。

主工作流固定读取 `<work_dir>/config.yaml`，不支持 `--config` 参数。机器和
资源配置文件本身可以使用 YAML 或 JSON；主工作流配置必须使用 YAML。

完整说明见 [`docs/usage.md`](../docs/usage.md)。

## Template configuration files

Copy `complete_config.yaml` or `simple_config.yaml` into the work directory as
`config.yaml`, then replace every `machine` and `resources` placeholder. Keep
`database_dir` empty when no central optimized-ion database is available. The
workflow always reads `<work_dir>/config.yaml`; it does not accept a `--config`
option. See [`docs/usage.md`](../docs/usage.md) for the complete guide.
