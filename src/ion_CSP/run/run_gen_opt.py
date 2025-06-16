from ion_CSP.gen_opt import CrystalGenerator
from ion_CSP.log_and_time import StatusLogger
from ion_CSP.log_and_time import log_and_time, merge_config, get_work_dir_and_config

# 默认配置
DEFAULT_CONFIG = {
    "gen_opt": {
        "num_per_group": 500,  # 每个空间群要生成的晶体结构数量
        "space_groups_limit": 230,  # 空间群搜索的限制
        "nodes": 1,  # 机器学习势优化占用 GPU 节点数
    }
}

@log_and_time
def main(work_dir, config):
    generator = CrystalGenerator(
        work_dir=work_dir,
        ion_numbers=config["gen_opt"]["ion_numbers"],
        species=config["gen_opt"]["species"],
    )
    task_name_1_1 = "1_generation"
    task_1_1 = StatusLogger(work_dir=work_dir, task_name=task_name_1_1)
    if not task_1_1.is_successful():
        try:
            task_1_1.set_running()
            # 根据提供的离子与对应的配比，使用 pyxtal 基于晶体空间群进行离子晶体结构的随机生成。
            generator.generate_structures(
                num_per_group=config["gen_opt"]["num_per_group"],
                space_groups_limit=config["gen_opt"]["space_groups_limit"],
            )
            # 使用 phonopy 生成对称化的原胞另存于 primitive_cell 文件夹中，降低后续优化的复杂性，同时检查原子数以防止 pyxtal 生成双倍比例的超胞。
            generator.phonopy_processing()
            task_1_1.set_success()
        except Exception:
            task_1_1.set_failure()
            raise

    if task_1_1.is_successful():
        task_name_1_2 = "1_optimization"
        task_1_2 = StatusLogger(work_dir=work_dir, task_name=task_name_1_2)
        if not task_1_2.is_successful():
            try:
                task_1_2.set_running()
                # 基于 dpdispatcher 模块，在远程服务器上批量准备并提交输入文件，并在任务结束后回收机器学习势优化的输出文件 OUTCAR 与 CONTCAR
                generator.dpdisp_mlp_tasks(
                    machine=config["gen_opt"]["machine"],
                    resources=config["gen_opt"]["resources"],
                    python_path=config["gen_opt"]["python_path"],
                    nodes=config["gen_opt"]["nodes"],
                )
                task_1_2.set_success()
            except Exception:
                task_1_2.set_failure()
                raise


if __name__ == "__main__":
    # 获取工作目录和配置
    work_dir, config = get_work_dir_and_config()
    # 合并配置（假设有merge_config函数）
    config["gen_opt"] = merge_config(
        default_config=DEFAULT_CONFIG, user_config=config, key="gen_opt"
    )
    # 调用主函数
    main(work_dir, config)
