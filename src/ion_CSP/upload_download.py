import os
import re
import json
import time
import logging
import getpass
import paramiko
from stat import S_ISDIR
from collections import deque
from typing import List, Dict


class SSHBatchJob:
    
    def __init__(
        self, work_dir: str, machine_json: str, machine_type: str = "ssh_direct"
    ):
        self.base_dir = work_dir
        os.chdir(self.base_dir)
        self.folder_name = os.path.normpath(os.path.abspath(work_dir)).split(os.sep)[-1]
        self.upload_folder = f"{self.folder_name}/3_for_vasp_opt"
        self.download_folder = "4_vasp_optimized"
        # 本地的目标文件夹路径
        self.local_folder_dir = f"{os.path.dirname(self.base_dir)}/{self.upload_folder}"
        # 加载配置文件
        with open(machine_json, "r") as mf:
            self.machine_config = json.load(mf)
        self.remote_dir = self.machine_config["remote_root"]
        self.remote_task_dir = f"{self.remote_dir}/{self.upload_folder}"
        remote_profile = self.machine_config["remote_profile"]
        if machine_type == "ssh_direct":
            try:
                # 创建 SSH 客户端并连接到服务器，支持超时设置
                self.client = paramiko.SSHClient()
                self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.client.connect(
                    hostname=remote_profile["hostname"],
                    username=remote_profile["username"],
                    password=remote_profile["password"],
                    port=remote_profile["port"],
                    look_for_keys=remote_profile["look_for_keys"],
                    timeout=10,
                )
                self.sftp = self.client.open_sftp()
                print(
                    f"Direct SSH connection with {machine_json.split('_machine.json')[0]} established successfully."
                )
                logging.info(
                    f"Direct SSH connection with {machine_json.split('_machine.json')[0]} established successfully."
                )
            except Exception as e:
                logging.error(
                    f"Failed to establish direct SSH connection with {machine_json.split('_machine.json')[0]}: {e}"
                )
                raise
        if machine_type == "jumper":
            # 创建跳板机 SSH 客户端
            jumper_client = paramiko.SSHClient()
            jumper_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                # 连接到跳板机
                jumper_profile = self.machine_config["jumper_profile"]
                jumper_client.connect(
                    hostname=jumper_profile["hostname"],
                    username=jumper_profile["username"],
                    port=jumper_profile["port"],
                    key_filename=jumper_profile["key_filename"],
                    timeout=10,
                )
                # 创建一个通道，并建立代理通道
                jumper_transport = jumper_client.get_transport()
                src_addr = (jumper_profile["hostname"], jumper_profile["port"])
                dest_addr = (remote_profile["hostname"], remote_profile["port"])
                jumper_channel = jumper_transport.open_channel(
                    kind="direct-tcpip", dest_addr=dest_addr, src_addr=src_addr
                )
                print("Jumper connection established successfully")
                logging.info("Jumper connection established successfully")
                # 创建 SSH 客户端并连接到服务器，支持超时设置
                self.client = paramiko.SSHClient()
                self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.client.connect(
                    hostname=remote_profile["hostname"],
                    username=remote_profile["username"],
                    password=remote_profile["password"],
                    port=remote_profile["port"],
                    sock=jumper_channel,
                    look_for_keys=remote_profile["look_for_keys"],
                    timeout=10,
                )
                self.sftp = self.client.open_sftp()
                print(
                    f"SSH jumper connection with {machine_json.split('_machine.json')[0]} established successfully."
                )
                logging.info(
                    f"SSH jumper connection with {machine_json.split('_machine.json')[0]} established successfully."
                )
            except Exception as e:
                logging.error(f"Failed to establish SSH connection: {e}")
                raise
        if machine_type == "2FA":
            try:
                # 获取 machine.json 中的固定部分密码
                fixed_password = remote_profile["password"]
                # 获取动态验证码
                dynamic_code = getpass.getpass(
                    prompt="请输入Authentifactor中的动态验证码: "
                )
                # 组合完整的密码
                full_password = f"{fixed_password}{dynamic_code}"
                self.client = paramiko.SSHClient()
                self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.client.connect(
                    hostname=remote_profile["hostname"],
                    username=remote_profile["username"],
                    password=full_password,
                    port=remote_profile["port"],
                    look_for_keys=remote_profile["look_for_keys"],
                    timeout=10,
                )
                self.sftp = self.client.open_sftp()
                print(
                    f"Direct SSH connection with {machine_json.split('_machine.json')[0]} established successfully."
                )
                logging.info(
                    f"Direct SSH connection with {machine_json.split('_machine.json')[0]} established successfully."
                )
            except Exception as e:
                logging.error(
                    f"Failed to establish direct SSH connection with {machine_json.split('_machine.json')[0]}: {e}"
                )
                raise

    def _execute_command(self, command: str):
        """执行命令，支持重试机制"""
        output, error = None, None
        for attempt in range(3):  # 重试 3 次
            try:
                _, stdout, stderr = self.client.exec_command(command)
                output, error = stdout.read().decode(), stderr.read().decode()
                logging.info(output)
                print(output)
                if error:
                    logging.error(error)
                    raise Exception(f"Error executing command: {error}")
                break  # 成功后跳出重试循环
            except Exception as e:
                print(f"Error executing command: {e}. Retrying...")
                time.sleep(5)  # 等待 5 秒后重试
        return output, error

    def _upload_files(self, local_dir: str, local_files: List[str], remote_dir: str):
        """上传文件到远程服务器，支持重试机制"""
        for local_file in local_files:
            local_path = os.path.join(local_dir, local_file)
            remote_path = os.path.join(remote_dir, local_file)
            try:
                self.sftp.stat(remote_dir)
            except FileNotFoundError:
                self.sftp.mkdir(remote_dir)
            for attempt in range(3):  # 重试 3 次
                try:
                    self.sftp.put(local_path, remote_path)
                    print(f"Uploaded successful: from {local_path} to {remote_path}")
                    logging.info(
                        f"Uploaded successful: from {local_path} to {remote_path}"
                    )
                    break  # 成功后跳出重试循环
                except Exception as e:
                    print(f"Error uploading {local_path}: {e}. Retrying...")
                    logging.error(f"Error uploading {local_path}: {e}. Retrying...")
                    time.sleep(2)  # 等待 2 秒后重试

    def _batch_prepare(self, file_config: Dict[str, list[str]]):
        """
        Prepare files for upload and download based on file configuration.

        Example Parameter:
            file_config = {
                            'upload_prefixes': ['POSCAR_'],
                            'upload_suffixes': ['.gjf'],
                            'download_prefixes': ['CONTCAR_'],
                            'download_suffixes': ['.log', 'fchk']
                        }
        """
        upload_prefixes = file_config.get("upload_prefixes", [])
        upload_suffixes = file_config.get("upload_suffixes", [])
        download_prefixes = file_config.get("download_prefixes", [])
        download_suffixes = file_config.get("download_suffixes", [])
        self.batch_forward_json = []
        # 根据给定的“前缀”选择要上传的文件
        if upload_prefixes:
            for upload_prefix in upload_prefixes:
                upload_prefix_files = [
                    f
                    for f in os.listdir(self.local_folder_dir)
                    if f.startswith(upload_prefix)
                ]
                self.forward_files.extend(upload_prefix_files)
                self.batch_forward_json.extend(
                    [f[len(upload_prefix) :], upload_prefix] for f in self.forward_files
                )
                # 可以根据上传文件的名字以及给定的“前缀”设定作业后所要下载的文件名
                if download_prefixes:
                    for download_prefix in download_prefixes:
                        self.backward_files.extend(
                            f"{download_prefix}{f[len(upload_prefix) :]}"
                            for f in upload_prefix_files
                        )
        # 根据给定的“后缀”选择要上传的文件
        if upload_suffixes:
            for upload_suffix in upload_suffixes:
                upload_suffix_files = [
                    f
                    for f in os.listdir(self.local_folder_dir)
                    if f.endswith(upload_suffix)
                ]
                self.forward_files.extend(upload_suffix_files)
                self.batch_forward_json.extend(
                    [f[: -len(upload_suffix)], upload_suffix]
                    for f in self.forward_files
                )
                # 可以根据上传文件的名字以及给定的“后缀”设定作业后所要下载的文件名
                if download_suffixes:
                    for download_suffix in download_suffixes:
                        self.backward_files.extend(
                            f"{f[: -len(upload_suffix)]}{download_suffix}"
                            for f in upload_suffix_files
                        )

    def prepare_and_submit(
        self,
        command: str,
        forward_common_files: List[str] = [],
        upload_files: List[str] = [],
        download_files: List[str] = [],
        batch_config: Dict[str, list[str]] = None,
    ):
        # 确保参数为文件名的字符串列表，否则抛出类型异常
        if not isinstance(forward_common_files, list):
            raise TypeError(
                f"Expected a list of strings, but received: {type(forward_common_files).__name__}"
            )
        # 在远程服务器上创建任务目录
        self._execute_command(f"mkdir -p {self.remote_task_dir}")
        if forward_common_files:
            self._upload_files(
                os.path.dirname(__file__),
                [file for file in forward_common_files],
                self.remote_dir,
            )

        # 针对专门的少数任务，可手动设定上传与下载的文件
        self.forward_files = upload_files
        self.backward_files = download_files
        if batch_config:
            self._batch_prepare(batch_config)
        self.backward_files = list(set(self.backward_files))
        # 输出所有的上传文件列表和下载文件列表并在日志中记录
        print(f"Forward_files: {self.forward_files}")
        print(f"Backward_files: {self.backward_files}")
        logging.info(f"Forward_files: {self.forward_files}")
        logging.info(f"Backward_files: {self.backward_files}")
        # 记录在json文件中，方便在ssh连接中断后下载文件
        with open(
            f"{self.local_folder_dir}/forward_batch_files.json", "w"
        ) as json_file:
            # 注意：forward_files.json中存放的是文件名与前后缀分开的键值对
            json.dump(self.batch_forward_json, json_file, indent=4)
        if self.backward_files:
            with open(
                f"{self.local_folder_dir}/backward_batch_files.json", "w"
            ) as json_file:
                # 注意：backward_files.json中存放的是完整的文件名列表
                json.dump(self.backward_files, json_file, indent=4)

        # 上传文件到远程服务器
        self._upload_files(
            self.local_folder_dir, [f for f in self.forward_files], self.remote_task_dir
        )
        try:
            # 执行提交命令
            output, _ = self._execute_command(f"cd {self.remote_dir}; {command}")
            # 正则表达式匹配 Job ID
            pattern_slurm = r"Submitted batch job (\d+)"
            pattern_lsf = r"Job <(\d+)> is submitted to queue <normal>"
            # 使用 re.findall 查找匹配所有输出内容
            matches_slurm = re.findall(pattern_slurm, output)
            matches_lsf = re.findall(pattern_lsf, output)
            # 合并所有匹配的 Job ID
            job_ids = matches_slurm + matches_lsf
            if job_ids:
                print(f"Captured Job IDs: {job_ids}")
                logging.info(f"Captured Job IDs: {job_ids}")
                with open(
                    f"{self.local_folder_dir}/submitted_job_ids.json", "w"
                ) as json_file:
                    json.dump(job_ids, json_file, indent=4)
            else:
                print("No Job IDs found in command output.")
        except Exception as e:
            print(f"Error executing command: {e}")

    def upload_entire_folder(self, local_folder: str, remote_folder: str):
        """Upload entire local folder to remote folder"""
        local_dir = os.path.join(self.base_dir, local_folder)
        remote_dir = os.path.join(self.remote_dir, remote_folder)

        # 创建远程目录，如果不存在的话
        try:
            self.sftp.mkdir(remote_dir)
        except IOError:  # 目录可能已经存在
            pass

        # 使用队列来管理待处理的文件夹
        folders = deque([local_dir])
        while folders:
            current_folder = folders.popleft()  # 获取当前处理的文件夹
            # 列出当前文件夹中的所有文件和子文件夹
            for item in os.listdir(current_folder):
                local_path = os.path.join(current_folder, item)
                remote_path = os.path.join(remote_dir, item)

                if os.path.isdir(local_path):  # 如果是目录，加入队列
                    # 创建远程对应的文件夹
                    try:
                        self.sftp.mkdir(remote_path)
                    except IOError:  # 目录可能已经存在
                        pass
                    folders.append(local_path)
                else:  # 如果是文件，上传文件
                    print(f"Uploading from {local_path} to {remote_path}")
                    self.sftp.put(local_path, remote_path)

    def download_entire_folder(
        self, remote_folder: str = None, local_folder: str = None
    ):
        """Download entire remote folder to local folder"""
        # if check_job_ids:
        #     with open(f'{self.folder}/submitted_job_ids.json', 'w') as json_file:
        #         job_ids = json.load(json_file)
        if not remote_folder:
            remote_folder = self.upload_folder
        if not local_folder:
            local_folder = self.download_folder
        local_dir = os.path.join(self.base_dir, local_folder)
        os.makedirs(local_dir, exist_ok=True)
        remote_dir = os.path.join(self.remote_dir, remote_folder)
        # 使用队列来管理待处理的文件夹
        folders = deque([remote_dir])
        while folders:
            current_folder = folders.popleft()  # 获取当前处理的文件夹
            # 列出当前文件夹中的所有文件和子文件夹
            for item in self.sftp.listdir_attr(current_folder):
                remote_path = os.path.join(current_folder, item.filename)
                relative_path = os.path.relpath(remote_path, start=remote_dir)
                local_path = os.path.join(local_dir, relative_path)
                if S_ISDIR(item.st_mode):  # 如果是目录，加入队列
                    # 创建本地对应的文件夹
                    if not os.path.exists(local_path):
                        os.makedirs(local_path)
                    folders.append(remote_path)
                else:  # 如果是文件，下载文件
                    os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    print(f"Downloading {remote_path} to {local_path}")
                    self.sftp.get(remote_path, local_path)

    def download_from_json(
        self,
        download_files: List[str] = [],
        download_prefixes: List[str] = [],
        download_suffixes: List[str] = [],
    ):
        """
        Due to the construction of a JSON storage file with file name information and prefixes and suffixes when uploading files, selective batch downloads can be performed based on the file name information in the JSON file and the given prefixes and suffixes of the files to be downloaded
        """
        results_dir = f"{self.local_folder_dir}/results"
        os.makedirs(results_dir, exist_ok=True)
        backward_files = download_files
        try:
            with open(f"{self.local_folder_dir}/backward_files.json", "r") as json_file:
                backward_files.extend(json.load(json_file))
                if not backward_files:
                    raise FileNotFoundError
            with open(f"{self.local_folder_dir}/forward_files.json", "r") as json_file:
                forward_json = json.load(json_file)
                if download_prefixes:
                    for download_prefix in download_prefixes:
                        backward_files.extend(
                            [f"{download_prefix}{f}" for f in forward_json.keys()]
                        )
                if download_suffixes:
                    for download_suffix in download_suffixes:
                        backward_files.extend(
                            [f"{f}{download_suffix}" for f in forward_json.keys()]
                        )
        except FileNotFoundError as e:
            logging.error(e)
        for remote_file in backward_files:
            local_file = os.path.join(results_dir, os.path.basename(remote_file))
            for attempt in range(3):  # 重试 3 次
                try:
                    remote_file_path = os.path.join(self.remote_task_dir, remote_file)
                    self.sftp.stat(remote_file_path)
                    self.sftp.get(remote_file_path, local_file)
                    print(
                        f"Downloaded {remote_file} from {self.remote_task_dir} to {local_file}"
                    )
                    logging.info(
                        f"Downloaded {remote_file} from {self.remote_task_dir} to {local_file}"
                    )
                    break  # 成功后跳出重试循环
                except FileNotFoundError:
                    print(
                        f"File {remote_file} not found in {self.remote_task_dir} on remote server."
                    )
                    logging.error(
                        f"File {remote_file} not found in {self.remote_task_dir} on remote server."
                    )
                    break  # 文件未找到，跳出重试循环
                except Exception as e:
                    print(f"Error downloading {remote_file}: {e}. Retrying...")
                    logging.error(f"Error downloading {remote_file}: {e}. Retrying...")
                    time.sleep(2)  # 等待 2 秒后重试

    def download_from_condition(
        self, prefixes: List[str] = [], suffixes: List[str] = []
    ):
        """Download all files with specified prefix or suffix conditions from the specified remote server directory"""
        # 如果没有提供前缀和后缀
        if not prefixes and not suffixes:
            logging.error("No prefixes or suffixes provided.")
            raise Exception("No prefixes or suffixes provided.")
        # 确保本地目录存在
        os.makedirs(self.local_folder_dir, exist_ok=True)
        # 列出远程目录中的文件
        remote_files = self.sftp.listdir(self.remote_task_dir)
        # 用于跟踪是否有文件被匹配和下载
        matched_files = False
        # 跟踪每个前缀和后缀的匹配情况
        unmatched_prefixes, unmatched_suffixes = set(prefixes), set(suffixes)
        for file_name in remote_files:
            # 如果提供了前缀
            if prefixes:
                for prefix in prefixes:
                    if file_name.startswith(prefix):
                        remote_file_path = os.path.join(self.remote_task_dir, file_name)
                        local_file_path = os.path.join(self.local_folder_dir, file_name)
                        # 下载文件
                        self.sftp.get(remote_file_path, local_file_path)
                        print(f"Downloaded: {remote_file_path} to {local_file_path}")
                        logging.info(
                            f"Downloaded: {remote_file_path} to {local_file_path}"
                        )
                        matched_files = True
                        unmatched_prefixes.discard(prefix)  # 移除已匹配的前缀
            # 如果提供了后缀
            if suffixes:
                for suffix in suffixes:
                    if file_name.endswith(suffix):
                        remote_file_path = os.path.join(self.remote_task_dir, file_name)
                        local_file_path = os.path.join(self.local_folder_dir, file_name)
                        # 下载文件
                        self.sftp.get(remote_file_path, local_file_path)
                        print(f"Downloaded: {remote_file_path} to {local_file_path}")
                        logging.info(
                            f"Downloaded: {remote_file_path} to {local_file_path}"
                        )
                        matched_files = True
                        unmatched_suffixes.discard(suffix)  # 移除已匹配的后缀
        # 输出未匹配到的前缀
        for prefix in unmatched_prefixes:
            print(f"Error: No files matched the given prefix: {prefix}")
            logging.error(f"Error: No files matched the given prefix: {prefix}")
        # 输出未匹配到的后缀
        for suffix in unmatched_suffixes:
            print(f"Error: No files matched the given suffix: {suffix}")
            logging.error(f"Error: No files matched the given suffix: {suffix}")
        # 如果没有匹配到任何文件，输出错误信息
        if not matched_files:
            print("Error: No files matched the given prefixes or suffixes.")
            logging.error("Error: No files matched the given prefixes or suffixes.")

    def close_connection(self):
        self.sftp.close()
        self.client.close()
