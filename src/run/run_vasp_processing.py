from ion_CSP.vasp_processing import VaspProcessing
from ion_CSP.log_and_time import StatusLogger
from ion_CSP.log_and_time import log_and_time, merge_config, get_work_dir_and_config

# 默认配置
DEFAULT_CONFIG = {
    "vasp_processing": {
        "nodes": 2,  # VASP 分步优化占用 CPU 节点数
        "molecules_prior": True,  # 是否检查离子晶体结构中所有离子的结构
    },
}

@log_and_time
def main(work_dir, config):
    task_name = "4_vasp_processing"
    task = StatusLogger(work_dir=work_dir, task_name=task_name)
    try:
        task.set_running()
        result = VaspProcessing(work_dir=work_dir)
        # 基于 dpdispatcher 模块，在远程CPU服务器上批量准备并提交VASP分步优化任务
        # result.dpdisp_vasp_tasks(
        #     machine=config["vasp_processing"]["machine"],
        #     resources=config["vasp_processing"]["resources"],
        #     nodes=config["vasp_processing"]["nodes"],
        # )
        # 批量读取 VASP 分步优化的输出文件，并将能量和密度等结果保存到目录中的相应CSV文件
        result.read_vaspout_save_csv(config["vasp_processing"]["molecules_prior"])
        task.set_success()
    except Exception:
        task.set_failure()
        raise


if __name__ == "__main__":
    # 获取工作目录和配置
    work_dir, config = get_work_dir_and_config()
    # 合并配置（假设有merge_config函数）
    config["vasp_processing"] = merge_config(
        default_config=DEFAULT_CONFIG, user_config=config, key="vasp_processing"
    )
    # 调用主函数
    main(work_dir, config)
