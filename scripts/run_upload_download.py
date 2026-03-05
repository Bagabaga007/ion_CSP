import os
import yaml
import logging
import argparse
from ion_CSP.log_and_time import log_and_time, StatusLogger
from ion_CSP.upload_download import SSHBatchJob


@log_and_time
def main(work_dir, config):
    task_name_3_1 = '3_upload_vasp'
    task_3_1 = StatusLogger(work_dir=work_dir, task_name=task_name_3_1)
    
    batch_config = {"upload_prefixes": ["CONTCAR_", "OUTCAR_"]}
    folder_name = os.path.normpath(os.path.abspath(work_dir)).split(os.sep)[-1]
    job = SSHBatchJob(
        work_dir=work_dir,
        machine_json=config["upload_download"]["machine_json"],
        machine_type="ssh_direct",
    )
    if not task_3_1.is_successful():
        try:
            task_3_1.set_running()
            script = "steps_opt_monitor.sh"
            command = f"./steps_opt_monitor.sh {folder_name}"
            job.prepare_and_submit(
                command=command, forward_common_files=[script], batch_config=batch_config
            )
            # 关闭 SFTP 和 SSH 客户端
            job.close_connection()
            task_3_1.set_success()
        except Exception:
            task_3_1.set_failure()
            raise

    if task_3_1.is_successful():
        task_name_3_2 = '3_download_vasp'
        task_3_2 = StatusLogger(work_dir=work_dir, task_name=task_name_3_2)
        if not task_3_2.is_successful():
            try:
                task_3_2.set_running()
                job.download_entire_folder()
                job.client.exec_command(f"rm -r {job.remote_dir}/{folder_name}")
                print(f"Deleted remote folder {folder_name}")
                logging.info(f"Deleted remote folder {folder_name}")
                # 关闭 SFTP 和 SSH 客户端
                job.close_connection()
                task_3_2.set_success()
            except Exception:
                task_3_2.set_failure()
                raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process files in a specified working directory")
    parser.add_argument("work_dir", type=str, help="The working directory to run the script in")
    args = parser.parse_args()
    # 尝试读取配置文件
    try:
        with open(os.path.join(args.work_dir, "config.yaml"), "r") as file:
            config = yaml.safe_load(file)
    except FileNotFoundError:
        print(f"config.yaml not found in {args.work_dir}.")
        raise
    # 调用主函数
    main(args.work_dir, config)
