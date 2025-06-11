from ion_CSP.read_mlp_density import ReadMlpDensity
from ion_CSP.log_and_time import StatusLogger
from ion_CSP.log_and_time import log_and_time, merge_config, get_work_dir_and_config

# 默认配置
DEFAULT_CONFIG = {
    "read_mlp_density": {
        "n_screen": 10,  # 筛选机器学习势优化后密度最大的n个CONTCAR与对应的OUTCAR
        "molecules_screen": True,  # 是否排除离子改变的晶体结构
        "detail_log": False,  # 是否额外生成详细的筛选日志文件
    },
}


@log_and_time
def main(work_dir, config):
    task_name = "2_read_mlp_density"
    task = StatusLogger(work_dir=work_dir, task_name=task_name)
    try:
        task.set_running()
        # 分析处理机器学习势优化得到的CONTCAR文件
        result = ReadMlpDensity(work_dir=work_dir)
        # 读取密度数据，根据离子是否成键进行筛选，并将前n个最大密度的文件保存到max_density文件夹
        result.read_density_and_sort(n_screen=config["read_mlp_density"]["n_screen"],
                                    molecules_screen=config["read_mlp_density"]["molecules_screen"],
                                    detail_log=config["read_mlp_density"]["detail_log"],)
        # 将max_density文件夹中的结构文件利用 phononpy 模块进行对称化处理，方便后续对于结构的查看，同时不影响晶胞性质
        result.phonopy_processing_max_density()
        task.set_success()
    except Exception:
        task.set_failure()
        raise

if __name__ == "__main__":
    # 获取工作目录和配置
    work_dir, config = get_work_dir_and_config()
    # 合并配置（假设有merge_config函数）
    config["read_mlp_density"] = merge_config(
        default_config=DEFAULT_CONFIG, user_config=config, key="read_mlp_density"
    )
    # 调用主函数
    main(work_dir, config)