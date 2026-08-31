"""Crystal structure generation and optimization module.

This module provides functionality for generating and optimizing crystal structures
using various methods including CALYPSO, USPEX, and other crystal structure prediction tools.
"""

import os
import sys
import csv
import time
import uuid
import signal
import shlex
import shutil
import psutil
import logging
import subprocess
from typing import List
from ase.io import read
from pathlib import Path
from pyxtal import pyxtal
from pyxtal.msg import Comp_CompatibilityError, Symm_CompatibilityError

from ion_CSP.log_and_time import redirect_dpdisp_logging, machine_resources_prep


class CrystalGenerator:

    def __init__(self, work_dir: Path, ion_numbers: List[int], species: List[str]):
        """
        Initialize the class based on the provided ionic crystal composition structure files and corresponding composition numbers.

        :params
            work_dir: The working directory where the ionic crystal structure files are located.
            ion_numbers: A list of integers representing the number of each ion in the ionic crystal.
            species: A list of strings representing the species of ions in the ionic crystal.
        """
        self.base_dir = work_dir.resolve()
        # 设置dpdispatcher日志文件存放路径
        dpdisp_log_path = self.base_dir / "dpdispatcher.log"
        redirect_dpdisp_logging(dpdisp_log_path)
        package_dir = Path(__file__).resolve().parent
        self.mlp_opt_file = package_dir / "mlp_opt.py"
        self.model_file = package_dir / "model" / "model.pt"
        # 记录离子晶体的组成信息
        self.ion_numbers = ion_numbers
        self.species = species
        self.species_paths: List[Path] = []
        ion_atomss, species_atoms = [], []
        logging.info(
            f"The components of ions {self.species} in the ionic crystal are {self.ion_numbers}"
        )
        # 读取离子晶体各组分的原子数，并在日志文件中记录
        for ion, number in zip(self.species, self.ion_numbers):
            species_path = self.base_dir / ion
            self.species_paths.append(species_path)
            species_atom = len(read(species_path))
            species_atoms.append(species_atom)
            ion_atoms = species_atom * number
            ion_atomss.append(ion_atoms)
        self.cell_atoms = sum(ion_atomss)
        logging.info(
            f"The number of atoms for each ion is: {species_atoms}, and the total number of atoms is {self.cell_atoms}"
        )
        # 创建用于存放生成结构文件的目录
        self.generation_dir = self.base_dir / "1_generated"
        self.POSCAR_dir = self.base_dir / "1_generated/POSCAR_Files"
        self.primitive_cell_dir = self.base_dir / "1_generated/primitive_cell"
        self.generation_dir.mkdir(exist_ok=True)


    def _sequentially_read_files(self, directory: Path, prefix_name: str = "POSCAR_"):
        """
        Private method:
        Extract numbers from file names, convert them to integers, sort them by sequence, and return a list containing both indexes and file names
        """
        # 获取dir文件夹中所有以prefix_name开头的文件，在此实例中为POSCAR_
        files = [f for f in directory.iterdir() if f.name.startswith(prefix_name)]
        file_index_pairs = []
        for file in files:
            index_part = file.name[len(prefix_name) :]  # 选取去除前缀'POSCAR_'的数字
            if index_part.isdigit():  # 确保剩余部分全是数字
                index = int(index_part)
                file_index_pairs.append((index, file.name))
        file_index_pairs.sort(key=lambda pair: pair[0])
        if not file_index_pairs:
            logging.error(f"No files found with prefix '{prefix_name}' in {directory}")
            raise FileNotFoundError(
                f"No files found with prefix '{prefix_name}' in {directory}"
            )
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
        # 限制空间群搜索范围以节约测试时间，否则搜索所有的230个空间群
        assert 1 <= space_groups_limit <= 230 and isinstance(space_groups_limit, int), (
            "Space group number should be an integer between 1 and 230."
        )
        space_groups = space_groups_limit if space_groups_limit else 230

        self.POSCAR_dir.mkdir(exist_ok=True)
        group_counts, group_exceptions = [], []
        total_count = 0  # 用于给生成的POSCAR文件计数
        for space_group in range(1, space_groups + 1):
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
                        species=[str(p) for p in self.species_paths],
                        numIons=self.ion_numbers,
                        conventional=False,
                    )
                    # 生成POSCAR_n文件
                    POSCAR_path = self.POSCAR_dir / f"POSCAR_{total_count}"
                    pyxtal_structure.to_file(POSCAR_path, fmt="poscar")
                    total_count += 1
                    group_count += 1
                # 捕获对于某一空间群生成结构的运行时间过长、组成兼容性错误、对称性兼容性错误等异常，使结构生成能够完全进行而不中断
                except (
                    RuntimeError,
                    Comp_CompatibilityError,
                    Symm_CompatibilityError,
                ) as e:
                    # 记录异常类型并跳出当前空间群的生成循环
                    exception_message = type(e).__name__
                    break
            group_counts.append(group_count)
            group_exceptions.append(exception_message)
            # 每 20 个空间群汇总一次进度，避免逐群刷屏（明细见 generation.csv）
            if space_group % 20 == 0 or space_group == space_groups:
                logging.info(
                    f"Progress: space groups 1-{space_group}/{space_groups} processed, "
                    f"{total_count} POSCAR generated so far."
                )
        # 写入排序后的 .csv 文件
        self.generation_csv_file = self.generation_dir / "generation.csv"
        with self.generation_csv_file.open(
            "w", newline="", encoding="utf-8"
        ) as csv_file:
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


    def _single_phonopy_processing(self, filename: str) -> bool:
        """
        Private method:
        Process a single POSCAR file using phonopy to generate symmetric primitive cells and conventional cells.

        :params
            filename: The name of the POSCAR file to be processed.

        :return: True if the file was deleted due to an atom number mismatch, otherwise False.
        """
        try:
            # 按顺序将生成的 POSCAR_n 文件复制为无数字后缀的 POSCAR 文件以供 phonopy 使用
            src_path = self.POSCAR_dir / filename
            poscar_temp = self.POSCAR_dir / "POSCAR"
            shutil.copyfile(str(src_path), str(poscar_temp))
            subprocess.run(
                ["phonopy", "--symmetry", str(poscar_temp)],
                cwd=str(self.POSCAR_dir),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            # 读取生成的 PPOSCAR，并将文件名改回 POSCAR_index
            pposcar_path = self.POSCAR_dir / "PPOSCAR"
            dst_path = self.primitive_cell_dir / filename
            shutil.move(str(pposcar_path), str(dst_path))

            # 检查生成的 POSCAR 中的原子数，如果不匹配则删除该 POSCAR 并在日志中记录
            cell_atoms = len(read(dst_path))
            if cell_atoms != self.cell_atoms:
                # 回溯空间群归属
                poscar_index = int(filename.split("_")[1])  # 提取POSCAR编号
                space_group = self._find_space_group(poscar_index)
                # 更新 .csv 文件
                with self.generation_csv_file.open("r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    rows = list(reader)
                # 更新对应空间群的 Bad_num 和 Exception
                for row in rows[1:]:  # 跳过表头
                    if int(row[0]) == space_group:
                        row[2] = str(int(row[2]) + 1)
                        row[3] = "AtomNumberError"
                        break
                # 将更新的信息写入 .csv 文件
                with self.generation_csv_file.open(
                    "w", newline="", encoding="utf-8"
                ) as f:
                    writer = csv.writer(f)
                    writer.writerows(rows)
                # 删除原子数不匹配的POSCAR
                dst_path.unlink()
                # 逐条 WARNING 会在大批量生成时刷爆日志，明细已记入 generation.csv，
                # 此处仅返回标志由调用方汇总统计。
                return True
            return False

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            # 新增：捕获phonopy执行错误
            logging.error(f"Phonopy execution failed for {filename}: {str(e)}")
            raise


    def phonopy_processing(self):
        """
        Use phonopy to check and generate symmetric primitive cells, reducing the complexity of subsequent optimization calculations, and preventing pyxtal.from_random from generating double proportioned supercells.
        """
        self.primitive_cell_dir.mkdir(exist_ok=True)
        logging.info("The necessary files are fully prepared.")
        POSCAR_file_index_pairs = self._sequentially_read_files(
            self.POSCAR_dir, prefix_name="POSCAR_"
        )
        # 开始对每个 POSCAR 文件进行 phonopy 处理
        total_files = len(POSCAR_file_index_pairs)
        logging.info(f"Start running phonopy processing for {total_files} POSCAR files ...")
        deleted_count = 0
        for _, filename in POSCAR_file_index_pairs:
            if self._single_phonopy_processing(filename=filename):
                deleted_count += 1
        # 汇总原子数不匹配而删除的结构数量（明细见 generation.csv 的 Bad_num / Exception 列）
        if deleted_count:
            logging.warning(
                f"Deleted {deleted_count}/{total_files} POSCAR files due to atom number "
                f"mismatch (details recorded in generation.csv)."
            )
        # 在 phonopy 成功进行对称化处理后，删除 1_generated/POSCAR_Files 文件夹以节省空间
        logging.info(
            f"The phonopy processing has been completed!! {total_files - deleted_count} "
            "symmetrized primitive cells have been saved in POSCAR format to the primitive_cell folder."
        )
        shutil.rmtree(self.POSCAR_dir)


    def dpdisp_mlp_tasks(
        self,
        machine_path: str,
        resources_path: str,
        nodes: int = 1,
        backend: str = "deepmd",
        python_executable: str = "python",
        model: str = "model.pt",
        device: str = None,
        workers: int = 0,
    ):
        """
        Based on the dpdispatcher module, prepare and submit files for optimization on remote server or local machine.

        params:
            machine: The machine configuration file for dpdispatcher, can be in JSON or YAML format.
            resources: The resources configuration file for dpdispatcher, can be in JSON or YAML format.
            nodes: The number of nodes to be used for optimization, default is 1.
        """
        # 读取machine和resources的参数
        machine, resources, parent = machine_resources_prep(
            machine_path=machine_path, resources_path=resources_path
        )
        if (
            machine.serialize()["batch_type"] == "Shell"
            and resources.serialize()["gpu_per_node"] != 0
        ):
            # 如果是本地运行，则根据显存占用率阈值，等待可用的GPU
            selected_gpu = _wait_for_gpu(memory_percent_threshold=40, wait_time=600)
            os.environ["CUDA_VISIBLE_DEVICES"] = str(selected_gpu)

        from dpdispatcher import Task, Submission

        # 准备dpdispatcher运行所需的文件，将其复制到primitive_cell文件夹中
        dpdisp_base = self.primitive_cell_dir
        backend = backend.lower()
        if backend not in {"deepmd", "dpa4", "dpa4_ion_ft", "mattersim"}:
            raise ValueError(f"Unsupported MLP backend: {backend}")
        model_argument = model
        self.required_files = [self.mlp_opt_file]
        if backend == "deepmd":
            self.required_files.append(self.model_file)
            model_argument = "model.pt"
        else:
            model_path = Path(model)
            bundled_model_path = self.mlp_opt_file.parent / "model" / model_path.name
            if not model_path.is_file() and bundled_model_path.is_file():
                model_path = bundled_model_path
        if backend != "deepmd" and model_path.is_file():
            self.required_files.append(model_path)
            model_argument = model_path.name
        for file in self.required_files:
            shutil.copy(str(file), str(dpdisp_base))
        # 依次读取primitive_cell文件夹中的所有POSCAR文件和对应的序号
        primitive_cell_file_index_pairs = self._sequentially_read_files(
            dpdisp_base, prefix_name="POSCAR_"
        )
        total_files = len(primitive_cell_file_index_pairs)
        logging.info(f"The total number of POSCAR files to be optimized: {total_files}")
        # 创建一个嵌套列表来存储每个GPU的任务并将文件平均依次分配给每个GPU
        # 例如：对于10个结构文件任务分发给4个GPU的情况，则4个GPU领到的任务分别[0, 4, 8], [1, 5, 9], [2, 6], [3, 7], 便于快速分辨GPU与作业的分配关系
        node_jobs = [[] for _ in range(nodes)]
        for index, _ in primitive_cell_file_index_pairs:
            node_index = index % nodes
            node_jobs[node_index].append(index)
        # 为每个GPU创建一个Task对象，并将对应的文件添加到forward_files和backward_files
        task_list = []
        for pop in range(nodes):
            remote_task_dir = f"{parent}pop{pop}"
            forward_files = [file.name for file in self.required_files]
            backward_files = ["log", "err"]

            # 将mlp_opt.py和model.pt复制一份到task_dir下
            task_dir = dpdisp_base / f"{parent}pop{pop}"
            task_dir.mkdir(exist_ok=True, parents=True)
            for file in forward_files:
                src_path = dpdisp_base / file
                dst_path = task_dir / file
                shutil.copyfile(str(src_path), str(dst_path))
            for job_i in node_jobs[pop]:
                # 将分配好的POSCAR文件添加到对应的上传文件中
                poscar_name = f"POSCAR_{job_i}"
                forward_files.append(poscar_name)
                # 每个POSCAR文件在优化后都取回对应的CONTCAR和OUTCAR输出文件
                backward_files.append(f"CONTCAR_{job_i}")
                backward_files.append(f"OUTCAR_{job_i}")
                shutil.copyfile(
                    str(dpdisp_base / poscar_name), str(task_dir / poscar_name)
                )
                shutil.copyfile(
                    str(dpdisp_base / f"POSCAR_{job_i}"),
                    str(task_dir / f"ori_{poscar_name}"),
                )

            command_parts = [
                python_executable,
                "mlp_opt.py",
                "--backend",
                backend,
                "--model",
                model_argument,
            ]
            if device:
                command_parts.extend(["--device", device])
            if workers:
                command_parts.extend(["--workers", str(workers)])
            task = Task(
                command=" ".join(shlex.quote(part) for part in command_parts),
                task_work_path=remote_task_dir,
                forward_files=forward_files,
                backward_files=backward_files,
            )
            task_list.append(task)

        # 创建 submission 实例并保存为实例变量（用于后续终止）
        self._submission = Submission(
            work_base=str(dpdisp_base),
            machine=machine,
            resources=resources,
            task_list=task_list,
        )


        # 执行提交（阻塞直到任务完成）
        # aborted 标志区分「正常结束」与「被信号/异常中断」：
        # 被 kill 时若仍遍历数万个结构去回收不存在的 CONTCAR/OUTCAR，会瞬间刷出
        # 数万行 "Missing output files" ERROR 撑爆日志（曾达 18MB），且毫无意义。
        # 使用 try/except/else：回收逻辑放在 else 中，仅在提交成功（无异常）时执行。
        # 所有中断/异常分支都会终止子进程并重新抛出，从而跳过回收，避免海量无效日志。
        try:
            logging.info("Submitting tasks to dpdispatcher...")
            self._submission.run_submission()
        except KeyboardInterrupt:
            # 捕获 KeyboardInterrupt，终止任务后重新抛出让 StatusLogger 处理
            logging.info("Received KeyboardInterrupt, terminating tasks...")
            self._terminate_tasks()
            raise  # 重新抛出，让 StatusLogger 的信号处理器处理状态更新
        except SystemExit:
            # StatusLogger 的信号处理器（SIGINT/SIGTERM）会调用 sys.exit()，抛出
            # SystemExit。它属于 BaseException，不会被下面的 except Exception 捕获，
            # 需单独处理，否则会执行无意义的输出回收并刷爆日志。
            logging.warning(
                "Submission interrupted by termination signal, terminating tasks "
                "and skipping output recovery."
            )
            self._terminate_tasks()
            raise
        except Exception as e:
            logging.error(f"Submission failed with error: {e}")
            self._terminate_tasks()
            raise
        else:
            # 仅在提交正常结束时回收优化结果
            self._recover_optimized_outputs(dpdisp_base, parent, nodes)
            if machine.serialize()["context_type"] == "SSHContext":
                # 如果调用远程服务器，则删除 data 级目录
                shutil.rmtree(dpdisp_base / parent, ignore_errors=True)
            # 完成后删除不必要的运行文件以节省空间
            for file in self.required_files:
                (dpdisp_base / file.name).unlink(missing_ok=True)
            logging.info("Batch optimization completed!!!")
            # 清理内部引用
            self._submission = None


    def _recover_optimized_outputs(self, dpdisp_base: Path, parent: str, nodes: int):
        """
        Private method:
        Collect CONTCAR/OUTCAR outputs from each pop task directory into 2_mlp_optimized.
        Missing outputs are aggregated into a single summary log instead of one error per
        file, which previously bloated the log to tens of thousands of lines on failure.
        """
        # 创建用于存放优化后文件的 mlp_optimized 目录
        optimized_dir = self.base_dir / "2_mlp_optimized"
        optimized_dir.mkdir(exist_ok=True)
        recovered, missing = 0, []
        for pop in range(nodes):
            # 从传回 primitive_cell 目录下的 pop 文件夹中将结果文件取到 mlp_optimized 目录
            task_dir = dpdisp_base / f"{parent}pop{pop}"
            # 按照给定的 POSCAR 结构文件按顺序读取 CONTCAR 和 OUTCAR 文件并复制
            task_file_index_pairs = self._sequentially_read_files(
                task_dir, prefix_name="POSCAR_"
            )
            for index, _ in task_file_index_pairs:
                try:
                    shutil.copyfile(
                        str(task_dir / f"CONTCAR_{index}"),
                        str(optimized_dir / f"CONTCAR_{index}"),
                    )
                    shutil.copyfile(
                        str(task_dir / f"OUTCAR_{index}"),
                        str(optimized_dir / f"OUTCAR_{index}"),
                    )
                    recovered += 1
                except FileNotFoundError:
                    missing.append(index)
                    continue
            # 在成功完成机器学习势优化后，删除 pop 文件夹以节省空间
            shutil.rmtree(task_dir)
        # 汇总回收结果，缺失文件只记一条摘要并给出排查提示
        if missing:
            preview = ", ".join(f"POSCAR_{i}" for i in missing[:10])
            more = f" (and {len(missing) - 10} more)" if len(missing) > 10 else ""
            logging.warning(
                f"Recovered {recovered} optimized structures; {len(missing)} POSCAR files "
                f"have no CONTCAR/OUTCAR output: {preview}{more}. "
                "The optimization jobs likely failed or produced no output — "
                "check dpdispatcher.log and the remote task logs."
            )
        else:
            logging.info(f"Recovered {recovered} optimized structures.")


    def _terminate_tasks(self):
        """
        终止当前 submission 的所有任务：先调用 dpdispatcher 官方接口，
        再兜底查找可能残留的 mlp_opt.py 进程（通过工作目录匹配，避免误杀）。
        """
        if not hasattr(self, "_submission") or self._submission is None:
            return

        try:
            machine_info = self._submission.machine.serialize()
        except AttributeError:
            logging.error("Cannot retrieve machine information for termination.")
            return
        context_type = machine_info.get("context_type", "LocalContext")

        # 1. 优先调用 dpdispatcher 的官方 kill 接口（针对记录的 job_id）
        try:
            logging.info("Terminating tasks via dpdispatcher.Submission...")
            for task in self._submission.belonging_tasks:
                if hasattr(task, "job_id") and task.job_id:
                    try:
                        self._submission.machine.kill(task)
                        logging.info(f"Killed task {task.task_hash} (job_id: {task.job_id})")
                    except Exception as e:
                        logging.warning(f"Failed to kill task {task.task_hash}: {e}")
        except Exception as e:
            logging.warning(f"dpdispatcher kill failed: {e}")

        # 2. 兜底：查找本地残留的 mlp_opt.py 进程（工作目录匹配）
        if context_type == "LocalContext":
            remote_root = str(self._submission.machine.context.remote_root)
            logging.info(f"Checking for residual mlp_opt.py processes in {remote_root}...")
            killed_count = 0
            for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
                try:
                    cmdline = proc.info.get("cmdline")
                    cwd = proc.info.get("cwd")
                    # 匹配：命令行含 mlp_opt.py，且工作目录以 remote_root 开头
                    if (
                        cmdline
                        and any("mlp_opt.py" in arg for arg in cmdline)
                        and cwd
                        and cwd.startswith(remote_root)
                    ):
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except psutil.TimeoutExpired:
                            proc.kill()
                        logging.info(f"Killed residual process {proc.pid}: {' '.join(cmdline)}")
                        killed_count += 1
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    psutil.ZombieProcess,
                ):
                    pass
            if killed_count == 0:
                logging.info("No residual mlp_opt.py processes found.")

        elif context_type == "SSHContext":
            # 远程：通过 ssh 执行 pkill，匹配工作目录下的 mlp_opt.py
            remote_profile = machine_info.get("remote_profile", {})
            hostname = remote_profile.get("hostname")
            username = remote_profile.get("username")
            if not hostname or not username:
                logging.warning("Cannot terminate remote tasks: missing remote_profile")
                return
            remote_root = self._submission.machine.context.remote_root
            # pkill -f 匹配命令行中的 mlp_opt.py，限定工作目录（pwdx + grep 预过滤）
            remote_cmd = (
                f'for pid in $(pgrep -f mlp_opt.py); do '
                f'  [[ $(pwdx $pid 2>/dev/null) == *"{remote_root}"* ]] && kill -9 $pid; '
                f'done'
            )
            cmd = ["ssh", f"{username}@{hostname}", remote_cmd]
            logging.info(f"Terminating remote mlp_opt.py tasks on {hostname}...")
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0 or result.returncode == 1:  # 1 = no matching process
                    logging.info("Remote termination command executed.")
                else:
                    logging.warning(f"Remote termination exit code {result.returncode}: {result.stderr}")
            except subprocess.TimeoutExpired:
                logging.error("Remote termination command timed out.")
            except Exception as e:
                logging.error(f"Failed to execute remote termination: {e}")
        else:
            logging.warning(f"Unknown context_type: {context_type}, cannot terminate tasks")


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
            time.sleep(wait_time)  # 等待指定时间后重试
