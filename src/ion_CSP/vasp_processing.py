import os
import csv
import json
import yaml
import shutil
import logging
from pathlib import Path
import importlib.resources
from ase.io import ParseError
from ase.io.vasp import read_vasp, read_vasp_out
from dpdispatcher import Machine, Resources, Task, Submission
from ion_CSP.log_and_time import redirect_dpdisp_logging
from ion_CSP.identify_molecules import identify_molecules, molecules_information


class VaspProcessing:
    def __init__(self, work_dir: Path):
        """
        This directory is used to store all the files related to VASP optimizations.

        :params
            work_dir: The working directory where VASP optimization files will be stored.
        """
        self.base_dir = work_dir.resolve()
        redirect_dpdisp_logging(work_dir / "dpdispatcher.log")
        self.for_vasp_opt_dir = self.base_dir / "3_for_vasp_opt"
        self.vasp_optimized_dir = self.base_dir / "4_vasp_optimized"
        self.param_dir = importlib.resources.files("ion_CSP.param")

    def _machine_resources_prep(self, machine_path: str, resources_path: str):
        """
        Prepare machine and resources configuration files for dpdispatcher.
        :params
            machine_path: The path to save the machine configuration file, which can be in JSON or YAML format.
            resources_path: The path to save the resources configuration file, which can be in JSON or YAML format.
        :return: machine, resources, parent
        1. machine: The machine configuration object.
        2. resources: The resources configuration object.
        3. parent: The parent directory prefix based on the context type (SSHContext or LocalContext).
        """
        # 读取machine.json和resources.json的参数
        if machine_path.endswith(".json"):
            machine = Machine.load_from_json(machine_path)
        elif machine_path.endswith(".yaml"):
            machine = Machine.load_from_yaml(machine_path)
        else:
            raise KeyError("Unsupported machine file type")
        if resources_path.endswith(".json"):
            resources = Resources.load_from_json(resources_path)
        elif resources_path.endswith(".yaml"):
            resources = Resources.load_from_yaml(resources_path)
        else:
            raise KeyError("Unsupported resources file type")
        # 由于dpdispatcher对于远程服务器以及本地运行的forward_common_files的默认存放位置不同，因此需要预先进行判断，从而不改动优化脚本
        machine_inform = machine.serialize()
        if machine_inform["context_type"] == "SSHContext":
            # 如果调用远程服务器，则创建二级目录
            parent = "data/"
        elif machine_inform["context_type"] == "LocalContext":
            # 如果在本地运行作业，则只在后续创建一级目录
            parent = ""
        return machine, resources, parent

    def dpdisp_vasp_optimization_tasks(
        self,
        machine_path: str,
        resources_path: str,
        nodes: int = 1,
    ):
        """
        Based on the dpdispatcher module, prepare and submit files for optimization on remote server or local machine.
        :params
            machine: The machine configuration file, which can be in JSON or YAML format.
            resources: The resources configuration file, which can be in JSON or YAML format.
            nodes: The number of nodes to distribute the optimization tasks across.
        """
        # 读取machine.json和resources.json的参数
        machine, resources, parent = self._machine_resources_prep(
            machine_path=machine_path, resources_path=resources_path
        )

        # 获取文件夹中所有以prefix_name开头的文件，在此实例中为CONTCAR_
        mlp_contcar_files = [
            f for f in self.for_vasp_opt_dir.iterdir() if f.name.startswith("CONTCAR_")
        ]
        if not mlp_contcar_files:
            raise FileNotFoundError(
                f"No CONTCAR_ files found in {self.for_vasp_opt_dir}"
            )
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
            task_dir = self.for_vasp_opt_dir / f"{parent}pop{pop}"
            task_dir.mkdir(parents=True, exist_ok=True)
            for file in forward_files:
                src = self.param_dir / file
                dst = task_dir / file
                shutil.copyfile(str(src), str(dst))
            for job_i in node_jobs[pop]:
                # 将分配好的POSCAR文件添加到对应的上传文件中
                contcar = mlp_contcar_files[job_i]
                forward_files.append(contcar.name)
                vasp_dir_name = contcar.name.split("CONTCAR_")[1]
                # 每个POSCAR文件在优化后都取回对应的CONTCAR和OUTCAR输出文件
                backward_files.append(f"{vasp_dir_name}/fine/*")
                backward_files.append(f"{vasp_dir_name}/*")
                dst = task_dir / contcar.name
                shutil.copyfile(str(contcar), str(dst))

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
            work_base=str(self.for_vasp_opt_dir),
            machine=machine,
            resources=resources,
            task_list=task_list,
        )
        submission.run_submission()

        # 创建用于存放优化后文件的 4_vasp_optimized 目录
        self.vasp_optimized_dir.mkdir(exist_ok=True)
        for contcar in mlp_contcar_files:
            vasp_dir_name = contcar.name.split("CONTCAR_")[1]
            outcar = self.for_vasp_opt_dir / f"OUTCAR_{vasp_dir_name}"
            if outcar.exists():
                shutil.copyfile(
                    str(contcar), str(self.vasp_optimized_dir / contcar.name)
                )
                shutil.copyfile(str(outcar), str(self.vasp_optimized_dir / outcar.name))

        for pop in range(nodes):
            # 从传回的 pop 文件夹中将结果文件取到 4_vasp_optimized 目录
            task_dir = self.for_vasp_opt_dir / f"{parent}pop{pop}"
            for job_i in node_jobs[pop]:
                contcar = mlp_contcar_files[job_i]
                vasp_dir_name = contcar.name.split("CONTCAR_")[1]
                src = task_dir / vasp_dir_name
                dst = self.vasp_optimized_dir / vasp_dir_name
                if src.exists():
                    shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
            # 在成功完成 VASP 分步优化后，删除 3_for_vasp_opt/{parent}/pop{n} 文件夹以节省空间
            shutil.rmtree(task_dir, ignore_errors=True)
        if machine.serialize()["context_type"] == "SSHContext":
            # 如果调用远程服务器，则删除data级目录
            shutil.rmtree(self.for_vasp_opt_dir / "data", ignore_errors=True)
        logging.info("Batch VASP optimization completed!!!")

    def dpdisp_vasp_relaxation_tasks(
        self,
        machine_path: str,
        resources_path: str,
        nodes: int = 1,
    ):
        """
        Based on the dpdispatcher module, prepare and submit files for VASP relaxation on remote server or local machine.
        :params
            machine: The machine configuration file, which can be in JSON or YAML format.
            resources: The resources configuration file, which can be in JSON or YAML format.
            nodes: The number of nodes to distribute the optimization tasks across.
        """
        # 读取machine.json和resources.json的参数
        machine, resources, parent = self._machine_resources_prep(
            machine_path=machine_path, resources_path=resources_path
        )
        # 获取dir文件夹中所有以prefix_name开头的文件，在此实例中为POSCAR_
        vasp_optimized_folders = [
            f
            for f in self.vasp_optimized_dir.iterdir()
            if f.is_dir() and f.name != "data"
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
            task_dir = self.vasp_optimized_dir / f"{parent}pop{pop}"
            task_dir.mkdir(parents=True, exist_ok=True)
            for file in forward_files:
                src = self.param_dir / file
                dst = task_dir / file
                shutil.copyfile(str(src), str(dst))
            for job_i in node_jobs[pop]:
                # 将分配好的POSCAR文件添加到对应的上传文件中
                vasp_dir_name = vasp_optimized_folders[job_i].name
                fine_optimized_file = f"{vasp_dir_name}/fine/CONTCAR"
                fine_contcar_path = self.vasp_optimized_dir / fine_optimized_file
                if fine_contcar_path.exists():
                    dst = task_dir / fine_optimized_file
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(str(fine_contcar_path), str(dst))
                else:
                    logging.error(f"File {fine_contcar_path} does not exist.")
                    raise
                forward_files.append(fine_optimized_file)
                # 每个POSCAR文件在优化后都取回对应的CONTCAR和OUTCAR输出文件
                backward_files.append(f"{vasp_dir_name}/*")
                backward_files.append(f"{vasp_dir_name}/fine/*")
                backward_files.append(f"{vasp_dir_name}/fine/final/*")

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
            work_base=str(self.vasp_optimized_dir),
            machine=machine,
            resources=resources,
            task_list=task_list,
        )
        submission.run_submission()

        for pop in range(nodes):
            # 从传回的 pop 文件夹中将结果文件取到 4_vasp_optimized 目录
            task_dir = self.vasp_optimized_dir / f"{parent}pop{pop}"
            for job_i in node_jobs[pop]:
                vasp_dir_name = vasp_optimized_folders[job_i].name
                final_dir = task_dir / vasp_dir_name / "fine" / "final"
                dst_dir = self.vasp_optimized_dir / vasp_dir_name / "fine" / "final"
                if final_dir.exists():
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(str(final_dir), str(dst_dir), dirs_exist_ok=True)
                else:
                    logging.error(
                        f"No final optimization results found for {vasp_dir_name} in {task_dir}"
                    )
            # 在成功完成 VASP 分步优化后，删除 4_vasp_optimized /{parent}/pop{n} 文件夹以节省空间
            shutil.rmtree(task_dir, ignore_errors=True)
        if machine.serialize()["context_type"] == "SSHContext":
            # 如果调用远程服务器，则删除data级目录
            shutil.rmtree(self.vasp_optimized_dir / parent)
        logging.info("Batch VASP optimization completed!!!")

    def _read_mlp_properties(self, contcar_path: Path, outcar_path: Path):
        """
        Read a single MLP CONTCAR and OUTCAR file and extract density and energy information.
        :params
            contcar_path: The path to the MLP CONTCAR file.
            outcar_path: The path to the MLP OUTCAR file.
        :return: density, energy
        1. density: The calculated density of the structure in g/cm³, rounded to three decimal places. If reading fails, returns None.
        2. energy: The total energy of the structure in eV, rounded to one decimal place. If reading fails, returns None.
        """
        density = None
        energy = None
        try:
            atoms = read_vasp(contcar_path)
            volume = atoms.get_volume()  # 体积单位为立方埃（Å³）
            masses = sum(atoms.get_masses())  # 质量单位为原子质量单位(amu)
            # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
            density = round(1.66054 * masses / volume, 3)
        except (ParseError, FileNotFoundError) as e:
            logging.error(f"Error reading CONTCAR file {contcar_path}: {e}")
            density = None
        except Exception as e:
            logging.error(f"Unexpected error reading CONTCAR file {contcar_path}: {e}")
            density = None
        try:
            # 由于机器学习势优化的 OUTCAR 文件并非常规格式，因此需要逐行读取
            with outcar_path.open("r") as f:
                for line in f:
                    if "TOTEN" in line:
                        values = line.split()
                        energy = round(float(values[-2]), 1)
        except (ParseError, FileNotFoundError) as e:
            logging.error(f"Error reading OUTCAR file {outcar_path}: {e}")
            energy = None
        except Exception as e:
            logging.error(f"Unexpected error reading OUTCAR file {outcar_path}: {e}")
            energy = None
        return density, energy

    def _read_vasp_outcar(self, outcar_path: Path):
        """
        Read a single VASP OUTCAR file and extract density and energy information.
        :params
            outcar_path: The path to the VASP OUTCAR file.
        :return: atoms, density, energy
        1. atoms: The ASE Atoms object read from the OUTCAR file. If reading fails, returns None.
        2. density: The calculated density of the structure in g/cm³, rounded to three decimal places. If reading fails, returns -inf.
        3. energy: The total energy of the structure in eV, rounded to one decimal place. If reading fails, returns None.
        """
        density = float("-inf")
        energy = float("inf")
        try:
            atoms = read_vasp_out(str(outcar_path))
            volume = atoms.get_volume()  # 体积单位为立方埃（Å³）
            masses = sum(atoms.get_masses())  # 质量单位为原子质量单位(amu)
            # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
            density = round(1.66054 * masses / volume, 3)
            energy = round(atoms.get_total_energy(), 1)
            return atoms, density, energy
        except (ParseError, FileNotFoundError):
            return None, float("-inf"), float("inf")
        except Exception as e:
            logging.error(f"Unexpected error reading OUTCAR file {outcar_path}: {e}\n")
            return None, float("-inf"), float("inf")

    def read_vaspout_save_csv(self, molecules_prior: bool, relaxation: bool = False):
        """
        Read VASP output files in batches and save energy and density to corresponding CSV files in the directory
        """
        data_rows = []

        config_path = self.base_dir / "config.yaml"
        if config_path.exists():
            with open(config_path, "r") as file:
                config = yaml.safe_load(file)
        species_json = [
            os.path.splitext(f)[0] + ".json" for f in config["gen_opt"]["species"]
        ]
        ion_numbers = config["gen_opt"]["ion_numbers"]

        for folder in self.vasp_optimized_dir.iterdir():
            if not folder.is_dir():
                continue
            if "_" not in folder.name:
                logging.warning(
                    f"Skipping folder with unexpected name format: {folder.name}"
                )
                continue

            number = folder.name.split("_")[-1]
            logging.info(f"CONTCAR_{folder.name}")
            # 读取 4_vasp_optimized 目录下的机器学习势的 CONTCAR 与 OUTCAR 文件
            mlp_contcar = self.vasp_optimized_dir / f"CONTCAR_{folder.name}"
            mlp_outcar = self.vasp_optimized_dir / f"OUTCAR_{folder.name}"
            mlp_density, mlp_energy = self._read_mlp_properties(mlp_contcar, mlp_outcar)
            logging.info(f"  MLP_Density: {mlp_density}, MLP_Energy: {mlp_energy}")
            # 读取二级目录下 Rough 优化的 OUTCAR 文件
            rough_outcar = folder / "OUTCAR"
            rough_atoms, rough_density, rough_energy = self._read_vasp_outcar(rough_outcar)
            if rough_atoms is None:
                logging.error(f"Error reading OUTCAR file {rough_outcar}\n")
            else:
                logging.info(
                    f"  Rough_Density: {rough_density}, Rough_Energy: {rough_energy}"
                )

            # 读取三级目录下 Fine 优化的 OUTCAR 文件
            fine_outcar = folder / "fine/OUTCAR"
            fine_atoms, fine_density, fine_energy = (
                self._read_vasp_outcar(fine_outcar)
            )
            fine_ions_check, final_ions_check = False, False
            final_density, final_energy = float("-inf"), float("inf")
            if fine_atoms is None:
                logging.error(f"Error reading fine/OUTCAR file {fine_outcar}\n")
            else:
                if not relaxation:
                    molecules, fine_ions_check, initial_info = identify_molecules(
                        fine_atoms, base_dir=self.base_dir
                    )
                    if not initial_info:
                        raise KeyError("No available initial molecules")
                    logging.info(
                        f"  Fine_Density: {fine_density}, Fine_Energy: {fine_energy}, Ions_Check: {fine_ions_check}"
                    )
                    molecules_information(molecules, fine_ions_check, initial_info)
                else:
                    logging.info(
                        f"  Fine_Density: {fine_density}, Fine_Energy: {fine_energy}"
                    )
                    # 读取四级目录下 Final 优化的 OUTCAR 文件
                    final_outcar = folder / "fine/final/OUTCAR"
                    final_atoms, final_density, final_energy = (
                        self._read_vasp_outcar(final_outcar)
                    )
                    if final_atoms is None:
                        logging.error(f"Error reading final/OUTCAR file {final_outcar}\n")
                    else:
                        molecules, final_ions_check, initial_info = identify_molecules(
                            final_atoms, base_dir=self.base_dir
                        )
                        if not initial_info:
                            raise KeyError("No available initial molecules")
                        logging.info(
                            f"  Final_Density: {final_density}, Final_Energy: {final_energy}, Ions_Check: {final_ions_check}"
                        )
                        molecules_information(molecules, final_ions_check, initial_info)
            fine_PC = False
            final_PC = False
            # 读取根目录下的 config.yaml 信息与对应的 .json 文件
            try:
                for json_file, count in zip(species_json, ion_numbers):
                    molecular_volumes = 0

                    with open(self.base_dir / json_file, "r") as file:
                        property = json.load(file)
                    molecular_volume = float(property["volume"])
                    molecular_volumes += molecular_volume * count
                    fine_volume = (
                        fine_atoms.get_volume() if fine_atoms is not None else None
                    )
                    fine_PC = round(molecular_volumes / fine_volume, 3)
                if relaxation:
                    final_volume = (
                        final_atoms.get_volume() if final_atoms is not None else None
                    )
                    final_PC = round(molecular_volumes / final_volume, 3)
            except (FileNotFoundError, UnboundLocalError, TypeError):
                fine_PC = False
                final_PC = False

            row = {
                "Number": number,
                "MLP_Density": mlp_density,
                "MLP_Energy": mlp_energy,
                "Rough_Density": rough_density,
                "Rough_Energy": rough_energy,
                "Fine_Density": fine_density,
                "Fine_Energy": fine_energy,
                "Fine_Ions_Check": fine_ions_check,
                "Fine_PC": fine_PC,
            }
            if relaxation:
                row.update(
                    {
                        "Final_Density": final_density,
                        "Final_Energy": final_energy,
                        "Final_Ions_Check": final_ions_check,
                        "Final_PC": final_PC,
                    }
                )
            data_rows.append(row)

        csv_file_path = self.base_dir / "vasp_density_energy.csv"
        if csv_file_path.exists():
            csv_file_path.unlink()
        with csv_file_path.open("w", newline="", encoding="utf-8") as csv_file:
            header = (
                [
                    "Number",
                    "MLP_Energy",
                    "Rough_Energy",
                    "Fine_Energy",
                    "MLP_Density",
                    "Rough_Density",
                    "Fine_Density",
                    "Fine_Ions_Check",
                    "Fine_PC",
                ]
                if not relaxation
                else [
                    "Number",
                    "MLP_Energy",
                    "Rough_Energy",
                    "Fine_Energy",
                    "Final_Energy",
                    "MLP_Density",
                    "Rough_Density",
                    "Fine_Density",
                    "Final_Density",
                    "Final_Ions_Check",
                    "Final_PC",
                ]
            )

            def sort_key(row):
                if not relaxation:
                    density_val = row["Fine_Density"]
                    ions_check = row["Fine_Ions_Check"]
                else:
                    density_val = row["Final_Density"]
                    ions_check = row["Final_Ions_Check"]
                if molecules_prior:
                    return (not bool(ions_check), -density_val)
                else:
                    return -density_val

            writer = csv.DictWriter(csv_file, fieldnames=header, extrasaction="ignore")
            data_rows.sort(key=sort_key)
            writer.writeheader()
            writer.writerows(data_rows)

        logging.info(f"VASP Density and Energy data saved to {csv_file_path}")

        # 创建以number为键的字典
        data_dict = {}
        for row in data_rows:
            number = row["Number"]
            data_dict[number] = {
                "mlp_density": row["MLP_Density"],
                "fine_density": row["Fine_Density"],
                "final_density": row["Final_Density"] if relaxation else None
            }

        # 一行代码获取每种密度的最大值所对应的编号
        max_mlp_num = max(data_dict, key=lambda k: data_dict[k]['mlp_density'])
        max_fine_num = max(data_dict, key=lambda k: data_dict[k]['fine_density'])
        max_final_num = max(data_dict, key=lambda k: data_dict[k]['final_density'])

        # 通过编号获取对应的最大值
        max_mlp_density = data_dict[max_mlp_num]['mlp_density']
        max_fine_density = data_dict[max_fine_num]['fine_density']
        max_final_density = data_dict[max_final_num]['final_density']

        # 打印结果
        logging.info(
            f"Maximum MLP Density: {max_mlp_density} (Structure Number: {max_mlp_num})"
        )
        if not relaxation:
            logging.info(
                f"Maximum Fine Density: {max_fine_density} (Structure Number: {max_fine_num})\n"
            )
        else:
            logging.info(
                f"Maximum Fine Density: {max_fine_density} (Structure Number: {max_fine_num})"
            )
            logging.info(
                f"Maximum Final Density: {max_final_density} (Structure Number: {max_final_num})\n"
            )

    def export_max_density_structure(self, relaxation: bool = False):
        """
        Read the structure number from the vasp_sensitiy_energy.csv file in the results folder, then search for the corresponding folder based on that sequence number, copy the highest density and highest precision CONTCAR file, and rename it POSCAR
        :params
            relaxation: Whether the final relaxation step has been performed. If True, the POSCAR will be copied from the final relaxation step; if False, it will be copied from the fine optimization step
        """
        # 找到 vasp_density_energy.csv 文件
        logging.info("Preparing to export the structure with the highest density...")
        csv_file_path = self.base_dir / "vasp_density_energy.csv"

        if not csv_file_path.exists():
            print(f"CSV file not found in {csv_file_path}")
            logging.info(f"CSV file not found in {csv_file_path}")
            return

        # 读取 CSV 文件并找到 Fine_Density 最大的结构
        max_density = float("-inf")
        best_number = None

        with csv_file_path.open("r", encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader)  # 读取表头
            # 检查表头格式
            try:
                if not relaxation:
                    target_density_col = header.index("Fine_Density")
                    target_ions_check_col = header.index("Fine_Ions_Check")
                    density_type = "Fine"
                else:
                    target_density_col = header.index("Final_Density")
                    target_ions_check_col = header.index("Final_Ions_Check")
                    density_type = "Final"
            except ValueError as e:
                print(f"Required column not found in CSV file: {e}")
                logging.error(f"Required column not found in CSV file: {e}")
                return

            #遍历所有行寻找最大密度
            for row_num, row in enumerate(
                reader, start=2
            ):  # row_num从2开始（表头后第一行）
                if len(row) <= max(target_density_col, target_ions_check_col):
                    print(f"Warning: Incomplete row {row_num} skipped")
                    continue  # 跳过不完整的行

                # 检查 Ions_Check 是否为 True
                ions_check = row[target_ions_check_col].strip().lower()
                if ions_check != "true":
                    continue  # 跳过 Ions_Check=False 的行
                try:
                    current_density = float(row[target_density_col])
                    if current_density > max_density:
                        max_density = current_density
                        best_number = row[0]  # 第一列是 Number
                except ValueError:
                    continue  # 跳过无法转换为数字的值

        if best_number is None:
            print("No valid structure found in CSV file")
            logging.info("No valid structure found in CSV file")
            return

        logging_info = f"Maximum {density_type} Density: {max_density} (Structure Number: {best_number})"
        print(logging_info)
        logging.info(logging_info)

        target_contcar = None
        # 根据结构序号构建要查找的文件夹路径
        for folder in self.vasp_optimized_dir.iterdir():
            if folder.is_dir() and folder.name.endswith(f"_{best_number}"):
                print(f"Found matching folder: {folder}")
                logging.info(f"Found matching folder: {folder}")

                # 根据 relaxation 参数决定复制哪个 CONTCAR 文件
                if not relaxation:
                    target_contcar = folder / "fine" / "CONTCAR"
                else:
                    target_contcar = folder / "fine" / "final" / "CONTCAR"

                if target_contcar.exists():
                    # 复制前备份现有的 POSCAR 文件（如果存在）
                    poscar_path = self.base_dir / "POSCAR"
                    if poscar_path.exists():
                        backup_path = self.base_dir / "POSCAR.bak"
                        shutil.move(poscar_path, backup_path)
                        print(f"Existing POSCAR backed up to {backup_path}")
                        logging.info(f"Existing POSCAR backed up to {backup_path}")
                    # 复制并重命名 CONTCAR 为 POSCAR
                    shutil.copy(target_contcar, poscar_path)
                    print(f"Copied CONTCAR from target {target_contcar} to {poscar_path}")
                    logging.info(f"Copied CONTCAR from target {target_contcar} to {poscar_path}")
                else:
                    print(f"Eligible CONTCAR not found at expected location: {target_contcar}")
                    logging.info(f"Eligible CONTCAR not found at expected location: {target_contcar}")
                break

        if target_contcar is None:
            print(f"No folder found for structure number {best_number}")
            logging.info(f"No folder found for structure number {best_number}")
