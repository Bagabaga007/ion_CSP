import os
import csv
import json
import yaml
import shutil
import logging
import importlib.resources
from ase.io import ParseError
from ase.io.vasp import read_vasp_out
from dpdispatcher import Machine, Resources, Task, Submission
from ion_CSP.log_and_time import redirect_dpdisp_logging
from ion_CSP.identify_molecules import identify_molecules, molecules_information


class VaspProcessing:
    def __init__(self, work_dir: str):
        """
        This directory is used to store all the files related to VASP optimizations.

        :params
            work_dir: The working directory where VASP optimization files will be stored.
        """
        redirect_dpdisp_logging(os.path.join(work_dir, "dpdispatcher.log"))
        self.base_dir = work_dir
        os.chdir(self.base_dir)
        self.for_vasp_opt_dir = f"{work_dir}/3_for_vasp_opt"
        self.vasp_optimized_dir = f"{work_dir}/4_vasp_optimized"
        self.param_dir = importlib.resources.files("ion_CSP.param")

    def dpdisp_vasp_optimization_tasks(
        self,
        machine: str,
        resources: str,
        nodes: int = 1,
    ):
        """
        Based on the dpdispatcher module, prepare and submit files for optimization on remote server or local machine.
        :params
            machine: The machine configuration file, which can be in JSON or YAML format.
            resources: The resources configuration file, which can be in JSON or YAML format.
            nodes: The number of nodes to distribute the optimization tasks across.
        """
        # 调整工作目录，减少错误发生
        os.chdir(self.for_vasp_opt_dir)
        # 读取machine.json和resources.json的参数
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
        if machine_inform["context_type"] == "SSHContext":
            # 如果调用远程服务器，则创建二级目录
            parent = "data/"
        elif machine_inform["context_type"] == "LocalContext":
            # 如果在本地运行作业，则只在后续创建一级目录
            parent = ""

        # 获取dir文件夹中所有以prefix_name开头的文件，在此实例中为POSCAR_
        mlp_contcar_files = [
            f for f in os.listdir(self.for_vasp_opt_dir) if f.startswith("CONTCAR_")
        ]
        # 创建一个嵌套列表来存储每个节点的任务并将文件平均依次分配给每个节点
        # 例如：对于10个结构文件任务分发给4个节点的情况，则4个节点领到的任务分别[0, 4, 8], [1, 5, 9], [2, 6], [3, 7]
        node_jobs = [[] for _ in range(nodes)]
        for index, file in enumerate(mlp_contcar_files):
            node_index = index % nodes
            node_jobs[node_index].append(index)
        task_list = []
        for pop in range(nodes):
            forward_files = [
                "INCAR_1",
                "INCAR_2",
                "POTCAR_H",
                "POTCAR_C",
                "POTCAR_N",
                "POTCAR_O",
                "sub_ori.sh",
            ]
            backward_files = ["log", "err"]
            # 将所有参数文件各复制一份到每个 task_dir 目录下
            task_dir = os.path.join(self.for_vasp_opt_dir, f"{parent}pop{pop}")
            os.makedirs(task_dir, exist_ok=True)
            for file in forward_files:
                shutil.copyfile(self.param_dir.joinpath(file), f"{task_dir}/{file}")
            for job_i in node_jobs[pop]:
                # 将分配好的POSCAR文件添加到对应的上传文件中
                forward_files.append(mlp_contcar_files[job_i])
                vasp_dir = mlp_contcar_files[job_i].split("CONTCAR_")[1]
                # 每个POSCAR文件在优化后都取回对应的CONTCAR和OUTCAR输出文件
                backward_files.append(f"{vasp_dir}/fine/*")
                backward_files.append(f"{vasp_dir}/*")
                shutil.copyfile(
                    f"{self.for_vasp_opt_dir}/{mlp_contcar_files[job_i]}",
                    f"{task_dir}/{mlp_contcar_files[job_i]}",
                )

            remote_task_dir = f"{parent}pop{pop}"
            command = "chmod +x sub_ori.sh && ./sub_ori.sh"
            task = Task(
                command=command,
                task_work_path=remote_task_dir,
                forward_files=forward_files,
                backward_files=backward_files,
            )
            task_list.append(task)

        submission = Submission(
            work_base=self.for_vasp_opt_dir,
            machine=machine,
            resources=resources,
            task_list=task_list,
        )
        submission.run_submission()

        # 创建用于存放优化后文件的 4_vasp_optimized 目录
        os.makedirs(self.vasp_optimized_dir, exist_ok=True)
        mlp_outcar_files = [
            f for f in os.listdir(self.for_vasp_opt_dir) if f.startswith("OUTCAR_")
        ]
        for mlp_contcar, mlp_outcar in zip(mlp_contcar_files, mlp_outcar_files):
            shutil.copyfile(
                f"{self.for_vasp_opt_dir}/{mlp_contcar}",
                f"{self.vasp_optimized_dir}/{mlp_contcar}",
            )
            shutil.copyfile(
                f"{self.for_vasp_opt_dir}/{mlp_outcar}",
                f"{self.vasp_optimized_dir}/{mlp_outcar}",
            )
        for pop in range(nodes):
            # 从传回的 pop 文件夹中将结果文件取到 4_vasp_optimized 目录
            task_dir = os.path.join(self.for_vasp_opt_dir, f"{parent}pop{pop}")
            for job_i in node_jobs[pop]:
                vasp_dir = mlp_contcar_files[job_i].split("CONTCAR_")[1]
                shutil.copytree(f"{task_dir}/{vasp_dir}", f"{self.vasp_optimized_dir}/{vasp_dir}", dirs_exist_ok=True)
            # 在成功完成 VASP 分步优化后，删除 3_for_vasp_opt/{parent}/pop{n} 文件夹以节省空间
            shutil.rmtree(task_dir)
        if machine_inform["context_type"] == "SSHContext":
            # 如果调用远程服务器，则删除data级目录
            shutil.rmtree(os.path.join(self.for_vasp_opt_dir, parent))
        logging.info("Batch VASP optimization completed!!!")

    def dpdisp_vasp_relaxation_tasks(
        self,
        machine: str,
        resources: str,
        nodes: int = 1,
    ):
        """
        Based on the dpdispatcher module, prepare and submit files for VASP relaxation on remote server or local machine.
        :params
            machine: The machine configuration file, which can be in JSON or YAML format.
            resources: The resources configuration file, which can be in JSON or YAML format.
            nodes: The number of nodes to distribute the optimization tasks across.
        """
        # 调整工作目录，减少错误发生
        os.chdir(self.vasp_optimized_dir)
        # 读取machine.json和resources.json的参数
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
        if machine_inform["context_type"] == "SSHContext":
            # 如果调用远程服务器，则创建二级目录
            parent = "data/"
        elif machine_inform["context_type"] == "LocalContext":
            # 如果在本地运行作业，则只在后续创建一级目录
            parent = ""

        # 获取dir文件夹中所有以prefix_name开头的文件，在此实例中为POSCAR_
        vasp_optimized_folders = [
            f
            for f in os.listdir(self.vasp_optimized_dir)
            if os.path.isdir(f) and f != "data"
        ]
        # 创建一个嵌套列表来存储每个节点的任务并将文件平均依次分配给每个节点
        # 例如：对于10个结构文件任务分发给4个节点的情况，则4个节点领到的任务分别[0, 4, 8], [1, 5, 9], [2, 6], [3, 7]
        node_jobs = [[] for _ in range(nodes)]
        for index, file in enumerate(vasp_optimized_folders):
            node_index = index % nodes
            node_jobs[node_index].append(index)
        task_list = []
        for pop in range(nodes):
            forward_files = [
                "INCAR_3",
                "POTCAR_H",
                "POTCAR_C",
                "POTCAR_N",
                "POTCAR_O",
                "sub_supple.sh",
            ]
            backward_files = ["log", "err"]
            # 将所有参数文件各复制一份到每个 task_dir 目录下
            task_dir = os.path.join(self.vasp_optimized_dir, f"{parent}pop{pop}")
            os.makedirs(task_dir, exist_ok=True)
            for file in forward_files:
                shutil.copyfile(self.param_dir.joinpath(file), f"{task_dir}/{file}")
            for job_i in node_jobs[pop]:
                # 将分配好的POSCAR文件添加到对应的上传文件中
                vasp_dir = vasp_optimized_folders[job_i]
                fine_optimized_file = f"{vasp_dir}/fine/CONTCAR"
                if os.path.exists(fine_optimized_file):
                    forward_files.append(fine_optimized_file)
                    os.makedirs(
                        os.path.dirname(f"{task_dir}/{fine_optimized_file}"), exist_ok=True
                    )
                    shutil.copyfile(
                        f"{self.vasp_optimized_dir}/{fine_optimized_file}",
                        f"{task_dir}/{fine_optimized_file}",
                    )
                # 每个POSCAR文件在优化后都取回对应的CONTCAR和OUTCAR输出文件
                backward_files.append(f"{vasp_dir}/*")
                backward_files.append(f"{vasp_dir}/fine/*")
                backward_files.append(f"{vasp_dir}/fine/final/*")

            remote_task_dir = f"{parent}pop{pop}"
            command = "chmod +x sub_supple.sh && ./sub_supple.sh"
            task = Task(
                command=command,
                task_work_path=remote_task_dir,
                forward_files=forward_files,
                backward_files=backward_files,
            )
            task_list.append(task)

        submission = Submission(
            work_base=self.vasp_optimized_dir,
            machine=machine,
            resources=resources,
            task_list=task_list,
        )
        submission.run_submission()

        for pop in range(nodes):
            # 从传回的 pop 文件夹中将结果文件取到 4_vasp_optimized 目录
            task_dir = os.path.join(self.vasp_optimized_dir, f"{parent}pop{pop}")
            for job_i in node_jobs[pop]:
                vasp_dir = vasp_optimized_folders[job_i]
                try:
                    shutil.copytree(
                        f"{task_dir}/{vasp_dir}/fine/final",
                        f"{self.vasp_optimized_dir}/{vasp_dir}/fine/final", 
                        dirs_exist_ok=True,
                    )
                except FileNotFoundError:
                    logging.error(
                        f"No final optimization results found for {vasp_dir} in {task_dir}"
                    )
            # 在成功完成 VASP 分步优化后，删除 4_vasp_optimized /{parent}/pop{n} 文件夹以节省空间
            shutil.rmtree(task_dir)
        if machine_inform["context_type"] == "SSHContext":
            # 如果调用远程服务器，则删除data级目录
            shutil.rmtree(os.path.join(self.vasp_optimized_dir, parent))
        logging.info("Batch VASP optimization completed!!!")

    def read_vaspout_save_csv(self, molecules_prior: bool, relaxation: bool = False):
        """
        Read VASP output files in batches and save energy and density to corresponding CSV files in the directory
        """
        os.chdir(self.base_dir)
        vasp_opt_dir = self.vasp_optimized_dir
        numbers, mlp_densities, mlp_energies = [], [], []
        rough_densities, rough_energies = [], []
        fine_densities, fine_energies = [], []
        final_densities, final_energies = [], []
        ions_checks, packing_coefficients = [], []
        for folder in os.listdir(vasp_opt_dir):
            vasp_opt_path = os.path.join(vasp_opt_dir, folder)
            if os.path.isdir(vasp_opt_path):
                mlp_density, number = folder.split("_")[0], folder.split("_")[1]
                numbers.append(number)
                mlp_densities.append(mlp_density)
                # 读取一级目录下的 OUTCAR 文件
                OUTCAR_file_path = os.path.join(vasp_opt_path, "OUTCAR")
                logging.info(f"CONTCAR_{mlp_density}_{number}")
                try:
                    with open(
                        f"{vasp_opt_dir}/OUTCAR_{mlp_density}_{number}"
                    ) as mlp_out:
                        lines = mlp_out.readlines()
                        for line in lines:
                            if "TOTEN" in line:
                                values = line.split()
                                mlp_energy = round(float(values[-2]), 1)
                except FileNotFoundError:
                    logging.error(
                        f"  No avalible MLP OUTCAR_{mlp_density}_{number} found"
                    )
                    mlp_energy = False

                try:
                    rough_atoms = read_vasp_out(OUTCAR_file_path)
                    atoms_volume = rough_atoms.get_volume()  # 体积单位为立方埃（Å³）
                    atoms_masses = sum(
                        rough_atoms.get_masses()
                    )  # 质量单位为原子质量单位(amu)
                    # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
                    rough_density = round(1.66054 * atoms_masses / atoms_volume, 3)
                    rough_energy = round(rough_atoms.get_total_energy(), 1)
                    logging.info(
                        f"  MLP_Density: {mlp_density}, MLP_Energy: {mlp_energy}"
                    )
                    logging.info(
                        f"  Rough_Density: {rough_density}, Rough_Energy: {rough_energy}"
                    )
                except (ParseError, FileNotFoundError):
                    logging.error(
                        f"  Unfinished optimization job of CONTCAR_{mlp_density}_{number}"
                    )
                    rough_density, rough_energy = False, False

                # 读取二级目录下的 OUTCAR 文件
                fine_OUTCAR_file_path = os.path.join(vasp_opt_path, "fine", "OUTCAR")
                try:
                    fine_atoms = read_vasp_out(fine_OUTCAR_file_path)
                    molecules, molecules_flag, initial_information = identify_molecules(
                        fine_atoms
                    )
                    if not initial_information:
                        raise KeyError("No available initial molecules")
                    fine_atoms_volume = (
                        fine_atoms.get_volume()
                    )  # 体积单位为立方埃（Å³）
                    fine_atoms_masses = sum(
                        fine_atoms.get_masses()
                    )  # 质量单位为原子质量单位(amu)
                    # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
                    fine_density = round(
                        1.66054 * fine_atoms_masses / fine_atoms_volume, 3
                    )
                    fine_energy = round(fine_atoms.get_total_energy(), 1)
                    logging.info(
                        f"  Fine_Density: {fine_density}, Fine_Energy: {fine_energy}"
                    )
                    molecules_information(
                        molecules, molecules_flag, initial_information
                    )
                except (ParseError, FileNotFoundError):
                    logging.error(
                        f"  Unfinished fine optimization job of CONTCAR_{mlp_density}_{number}"
                    )
                    fine_density, fine_energy, molecules_flag = False, False, False

                final_density, final_energy = (
                    False,
                    False,
                )
                if relaxation:
                    # 读取三级目录下的 OUTCAR 文件
                    final_OUTCAR_file_path = os.path.join(
                        vasp_opt_path, "fine", "final", "OUTCAR"
                    )
                    try:
                        final_atoms = read_vasp_out(final_OUTCAR_file_path)
                        molecules, molecules_flag, initial_information = (
                            identify_molecules(final_atoms)
                        )
                        if not initial_information:
                            raise KeyError("No available initial molecules")
                        final_atoms_volume = (
                            final_atoms.get_volume()
                        )  # 体积单位为立方埃（Å³）
                        final_atoms_masses = sum(
                            final_atoms.get_masses()
                        )  # 质量单位为原子质量单位(amu)
                        # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
                        final_density = round(
                            1.66054 * final_atoms_masses / final_atoms_volume, 3
                        )
                        final_energy = round(final_atoms.get_total_energy(), 1)
                        logging.info(
                            f"  Final_Density: {final_density}, Final_Energy: {final_energy}"
                        )
                        molecules_information(
                            molecules, molecules_flag, initial_information
                        )
                    except (ParseError, FileNotFoundError):
                        logging.error(
                            f"  Unfinished final optimization job of CONTCAR_{mlp_density}_{number}"
                        )
                        final_density, final_energy, molecules_flag = (
                            False,
                            False,
                            False,
                        )

                # 读取根目录下的 config.yaml 信息与对应的 .json 文件
                config_path = os.path.join(self.base_dir, "config.yaml")
                with open(config_path, "r") as file:
                    config = yaml.safe_load(file)
                try:
                    species_json = [
                        os.path.splitext(f)[0] + ".json"
                        for f in config["gen_opt"]["species"]
                    ]
                    ion_numbers = config["gen_opt"]["ion_numbers"]
                    for json_file, count in zip(species_json, ion_numbers):
                        molecular_volumes = 0
                        with open(os.path.join(self.base_dir, json_file), "r") as file:
                            property = json.load(file)
                        molecular_volume = float(property["volume"])
                        molecular_volumes += molecular_volume * count
                    if relaxation:
                        packing_coefficient = round(
                            molecular_volumes / final_atoms_volume, 3
                        )
                    else:
                        packing_coefficient = round(
                            molecular_volumes / fine_atoms_volume, 3
                        )
                except (FileNotFoundError, UnboundLocalError):
                    packing_coefficient = False

                mlp_energies.append(mlp_energy)
                rough_densities.append(rough_density)
                rough_energies.append(rough_energy)
                fine_densities.append(fine_density)
                fine_energies.append(fine_energy)
                final_densities.append(final_density)
                final_energies.append(final_energy)
                ions_checks.append(molecules_flag)
                packing_coefficients.append(packing_coefficient)

        with open(
            f"{self.base_dir}/vasp_density_energy.csv",
            "w",
            newline="",
            encoding="utf-8",
        ) as csv_file:
            writer = csv.writer(csv_file)
            header = [
                "Number",
                "MLP_E",
                "Rough_E",
                "Fine_E",
                "MLP_Density",
                "Rough_Density",
                "Fine_Density",
                "Ions_Check",
            ]
            if relaxation:
                header = [
                    "Number",
                    "MLP_E",
                    "Rough_E",
                    "Fine_E",
                    "Final_E",
                    "MLP_Density",
                    "Rough_Density",
                    "Fine_Density",
                    "Final_Density",
                    "Ions_Check",
                ]
            if packing_coefficients:
                header.append("Pack_Coef")
            datas = list(
                zip(
                    numbers,
                    mlp_energies,
                    rough_energies,
                    fine_energies,
                    mlp_densities,
                    rough_densities,
                    fine_densities,
                    ions_checks,
                    (*packing_coefficients,) if packing_coefficients else (),
                )
            )
            if relaxation:
                datas = list(
                    zip(
                        numbers,
                        mlp_energies,
                        rough_energies,
                        fine_energies,
                        final_energies,
                        mlp_densities,
                        rough_densities,
                        fine_densities,
                        final_densities,
                        ions_checks,
                        (*packing_coefficients,) if packing_coefficients else (),
                    )
                )
            if molecules_prior:
                # 如果设置了 molecules_prior 参数为 True，则优先第倒数Ions_Check 为 True 的结果，再根据第6列的 Fine_Density 降序排序
                datas.sort(key=lambda x: (not x[-2], -float(x[-3])))
            else:
                # 否则，直接根据第6列（从0列开始）的 Fine_Density 降序排序
                datas.sort(key=lambda x: -float(x[-3]))
            writer.writerow(header)
            for data in datas:
                writer.writerow(data)
                
        logging.info(
            f"Maximum MLP Density: {max(mlp_densities)}, Structure Number: {numbers[mlp_densities.index(max(mlp_densities))]}"
        )
        logging.info(
            f"Maximum Fine Density: {max(fine_densities)}, Structure Number: {numbers[fine_densities.index(max(fine_densities))]}"
        )
        if relaxation:
            logging.info(
                f"Maximum Final Density: {max(final_densities)}, Structure Number: {numbers[final_densities.index(max(final_densities))]}"
            )

    def export_max_density_structure(self):
        """
        Read the structure number from the vasp_sensitiy_energy.csv file in the results folder, then search for the corresponding folder based on that sequence number, copy the highest density and highest precision CONTCAR file, and rename it POSCAR
        """
        # 找到 vas_density_energy.csv 文件
        csv_file_path = os.path.join(self.base_dir, "vasp_density_energy.csv")
        if os.path.exists(csv_file_path):
            # 读取 CSV 文件
            with open(csv_file_path, "r") as csvfile:
                reader = csv.reader(csvfile)
                # 跳过表头读取第一行结构序号，即符合结构筛选要求的最大密度结构
                header = next(reader)
                if header[0] != "Number":
                    raise KeyError(
                        "The first column of the CSV file is not 'Number', please check the file format."
                    )
                first_row = next(reader)
                structure_number = str(first_row[0])
            # 根据结构序号构建要查找的文件夹路径
            vasp_optimized_dir = os.path.join(self.base_dir, "4_vasp_optimized")
            for vasp_folder_name in os.listdir(vasp_optimized_dir):
                vasp_folder_path = os.path.join(vasp_optimized_dir, vasp_folder_name)
                if os.path.isdir(vasp_folder_path) and vasp_folder_name.endswith(
                    structure_number
                ):
                    # 查找 CONTCAR 文件
                    # final_contcar_path = os.path.join(
                    #     vasp_folder_path, "fine", "final", "CONTCAR"
                    # )
                    # print(f"Trying to get the final structure from {vasp_folder_path}")
                    # logging.info(
                    #     f"Trying to get the final structure from {vasp_folder_path}"
                    # )
                    # if os.path.exists(final_contcar_path):
                    #     # 复制 CONTCAR 文件到 combo_n 文件夹并重命名为 POSCAR
                    #     shutil.copy(
                    #         final_contcar_path, os.path.join(self.base_dir, "POSCAR")
                    #     )
                    #     print(f"Renamed CONTCAR to POSCAR in {self.base_dir}, copied from {final_contcar_path}")
                    #     logging.info(
                    #         f"Renamed CONTCAR to POSCAR in {self.base_dir}, copied from {final_contcar_path}"
                    #     )
                    fine_contcar_path = os.path.join(
                        vasp_folder_path, "fine", "CONTCAR"
                    )
                    if os.path.exists(fine_contcar_path):
                        print(f"CONTCAR not found in {os.path.join(vasp_folder_path, 'fine', 'final')}")
                        logging.info(
                            f"CONTCAR not found in {os.path.join(vasp_folder_path, 'fine', 'final')}"
                        )
                        # 复制 CONTCAR 文件到 combo_n 文件夹并重命名为 POSCAR
                        shutil.copy(
                            fine_contcar_path, os.path.join(self.base_dir, "POSCAR")
                        )
                        print(f"Renamed CONTCAR to POSCAR in {self.base_dir}, copied from {fine_contcar_path}")
                        logging.info(
                            f"Renamed CONTCAR to POSCAR in {self.base_dir}, copied from {fine_contcar_path}"
                        )
                    else:
                        print(f"Eligible CONTCAR not found in {vasp_folder_path}")
                        logging.info(
                            f"Eligible CONTCAR not found in {vasp_folder_path}"
                        )
        else:
            print(f"CSV file not found in {self.base_dir}")
            logging.info(f"CSV file not found in {self.base_dir}")
