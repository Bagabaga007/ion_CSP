import os
import yaml
import argparse
from ion_CSP.log_and_time import log_and_time, StatusLogger
from ion_CSP.vasp_processing import VaspProcessing


@log_and_time
def main(work_dir, config):
    task_name = "4_vasp_processing"
    task = StatusLogger(work_dir=work_dir, task_name=task_name)
    try:
        task.set_running()
        result = VaspProcessing(work_dir=work_dir)
        # 基于 dpdispatcher 模块，在远程CPU服务器上批量准备并提交VASP分步优化任务
        result.dpdisp_vasp_tasks(
            machine=config["vasp_processing"]["machine_json"],
            resources=config["vasp_processing"]["resources_json"],
            nodes=config["vasp_processing"]["nodes"],
        )
        # 批量读取 VASP 分步优化的输出文件，并将能量和密度等结果保存到目录中的相应CSV文件
        result.read_vaspout_save_csv(config["vasp_processing"]["molecules_prior"])
        task.set_success()
    except Exception:
        task.set_failure()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Process files in a specified working directory"
    )
    parser.add_argument(
        "work_dir", type=str, help="The working directory to run the script in"
    )
    args = parser.parse_args()
    # 尝试读取配置文件
    try:
        with open(os.path.join(args.work_dir, "config.yaml"), "r") as file:
            config = yaml.safe_load(file)
    except FileNotFoundError:
        print(f"config.yaml not found in {args.work_dir}.")
        raise
    # 获取当前脚本的名称
    script_name = os.path.basename(__file__)
    # 调用主函数
    main(script_name, args.work_dir, config)
