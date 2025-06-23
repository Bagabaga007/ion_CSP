import os
import csv
import time
import shutil
import logging
import subprocess
import importlib.resources
from typing import List
from ase.io import read
from dpdispatcher import Machine, Resources
from pyxtal import pyxtal
from pyxtal.msg import Comp_CompatibilityError, Symm_CompatibilityError
from ion_CSP.log_and_time import redirect_dpdisp_logging


class CrystalGenerator:
    def __init__(self, work_dir: str, ion_numbers: List[int], species: List[str]):
        """
        Initialize the class based on the provided ionic crystal composition structure files and corresponding composition numbers.

        :params
            work_dir: The working directory where the ionic crystal structure files are located.
            ion_numbers: A list of integers representing the number of each ion in the ionic crystal.
            species: A list of strings representing the species of ions in the ionic crystal.
        """
        redirect_dpdisp_logging(os.path.join(work_dir, "dpdispatcher.log"))
        self.mlp_opt_file = importlib.resources.files("ion_CSP").joinpath("mlp_opt.py")
        self.model_file = importlib.resources.files("ion_CSP.model").joinpath("model.pt")
        # 获取当前脚本的路径以及同路径下离子晶体组分的结构文件, 并将这一路径作为工作路径来避免可能的错误
        self.base_dir = work_dir
        os.chdir(self.base_dir)
        self.ion_numbers = ion_numbers
        self.species = species
        self.species_paths = []
        ion_atomss, species_atoms = [], []
        # 读取离子晶体各组分的原子数，并在日志文件中记录
        for ion, number in zip(self.species, self.ion_numbers):
            species_path = os.path.join(self.base_dir, ion)
            self.species_paths.append(species_path)
            species_atom = len(read(species_path))
            species_atoms.append(species_atom)
            ion_atoms = species_atom * number
            ion_atomss.append(ion_atoms)
        self.cell_atoms = sum(ion_atomss)
        logging.info(
            f"The components of ions {self.species} in the ionic crystal are {self.ion_numbers}"
        )
        logging.info(
            f"The number of atoms for each ion is: {species_atoms}, and the total number of atoms is {self.cell_atoms}"
        )
        self.generation_dir = os.path.join(self.base_dir, "1_generated")
        os.makedirs(self.generation_dir, exist_ok=True)
        self.POSCAR_dir = os.path.join(self.base_dir, "1_generated", "POSCAR_Files")
        self.primitive_cell_dir = os.path.join(self.base_dir, "1_generated", "primitive_cell")

    def _sequentially_read_files(self, directory: str, prefix_name: str):
        """
        Private method:
        Extract numbers from file names, convert them to integers, sort them by sequence, and return a list containing both indexes and file names

        :params
            directory: The directory where the files are located.
            prefix_name: The prefix of the file names to be processed, e.g., 'POSCAR_'.
        """
        # 获取dir文件夹中所有以prefix_name开头的文件，在此实例中为POSCAR_
        files = [f for f in os.listdir(directory) if f.startswith(prefix_name)]
        file_index_pairs = []
        for filename in files:
            index_part = filename[len(prefix_name) :]  # 选取去除前缀'POSCAR_'的数字
            if index_part.isdigit():  # 确保剩余部分全是数字
                index = int(index_part)
                file_index_pairs.append((index, filename))
        file_index_pairs.sort(key=lambda pair: pair[0])
        return file_index_pairs

    def generate_structures(
        self, num_per_group: int = 100, space_groups_limit: int = 230
    ):
        """
        Based on the provided ion species and corresponding numbers, use pyxtal to randomly generate ion crystal structures based on crystal space groups.
        :params
            num_per_group: The number of POSCAR files to be generated for each space group, default is 100.
            space_groups_limit: The maximum number of space groups to be searched, default is 230, which is the total number of space groups.
        """
        # 如果目录不存在，则创建POSCAR_Files文件夹
        os.makedirs(self.POSCAR_dir, exist_ok=True)
        total_count = 0  # 用于给生成的POSCAR文件计数
        assert 1 <= space_groups_limit <= 230, "Space group number out of range!"
        if space_groups_limit:  
            # 限制空间群搜索范围，以节约测试时间
            space_groups = space_groups_limit
        else:  
            # 否则搜索所有的230个空间群
            space_groups = 230
        group_counts, group_exceptions = [], []
        for space_group in range(1, space_groups + 1):
            logging.info(f"Space group: {space_group}")
            group_count, exception_message = 0, "None"
            # 参数num_per_group确定对每个空间群所要生成的POSCAR结构文件个数
            for i in range(num_per_group):
                try:
                    # 调用pyxtal类
                    pyxtal_structure = pyxtal(molecular=True)
                    # 根据阴阳离子结构文件与对应的配比以及空间群信息随机生成离子晶体，N取100以上
                    pyxtal_structure.from_random(
                        dim=3,
                        group=space_group,
                        species=self.species_paths,
                        numIons=self.ion_numbers,
                        conventional=False,
                    )
                    # 生成POSCAR_n文件
                    POSCAR_path = os.path.join(
                        self.POSCAR_dir, f"POSCAR_{total_count}"
                    )
                    pyxtal_structure.to_file(POSCAR_path, fmt="poscar")
                    total_count += 1
                    group_count += 1
                except (RuntimeError, Comp_CompatibilityError, Symm_CompatibilityError) as e:
                    # 捕获对于某一空间群生成结构的运行时间过长、组成兼容性错误、对称性兼容性错误等异常，使结构生成能够完全进行而不中断
                    logging.error(f"Generating structure error: {e}")
                    # 记录异常类型并跳出当前空间群的生成循环
                    exception_message = type(e).__name__
                    break
            group_counts.append(group_count)
            group_exceptions.append(exception_message)
            logging.info(f" {group_count} POSCAR generated.")
        generation_csv_file = os.path.join(self.generation_dir, 'generation.csv')
        # 写入排序后的 .csv 文件
        with open(generation_csv_file, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            # 动态生成表头
            header = ["Space_group", "POSCAR_num", "Bad_num", "Exception"]
            writer.writerow(header)
            # 写入排序后的数
            for space_group, group_count, group_exception in zip(
                range(1, space_groups + 1), group_counts, group_exceptions
            ):
                writer.writerow([space_group, group_count, 0, group_exception])
        # 保存group_counts供后续使用
        self.group_counts = group_counts
        logging.info(
            f"Using pyxtal.from_random, {total_count} ion crystal structures were randomly generated based on crystal space groups."
        )

    def _single_phonopy_processing(self, filename):
        """
        Private method: 
        Process a single POSCAR file using phonopy to generate symmetric primitive cells and conventional cells.

        :params
            filename: The name of the POSCAR file to be processed.
        """
        # 按顺序处理POSCAR文件，首先复制一份无数字后缀的POSCAR文件
        shutil.copy(f"{self.POSCAR_dir}/{filename}", f"{self.POSCAR_dir}/POSCAR")
        try:
            subprocess.run(["nohup", "phonopy", "--symmetry", "POSCAR"], check=True)
        except subprocess.CalledProcessError as e:
            # 新增：捕获phonopy执行错误
            logging.error(f"Phonopy execution failed for {filename}: {str(e)}")
            raise

        # 将phonopy生成的PPOSCAR（对称化原胞）和BPOSCAR（对称化常规胞）放到对应的文件夹中，并将文件名改回POSCAR_index
        shutil.move(
            f"{self.POSCAR_dir}/PPOSCAR", f"{self.primitive_cell_dir}/{filename}"
        )
        cell_atoms = len(read(f"{self.primitive_cell_dir}/{filename}"))
        
        # 检查生成的POSCAR中的原子数，如果不匹配则删除该POSCAR并在日志中记录
        if cell_atoms != self.cell_atoms:
            error_message = f"Atom number mismatch ({cell_atoms} vs {self.cell_atoms})"
            print(f"{filename} - {error_message}")
            
            # 新增：回溯空间群归属
            poscar_index = int(filename.split('_')[1])  # 提取POSCAR编号
            space_group = self._find_space_group(poscar_index)
            
            # 更新CSV文件
            csv_path = os.path.join(self.generation_dir, 'generation.csv')
            with open(csv_path, 'r') as f:
                reader = csv.reader(f)
                rows = list(reader)
            
            # 更新对应空间群的Bad_num和Exception
            for row in rows[1:]:  # 跳过表头
                if int(row[0]) == space_group:
                    row[2] = str(int(row[2]) + 1)
                    row[3] = "AtomNumberError"
                    break
            # 将更新的信息写入 .csv 文件
            with open(csv_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerows(rows)
            # 删除原子数不匹配的POSCAR
            os.remove(f"{self.primitive_cell_dir}/{filename}")

    def _find_space_group(self, poscar_index: int) -> int:
        """
        Private method:
        Find the space group for a given POSCAR index based on the group_counts.

        :params
            poscar_index: The index of the POSCAR file to find the space group for.

        :return: The space group number corresponding to the POSCAR index.
        """
        cumulative = 0
        for idx, count in enumerate(self.group_counts, start=1):
            if cumulative <= poscar_index < cumulative + count:
                return idx
            cumulative += count
        raise ValueError(f"POSCAR {poscar_index} not found in any space group")
    
    def phonopy_processing(self):
        """
        Use phonopy to check and generate symmetric primitive cells, reducing the complexity of subsequent optimization calculations, and preventing pyxtal.from_random from generating double proportioned supercells.
        """
        os.makedirs(self.primitive_cell_dir, exist_ok=True)
        logging.info("The necessary files are fully prepared.")
        POSCAR_file_index_pairs = self._sequentially_read_files(
            self.POSCAR_dir, prefix_name="POSCAR_"
        )
        # 改变工作目录为POSCAR_Files，便于运行shell命令进行phonopy对称性检查和原胞与常规胞的生成
        os.chdir(self.POSCAR_dir)
        try:
            logging.info("Start running phonopy processing ...")
            for _, filename in POSCAR_file_index_pairs:
                self._single_phonopy_processing(filename=filename)
            # 在 phonopy 成功进行对称化处理后，删除 1_generated/POSCAR_Files 文件夹以节省空间
            logging.info(
                "The phonopy processing has been completed!!\nThe symmetrized primitive cells have been saved in POSCAR format to the primitive_cell folder."
            )
            shutil.rmtree(self.POSCAR_dir)
        except FileNotFoundError:
            logging.error(
                "There are no POSCAR structure files after generating.\nPlease check the error during generation"
            )
            raise FileNotFoundError(
                "There are no POSCAR structure files after generating.\nPlease check the error during generation"
            )
        

    def dpdisp_mlp_tasks(self, machine: str, resources: str, nodes: int = 1):
        """
        Based on the dpdispatcher module, prepare and submit files for optimization on remote server or local machine.

        params:
            machine: The machine configuration file for dpdispatcher, can be in JSON or YAML format.
            resources: The resources configuration file for dpdispatcher, can be in JSON or YAML format.
            nodes: The number of nodes to be used for optimization, default is 1.
        """
        # 调整工作目录，减少错误发生
        os.chdir(self.primitive_cell_dir)
        # 准备dpdispatcher运行所需的文件，将其复制到primitive_cell文件夹中
        self.required_files = [self.mlp_opt_file, self.model_file]
        for file in self.required_files:
            shutil.copy(file, self.primitive_cell_dir)
        # 读取machine和resources的参数
        if machine.endswith(".json"):
            machine = Machine.load_from_json(machine)
        elif machine.endswith(".yaml"):
            machine = Machine.load_from_yaml(machine)
        else:
            raise KeyError("Not supported machine file type")
        if resources.endswith(".json"):
            resources = Resources.load_from_json(resources)
        elif resources.endswith(".yaml"):
            resources = Resources.load_from_yaml(resources)
        else:
            raise KeyError("Not supported resources file type")
        # 由于dpdispatcher对于远程服务器以及本地运行的forward_common_files的默认存放位置不同，因此需要预先进行判断，从而不改动优化脚本
        machine_inform = machine.serialize()
        resources_inform = resources.serialize()
        if machine_inform["context_type"] == "SSHContext":
            # 如果调用远程服务器，则创建二级目录
            parent = "data/"
        elif machine_inform["context_type"] == "LocalContext":
            # 如果在本地运行作业，则只在后续创建一级目录
            parent = ""
            if (
                machine_inform["batch_type"] == "Shell"
                and resources_inform["gpu_per_node"] != 0
            ):
                # 如果是本地运行，则根据显存占用率阈值，等待可用的GPU
                selected_gpu = _wait_for_gpu(memory_percent_threshold=40, wait_time=600)
                os.environ["CUDA_VISIBLE_DEVICES"] = str(selected_gpu)

        from dpdispatcher import Task, Submission

        # 依次读取primitive_cell文件夹中的所有POSCAR文件和对应的序号
        primitive_cell_file_index_pairs = self._sequentially_read_files(
            self.primitive_cell_dir, prefix_name="POSCAR_"
        )
        total_files = len(primitive_cell_file_index_pairs)
        logging.info(f"The total number of POSCAR files to be optimized: {total_files}")
        # 创建一个嵌套列表来存储每个GPU的任务并将文件平均依次分配给每个GPU
        # 例如：对于10个结构文件任务分发给4个GPU的情况，则4个GPU领到的任务分别[0, 4, 8], [1, 5, 9], [2, 6], [3, 7], 便于快速分辨GPU与作业的分配关系
        node_jobs = [[] for _ in range(nodes)]
        for index, _ in primitive_cell_file_index_pairs:
            node_index = index % nodes
            node_jobs[node_index].append(index)
        task_list = []
        for pop in range(nodes):
            remote_task_dir = f"{parent}pop{pop}"
            command = "python mlp_opt.py"
            forward_files = ["mlp_opt.py", "model.pt"]
            backward_files = ["log", "err"]
            # 将mlp_opt.py和model.pt复制一份到task_dir下
            task_dir = os.path.join(self.primitive_cell_dir, f"{parent}pop{pop}")
            os.makedirs(task_dir, exist_ok=True)
            for file in forward_files:
                shutil.copyfile(
                    f"{self.primitive_cell_dir}/{file}", f"{task_dir}/{file}"
                )
            for job_i in node_jobs[pop]:
                # 将分配好的POSCAR文件添加到对应的上传文件中
                forward_files.append(f"POSCAR_{job_i}")
                # 每个POSCAR文件在优化后都取回对应的CONTCAR和OUTCAR输出文件
                backward_files.append(f"CONTCAR_{job_i}")
                backward_files.append(f"OUTCAR_{job_i}")
                shutil.copyfile(
                    f"{self.primitive_cell_dir}/POSCAR_{job_i}",
                    f"{task_dir}/POSCAR_{job_i}",
                )
                shutil.copyfile(
                    f"{self.primitive_cell_dir}/POSCAR_{job_i}",
                    f"{task_dir}/ori_POSCAR_{job_i}",
                )

            task = Task(
                command=command,
                task_work_path=remote_task_dir,
                forward_files=forward_files,
                backward_files=backward_files,
            )
            task_list.append(task)

        submission = Submission(
            work_base=self.primitive_cell_dir,
            machine=machine,
            resources=resources,
            task_list=task_list,
        )
        submission.run_submission()

        # 创建用于存放优化后文件的 mlp_optimized 目录
        optimized_dir = os.path.join(self.base_dir, "2_mlp_optimized")
        os.makedirs(optimized_dir, exist_ok=True)
        for pop in range(nodes):
            # 从传回 primitive_cell 目录下的 pop 文件夹中将结果文件取到 mlp_optimized 目录
            task_dir = os.path.join(self.primitive_cell_dir, f"{parent}pop{pop}")
            # 按照给定的 POSCAR 结构文件按顺序读取 CONTCAR 和 OUTCAR 文件并复制
            task_file_index_pairs = self._sequentially_read_files(
                task_dir, prefix_name="POSCAR_"
            )
            for index, _ in task_file_index_pairs:
                shutil.copyfile(
                    f"{task_dir}/CONTCAR_{index}", f"{optimized_dir}/CONTCAR_{index}"
                )
                shutil.copyfile(
                    f"{task_dir}/OUTCAR_{index}", f"{optimized_dir}/OUTCAR_{index}"
                )
            # 在成功完成机器学习势优化后，删除 1_generated/primitive_cell/{parent}/pop{n} 文件夹以节省空间
            shutil.rmtree(task_dir)
        if machine_inform["context_type"] == "SSHContext":
            # 如果调用远程服务器，则删除data级目录
            shutil.rmtree(os.path.join(self.primitive_cell_dir, parent))
        # 完成后删除不必要的运行文件以节省空间，并记录优化完成的信息
        for file in ["mlp_opt.py", "model.pt"]:
            os.remove(f"{self.primitive_cell_dir}/{file}")
        logging.info("Batch optimization completed!!!")


def _get_available_gpus(memory_percent_threshold=40):
    """
    Private method:
    Get available GPUs with memory usage below the specified threshold.

    params:
        memory_percent_threshold (int): The threshold for GPU memory usage percentage.
    """
    try:
        # 获取 nvidia-smi 的输出
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            encoding="utf-8",
        )
        available_gpus = []
        for line in output.strip().split("\n"):
            index, memory_used, memory_total = map(int, line.split(","))
            memory_used_percent = memory_used / memory_total * 100
            # 判断内存负载是否低于阈值
            if memory_used_percent < memory_percent_threshold:
                available_gpus.append((index, memory_used_percent))
        # 根据内存负载百分比排序，负载小的优先
        available_gpus.sort(key=lambda x: x[1])
        # 只返回 GPU 索引
        return [gpu[0] for gpu in available_gpus]
    except subprocess.CalledProcessError as e:
        logging.error(f"Error while getting GPU info: {e}")
        return []
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return []


def _wait_for_gpu(memory_percent_threshold=40, wait_time=300):
    """
    Private method:
    Wait until a GPU is available with memory usage below the specified threshold.
    params:
        memory_percent_threshold (int): The threshold for GPU memory usage percentage.
        wait_time (int): The time to wait before checking again, in seconds.
    """
    while True:
        available_gpus = _get_available_gpus(memory_percent_threshold)
        logging.info(f"Available GPU: {available_gpus}")
        if available_gpus:
            selected_gpu = available_gpus[0]
            logging.info(f"Using GPU: {selected_gpu}")
            return selected_gpu
        else:
            logging.info(f"No available GPUs found. Waiting for {wait_time} second ...")
            time.sleep(wait_time)  # 等待 5 分钟
