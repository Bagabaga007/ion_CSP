import logging
from ion_CSP.gen_opt import CrystalGenerator
from ion_CSP.read_mlp_density import ReadMlpDensity
from ion_CSP.vasp_processing import VaspProcessing
from ion_CSP.log_and_time import StatusLogger
from ion_CSP.log_and_time import log_and_time, merge_config, get_work_dir_and_config

# 默认配置
DEFAULT_CONFIG = {
    "gen_opt": {
        "num_per_group": 500,  # 每个空间群要生成的晶体结构数量
        "space_groups_limit": 230,  # 空间群搜索的限制
        "nodes": 1,  # 机器学习势优化占用 GPU 节点数
    },
    "read_mlp_density": {
        "n_screen": 10,  # 筛选机器学习势优化后密度最大的n个CONTCAR与对应的OUTCAR
        "molecules_screen": True,  # 是否排除离子改变的晶体结构
        "detail_log": False,  # 是否额外生成详细的筛选日志文件
    },
    "vasp_processing": {
        "nodes": 2,  # VASP 分步优化占用 CPU 节点数
        "molecules_prior": True,  # 是否检查离子晶体结构中所有离子的结构
    },
}


@log_and_time
def main(work_dir, config):
    logging.info(f"Using config: {config}")
    tasks = {
        "1_generation": lambda: generation_task(work_dir, config),
        "1_optimization": lambda: mlp_optimization_task(work_dir, config),
        "2_read_mlp_density": lambda: read_mlp_density_task(work_dir, config),
        "3_vasp_optimization": lambda: vasp_optimization_task(work_dir, config),
        "3_vasp_relaxation": lambda: vasp_relaxation_task(work_dir, config),
    }
    for task_name, task_func in tasks.items():
        task_logger = StatusLogger(work_dir=work_dir, task_name=task_name)
        if not task_logger.is_successful():
            try:
                task_logger.set_running()
                task_func()
                task_logger.set_success()
            except Exception:
                task_logger.set_failure()
                raise
    logging.info(f"All tasks have been run successfully, including {tasks.keys()}")


def generation_task(work_dir, config):
    generator = CrystalGenerator(
        work_dir=work_dir,
        ion_numbers=config["gen_opt"]["ion_numbers"],
        species=config["gen_opt"]["species"],
    )
    # 根据提供的离子与对应的配比，使用 pyxtal 基于晶体空间群进行离子晶体结构的随机生成。
    generator.generate_structures(
        num_per_group=config["gen_opt"]["num_per_group"],
        space_groups_limit=config["gen_opt"]["space_groups_limit"],
    )
    # 使用 phonopy 生成对称化的原胞另存于 primitive_cell 文件夹中，降低后续优化的复杂性，同时检查原子数以防止 pyxtal 生成双倍比例的超胞。
    generator.phonopy_processing()


def mlp_optimization_task(work_dir, config):
    generator = CrystalGenerator(
        work_dir=work_dir,
        ion_numbers=config["gen_opt"]["ion_numbers"],
        species=config["gen_opt"]["species"],
    )
    # 基于 dpdispatcher 模块，在远程GPU服务器上批量准备并提交输入文件，并在任务结束后回收机器学习势优化的输出文件 OUTCAR 与 CONTCAR
    generator.dpdisp_mlp_tasks(
        machine=config["gen_opt"]["machine"],
        resources=config["gen_opt"]["resources"],
        nodes=config["gen_opt"]["nodes"],
    )


def read_mlp_density_task(work_dir, config):
    # 分析处理机器学习势优化得到的CONTCAR文件
    mlp_result = ReadMlpDensity(work_dir=work_dir)
    # 读取密度数据，根据离子是否成键进行筛选，并将前n个最大密度的文件保存到max_density文件夹
    mlp_result.read_density_and_sort(
        n_screen=config["read_mlp_density"]["n_screen"],
        molecules_screen=config["read_mlp_density"]["molecules_screen"],
        detail_log=config["read_mlp_density"]["detail_log"],
    )
    # 将max_density文件夹中的结构文件利用 phononpy 模块进行对称化处理，方便后续对于结构的查看，同时不影响晶胞性质
    mlp_result.phonopy_processing_max_density()


def vasp_optimization_task(work_dir, config):
    # VASP分步固定晶胞角度优化处理
    vasp_result = VaspProcessing(work_dir=work_dir)
    # 基于 dpdispatcher 模块，在远程CPU服务器上批量准备并提交VASP分步优化任务
    vasp_result.dpdisp_vasp_optimization_tasks(
        machine=config["vasp_processing"]["machine"],
        resources=config["vasp_processing"]["resources"],
        nodes=config["vasp_processing"]["nodes"],
    )
    # 批量读取 VASP 分步优化的输出文件，并将能量和密度等结果保存到目录中的相应CSV文件
    vasp_result.read_vaspout_save_csv(
        molecules_prior=config["vasp_processing"]["molecules_prior"]
    )


def vasp_relaxation_task(work_dir, config):
    # VASP无约束晶胞优化处理
    vasp_result = VaspProcessing(work_dir=work_dir)
    # 基于 dpdispatcher 模块，在远程CPU服务器上批量准备并提交VASP分步优化任务
    vasp_result.dpdisp_vasp_relaxation_tasks(
        machine=config["vasp_processing"]["machine"],
        resources=config["vasp_processing"]["resources"],
        nodes=config["vasp_processing"]["nodes"],
    )
    # 批量读取 VASP 分步优化的输出文件，并将能量和密度等结果保存到目录中的相应CSV文件
    vasp_result.read_vaspout_save_csv(
        molecules_prior=config["vasp_processing"]["molecules_prior"], relaxation=True
    )
    vasp_result.export_max_density_structure()


if __name__ == "__main__":
    # 获取工作目录和配置
    work_dir, config = get_work_dir_and_config()
    # 合并配置（假设有merge_config函数）
    modules = ["gen_opt", "read_mlp_density", "vasp_processing"]
    for module in modules:
        config[module] = merge_config(
            default_config=DEFAULT_CONFIG, user_config=config, key=module
        )
    # 调用主函数
    main(work_dir, config)
