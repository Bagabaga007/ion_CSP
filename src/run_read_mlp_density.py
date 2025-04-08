import os
import yaml
import argparse
from ion_CSP.log_and_time import log_and_time, StatusLogger
from ion_CSP.read_mlp_density import ReadMlpDensity

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
    parser = argparse.ArgumentParser(description="Process files in a specified working directory" )
    parser.add_argument("work_dir", type=str, help="The working directory to run the script in")
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
