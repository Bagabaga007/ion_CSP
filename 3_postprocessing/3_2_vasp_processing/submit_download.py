import os
import re
import json
import time
import logging
import paramiko

class SSHBatchJob:
    def __init__(self, folder, machine_json, machine_type='ssh_direct'):
        self.base_dir = os.path.dirname(__file__)
        os.chdir(self.base_dir)
        self.folder = folder
        # 本地的目标文件夹路径
        self.local_folder_dir = f'{self.base_dir}/{self.folder}'
        # 加载配置文件
        with open(machine_json, 'r') as mf:
            self.machine_config = json.load(mf)
        self.remote_dir = self.machine_config['remote_root']
        self.remote_task_dir = f'{self.remote_dir}/{self.folder}'
        remote_profile = self.machine_config['remote_profile']
        if machine_type == 'ssh_direct':
            try:
                # 创建 SSH 客户端并连接到服务器，支持超时设置
                self.client = paramiko.SSHClient()
                self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.client.connect(
                    hostname=remote_profile['hostname'],
                    username=remote_profile['username'],
                    password=remote_profile['password'],
                    port=remote_profile['port'],
                    look_for_keys=remote_profile['look_for_keys'],
                    timeout=10,
                )
                self.sftp = self.client.open_sftp()
                print(f'Direct SSH connection with {machine_json.split("_machine.json")[0]} established successfully.')
                logging.info(f'Direct SSH connection with {machine_json.split("_machine.json")[0]} established successfully.')
            except Exception as e:
                logging.error(f'Failed to establish direct SSH connection with {machine_json.split("_machine.json")[0]}: {e}')
                raise 
        if machine_type == 'jumper':
            # 创建跳板机 SSH 客户端
            jumper_client = paramiko.SSHClient()
            jumper_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                # 连接到跳板机
                jumper_profile = self.machine_config['jumper_profile']
                jumper_client.connect(
                    hostname=jumper_profile['hostname'],
                    username=jumper_profile['username'],
                    port=jumper_profile['port'],
                    key_filename=jumper_profile['key_filename'],
                    timeout=10,
                )
                # 创建一个通道，并建立代理通道
                jumper_transport = jumper_client.get_transport()
                src_addr = (jumper_profile['hostname'], jumper_profile['port'])
                dest_addr = (remote_profile['hostname'], remote_profile['port'])
                jumper_channel = jumper_transport.open_channel(kind="direct-tcpip", dest_addr=dest_addr, src_addr=src_addr)
                print('Jumper connection established successfully')
                logging.info('Jumper connection established successfully')
                # 创建 SSH 客户端并连接到服务器，支持超时设置
                self.client = paramiko.SSHClient()
                self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.client.connect(
                    hostname=remote_profile['hostname'],
                    username=remote_profile['username'],
                    password=remote_profile['password'],
                    port=remote_profile['port'],
                    sock=jumper_channel,
                    look_for_keys=remote_profile['look_for_keys'],
                    timeout=10,
                )
                self.sftp = self.client.open_sftp()
                print(f'SSH jumper connection with {machine_json.split("_machine.json")[0]} established successfully.')
                logging.info(f'SSH jumper connection with {machine_json.split("_machine.json")[0]} established successfully.')
            except Exception as e:
                logging.error(f"Failed to establish SSH connection: {e}")
                raise

    def _execute_command(self, command):
        """执行命令，支持重试机制"""
        for attempt in range(3):  # 重试 3 次
            try:
                _, stdout, stderr = self.client.exec_command(command)
                output, error = stdout.read().decode(), stderr.read().decode()
                logging.info(output)
                print(output)
                if error:
                    logging.error(error)
                    raise Exception(f'Error executing command: {error}')
                break  # 成功后跳出重试循环
            except Exception as e:
                print(f'Error executing command: {e}. Retrying...')
                time.sleep(5)  # 等待 5 秒后重试
        return output, error

    def _upload_files(self, local_dir, local_files, remote_dir):
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
                    print(f'Uploaded successful: from {local_path} to {remote_path}')
                    logging.info(f'Uploaded successful: from {local_path} to {remote_path}')
                    break  # 成功后跳出重试循环
                except Exception as e:
                    print(f'Error uploading {local_path}: {e}. Retrying...')
                    logging.error(f'Error uploading {local_path}: {e}. Retrying...')
                    time.sleep(2)  # 等待 2 秒后重试

    def _batch_prepare(self, file_config):
        """
        Prepare files for upload and download based on file configuration.
        
        Example Parameter:
            file_config = {
                            'upload_prefix': 'POSCAR_',
                            'upload_suffix': '.gjf',
                            'download_prefixes': ['CONTCAR_'],
                            'download_suffixes': ['.log', 'fchk']
                        }
        """
        upload_prefix = file_config.get('upload_prefix', '')
        upload_suffix = file_config.get('upload_suffix', '')
        download_prefixes = file_config.get('download_prefixes', [])
        download_suffixes = file_config.get('download_suffixes', [])
        if upload_prefix:  # 根据给定的“前缀”选择要上传的文件
            upload_prefix_files = [f for f in os.listdir(self.local_folder_dir) if f.startswith(upload_prefix)]
            self.forward_files.extend(upload_prefix_files)
            self.forward_json = {f[len(upload_prefix):]: upload_prefix for f in self.forward_files}
            if download_prefixes:  # 可以根据上传文件的名字以及给定的“前缀”设定作业后所要下载的文件名
                for download_prefix in download_prefixes:
                    self.backward_files.extend(f'{download_prefix}{f[len(upload_prefix):]}' for f in upload_prefix_files)
        if upload_suffix:  # 根据给定的“后缀”选择要上传的文件
            upload_suffix_files = [f for f in os.listdir(self.local_folder_dir) if f.endswith(upload_suffix)]
            self.forward_files.extend(upload_suffix_files)
            self.forward_json = {f[:-len(upload_suffix)]: upload_suffix for f in self.forward_files}
            if download_suffixes:  # 可以根据上传文件的名字以及给定的“后缀”设定作业后所要下载的文件名
                for download_suffix in download_suffixes:
                    self.backward_files.extend(f'{f[:-len(upload_suffix)]}{download_suffix}' for f in upload_suffix_files)

    def prepare_and_submit(self, command, forward_common_files=[], upload_files=[], download_files=[], batch_config:dict=None):
        # 确保参数为文件名的字符串列表，否则抛出类型异常
        if not isinstance(forward_common_files, list):
            raise TypeError(f'Expected a list of strings, but received: {type(forward_common_files).__name__}')
        # 在远程服务器上创建任务目录
        self._execute_command(f'mkdir -p {self.remote_task_dir}') 
        if forward_common_files:    
            self._upload_files(self.base_dir, [file for file in forward_common_files], self.remote_dir)

        # 针对专门的少数任务，可手动设定上传与下载的文件
        self.forward_files = upload_files
        self.backward_files = download_files
        if batch_config:
            self._batch_prepare(batch_config) 
        # 输出所有的上传文件列表和下载文件列表并在日志中记录
        print(f'Forward_files: {self.forward_files}')
        print(f'Backward_files: {self.backward_files}')
        logging.info(f'Forward_files: {self.forward_files}')
        logging.info(f'Backward_files: {self.backward_files}')
        # 记录在json文件中，方便在ssh连接中断后下载文件
        with open(f'{self.folder}/forward_batch_files.json', 'w') as json_file:
            # 注意：forward_files.json中存放的是文件名与前后缀分开的键值对
            json.dump(self.forward_json, json_file, indent=4)
        if self.backward_files:
            with open(f'{self.folder}/backward_batch_files.json', 'w') as json_file:
                # 注意：backward_files.json中存放的是完整的文件名列表
                json.dump(self.backward_files, json_file, indent=4)
              
        # 上传文件到远程服务器
        self._upload_files(self.local_folder_dir, [f for f in self.forward_files], self.remote_task_dir)
        try:
            # 执行提交命令
            output, _ = self._execute_command(f'cd {self.remote_dir}; {command}')
            # 正则表达式匹配 Job ID
            pattern_slurm =  r'Submitted batch job (\d+)'
            pattern_lsf = r'Job <(\d+)> is submitted to queue <normal>' 
            # 使用 re.findall 查找匹配所有输出内容
            matches_slurm = re.findall(pattern_slurm, output)
            matches_lsf = re.findall(pattern_lsf, output)
            # 合并所有匹配的 Job ID
            job_ids = matches_slurm + matches_lsf         
            if job_ids:
                print(f'Captured Job IDs: {job_ids}')
                logging.info(f'Captured Job IDs: {job_ids}')              
                with open(f'{self.folder}/submitted_job_id.json', 'w') as json_file:
                    json.dump(job_ids, json_file, indent=4)
            else:
                print('No Job IDs found in command output.')
            
        except Exception as e:
            print(f'Error executing command: {e}')

    def download_entire_folder(self, remote_path, local_path):
        """Download the entire folder from SFTP server to local"""
        pass

    def download_from_json(self, download_files=[], download_prefixes=[], download_suffixes=[]):
        """下载文件，支持重试机制"""
        results_dir = f'{self.folder}/results'
        os.makedirs(results_dir, exist_ok=True)
        backward_files = download_files
        try:
            with open(f'{self.folder}/backward_files.json', 'r') as json_file:
                backward_files.extend(json.load(json_file))
                if not backward_files:
                    raise FileNotFoundError
            with open(f'{self.folder}/forward_files.json', 'r') as json_file:
                forward_json = json.load(json_file)
                if download_prefixes:
                    for download_prefix in download_prefixes:
                        backward_files.extend([f'{download_prefix}{f}' for f in forward_json.keys()])
                if download_suffixes:
                    for download_suffix in download_suffixes:
                        backward_files.extend([f'{f}{download_suffix}' for f in forward_json.keys()])
        except FileNotFoundError as e:
            logging.error(e)
        for remote_file in backward_files:
            local_file = os.path.join(results_dir, os.path.basename(remote_file))
            for attempt in range(3):  # 重试 3 次
                try:
                    remote_file_path = os.path.join(self.remote_task_dir, remote_file)
                    self.sftp.stat(remote_file_path)
                    self.sftp.get(remote_file_path, local_file)
                    print(f'Downloaded {remote_file} from {self.remote_task_dir} to {local_file}')
                    logging.info(f'Downloaded {remote_file} from {self.remote_task_dir} to {local_file}')
                    break  # 成功后跳出重试循环
                except FileNotFoundError:
                    print(f'File {remote_file} not found in {self.remote_task_dir} on remote server.')
                    logging.error(f'File {remote_file} not found in {self.remote_task_dir} on remote server.')
                    break  # 文件未找到，跳出重试循环
                except Exception as e:
                    print(f'Error downloading {remote_file}: {e}. Retrying...')
                    logging.error(f'Error downloading {remote_file}: {e}. Retrying...')
                    time.sleep(2)  # 等待 2 秒后重试
    
    def download_from_condition(self, prefixes:list=[], suffixes:list=[]):
        """Download all files with specified prefix or suffix conditions from the specified remote server directory"""        
        # 如果没有提供前缀和后缀
        if not prefixes and not suffixes:
            logging.error(f'No prefixes or suffixes provided.')
            raise Exception(f'No prefixes or suffixes provided.')
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
                        print(f'Downloaded: {remote_file_path} to {local_file_path}')
                        logging.info(f'Downloaded: {remote_file_path} to {local_file_path}')
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
                        print(f'Downloaded: {remote_file_path} to {local_file_path}')
                        logging.info(f'Downloaded: {remote_file_path} to {local_file_path}')
                        matched_files = True
                        unmatched_suffixes.discard(suffix)  # 移除已匹配的后缀
         # 输出未匹配到的前缀
        for prefix in unmatched_prefixes:
            print(f'Error: No files matched the given prefix: {prefix}')
            logging.error(f'Error: No files matched the given prefix: {prefix}')
        # 输出未匹配到的后缀
        for suffix in unmatched_suffixes:
            print(f'Error: No files matched the given suffix: {suffix}')
            logging.error(f'Error: No files matched the given suffix: {suffix}')
        # 如果没有匹配到任何文件，输出错误信息
        if not matched_files:
            print(f'Error: No files matched the given prefixes or suffixes.')
            logging.error(f'Error: No files matched the given prefixes or suffixes.')

    def check_jobs_completion(self, ):
        """Check if the submitted task has been completed"""
        while True:
            _, stdout, stderr = self._exec_command('squeue')
    
    def close_connection(self):
        self.sftp.close()
        self.client.close()

    def check_all_finished(self):
        pass

def log_and_time(func):
    """Decorator for recording log information and script runtime"""
    # 获取脚本所在目录, 在该目录下生成日志
    base_dir = os.path.dirname(__file__)
    script_name = os.path.basename(__file__) 
    log_file_path = os.path.join(base_dir, f'{script_name}_output.log')
    # 配置日志记录
    logging.basicConfig(
        filename = log_file_path,  # 日志文件名
        level = logging.INFO,  # 指定日志级别
        format='%(asctime)s - %(levelname)s - %(message)s'  # 日志格式
    )    
    def wrapper(*args, **kwargs):
        # 获取程序开始执行时的CPU时间和Wall Clock时间
        start_cpu, start_clock = time.process_time(), time.perf_counter()
        # 记录程序开始信息
        script_name = os.path.basename(__file__) 
        logging.info(f'Start running: {script_name}')
        # 调用实际的函数, 如果出现错误, 报错的同时也将错误信息记录到日志中
        result = None
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            logging.error(f'Error occurred: {e}', exc_info=True)
            raise
        print('The script has run successfully, and the output content has been recorded in the output.log file in the same directory.')
        # 获取程序结束时的CPU时间和Wall Clock时间
        end_cpu, end_clock = time.process_time(), time.perf_counter()
        # 计算CPU时间和Wall Clock时间的差值
        cpu_time, wall_time = end_cpu-start_cpu, end_clock-start_clock
        # 记录程序结束信息
        logging.info(f'End running: {script_name}\nWall time: {wall_time:.4f} sec, CPU time: {cpu_time:.4f} sec\n')
        return result
    return wrapper


@ log_and_time
def main(): 
    folder = 'to_be_opt/vasp_combo_11/primitive_cell'
    command = f'chmod +x JLU_184_batch_single.sh; ./JLU_184_batch_single.sh'
    batch_config = {'upload_prefix': 'CONTCAR_'}

    job = SSHBatchJob(folder=folder, machine_json='JLU_184_machine.json', machine_type='ssh_direct')
    job.prepare_and_submit(command=command, forward_common_files=['JLU_184_batch_single.sh', 'JLU_184_sub.sh',  'INCAR_0', 'POTCAR'], batch_config=batch_config)
    # job.download_from_json(download_suffixes=['.chk'])
    # job.download_from_condition(prefixes=['aaa'], suffixes=['.log'])
    # 关闭 SFTP 和 SSH 客户端
    job.close_connection()

if __name__ == '__main__':
    main()
