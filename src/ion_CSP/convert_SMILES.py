import shutil
import logging
import pandas as pd
import importlib.resources
from typing import List
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import AllChem
from dpdispatcher import Task, Submission
from ion_CSP.log_and_time import redirect_dpdisp_logging, machine_resources_prep


class SmilesProcessing:

    def __init__(
        self,
        work_dir: Path,
        csv_file: str,
        converted_folder: str = "1_1_SMILES_gjf",
        optimized_dir: str = "1_2_Gaussian_optimized",
    ):
        """
        This class is used to process SMILES codes from a CSV file, convert them into Gaussian input files, and prepare for optimization tasks. It also supports grouping by charge and filtering based on functional groups.

        params:
            work_dir: the path of the working directory.
            csv_file: the csv file name in the working directory.
            converted_folder: the folder name for storing converted SMILES files.
            optimized_dir: the folder name for storing Gaussian optimized files.
        """
        redirect_dpdisp_logging(work_dir / "dpdispatcher.log")
        # 读取csv文件并处理数据, csv文件的表头包括 SMILES, Charge, Refcode或Number
        self.base_dir = work_dir.resolve()
        if not csv_file:
            raise Exception("Necessary .csv file not provided!")
        csv_path = self.base_dir / csv_file
        if csv_path.is_dir():
            raise Exception(f"Expected a CSV file, but got a directory: {csv_path}")
        if not csv_path.exists():
            raise Exception(f"Necessary .csv file not provided: {csv_path}")
        self._validate_csv_format(csv_path)

        self.converted_dir = self.base_dir / converted_folder / Path(csv_file).stem
        self.gaussian_optimized_dir = self.base_dir / optimized_dir
        self.converted_dir.mkdir(parents=True, exist_ok=True)
        self.gaussian_optimized_dir.mkdir(parents=True, exist_ok=True)
        self.param_dir = importlib.resources.files("ion_CSP.param")

        original_df = pd.read_csv(csv_path)
        logging.info(f"Processing {csv_path}")
        # 对SMILES码去重
        df = original_df.drop_duplicates(subset="SMILES")
        try:
            # 根据Refcode进行排序
            df = df.sort_values(by="Refcode")
            self.base_name = "Refcode"
        except KeyError:
            # 如果不存在Refcode，则根据Number进行排序
            df = df.sort_values(by="Number")
            self.base_name = "Number"

        # 根据Charge分组
        grouped = df.groupby("Charge")
        duplicate_message = f"\nOriginal SMILES dataset: {len(original_df)}\nAfter SMILES deduplication\n Valid SMILES: {len(df)}\n Duplicate SMILES: {len(original_df) - len(df)}"
        logging.info(duplicate_message)

        self.df = df
        self.grouped = grouped


    def _validate_csv_format(self, csv_path: Path):
        """
        Validate that the CSV file has the required columns and correct structure.
        Must contain at least: SMILES, Charge, and either Refcode or Number.
        Raises Exception with clear message if validation fails.
        """
        try:
            df = pd.read_csv(csv_path, nrows=1)
        except pd.errors.EmptyDataError:
            raise Exception(f"CSV file is empty: {csv_path}")
        except pd.errors.ParserError as e:
            raise Exception(f"CSV file is malformed (e.g., wrong delimiter): {csv_path}\nError: {e}")

        required_cols = {"SMILES", "Charge"}
        optional_cols = {"Refcode", "Number"}

        missing_cols = required_cols - set(df.columns)
        if missing_cols:
            raise Exception(
                f"CSV file missing required columns: {missing_cols}. "
                f"Required: {required_cols}. Found: {list(df.columns)}"
            )

        # 至少有一个可选列
        if not (optional_cols & set(df.columns)):
            raise Exception(
                f"CSV file must contain at least one of: {optional_cols}. "
                f"Found: {list(df.columns)}"
            )

        # 检查 Charge 是否为数值类型
        if not pd.api.types.is_numeric_dtype(df["Charge"]):
            raise Exception(f"Column 'Charge' must be numeric. Got: {df['Charge'].dtype}")

        logging.info(f"CSV format validated successfully: {csv_path}")


    def _convert_SMILES(self, dir_path: str, smiles: str, basename: str, charge: int):
        """
        Private method:
        Use the rdkit module to read SMILES code and convert it into the required file types such as gjf, xyz, mol, etc.

        params:
            dir_path: The directory used for outputting files.
            smiles: SMILES code to be converted.
            basename: The reference code or number corresponding to SMILES code.
            charge: The charge carried by ions.

        return:
            result_code: Result code 0 or -1, representing success and failure respectively.
            basename: The corresponding basename.
        """
        dir_path = Path(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            logging.error(f"Invalid SMILES: {smiles} for {basename}")
            return False, basename
        try:
            mol = Chem.AddHs(mol)
        except Exception as e:
            logging.error(
                f"Error occurred while adding hydrogens to molecule {basename} with charge {charge}: {e}"
            )
            return False, basename
        try:
            # 生成3D坐标
            AllChem.EmbedMolecule(mol)
            AllChem.UFFOptimizeMolecule(mol)
            # 获取原子信息
            conf = mol.GetConformer()
            num_atoms = mol.GetNumAtoms()
            # 计算电荷与分子多重度
            num_charge, num_unpaired_electrons = 0, 0
            for atom in mol.GetAtoms():
                num_charge += atom.GetFormalCharge()
                num_unpaired_electrons += atom.GetNumRadicalElectrons()
            if num_charge != charge:
                logging.error(
                    f"{basename}: charge wrong! calculated {num_charge} and given {charge}"
                )
            multiplicity = 2 * num_unpaired_electrons + 1
            # 根据type参数判断要生成什么类型的结构文件, 目前只支持gjf, xyz, mol格式
            gjf_path = dir_path / f"{basename}.gjf"
            # 创建gjf文件内容
            gjf_content = f"%nprocshared=8\n%chk={basename}.chk\n#p B3LYP/6-31G** opt\n\n{basename}\n\n{num_charge} {multiplicity}\n"
            for atom in range(num_atoms):
                pos = conf.GetAtomPosition(atom)
                atom_symbol = mol.GetAtomWithIdx(atom).GetSymbol()
                gjf_content += f"{atom_symbol} {pos.x:.6f} {pos.y:.6f} {pos.z:.6f}\n"
            # 写入gjf文件
            gjf_path.write_text(f"{gjf_content}\n\n")
            # gjf文件末尾需要空行，否则Gaussian会报End of file in ZSymb错误(l101.exe)
            result_flag = True
        except Exception as e:  # 捕获运行过程中的错误
            logging.error(
                f"Error occurred while optimizing molecule of {basename} with charge {charge}: {e}"
            )
            result_flag = False
        # 第一项返回值为结果码True或False, 分别代表成功和失败; 第二项返回值为对应的refcode或序号
        return result_flag, basename


    def charge_group(self):
        """
        Create folders by grouping according to charges and convert SMILES codes into corresponding structural files.
        """
        # 分别记录生成结构成功和失败的refcode或序号
        success, fail = [], []
        for charge, group in self.grouped:
            # 根据文件类型与电荷分组创建对应的文件夹
            charge_dir = self.converted_dir / f"charge_{charge}"
            # 通过_convert_SMILES函数依次处理SMILES码
            for _, row in group.iterrows():
                result_flag, basename = self._convert_SMILES(
                    dir_path=charge_dir,
                    smiles=row["SMILES"],
                    basename=row[self.base_name],
                    charge=row["Charge"],
                )
                # 根据私有方法_convert_SMILES的返回值记录refcode对应的分子是否能够成功生成结构文件
                if result_flag:
                    success.append(basename)
                else:
                    fail.append(basename)
        # 将统计信息输出并记录到log文件中
        generation_message = f"\nDuring the .gjf file generation process\n Successfully generated .gjf files: {len(success)}\n Errors encounted: {len(fail)}\n Error {self.base_name}: {fail}"
        logging.info(generation_message)


    def screen(
        self,
        charge_screen: int = 0,
        group_screen: str = "",
        group_name: str = "",
        group_screen_invert: bool = False,
    ):
        """
        Screen based on the provided functional groups and charges.

        params:
            charge_screen: The charge to screen for, default is 0.
            group_screen: The functional group to screen for, default is empty string.
            group_name: The name of the functional group, used for naming the output directory.
            group_screen_invert: If True, invert the screening condition for the functional group.
        """
        # 另外筛选出符合条件的离子
        screened = self.df
        if group_screen:
            if group_screen_invert:
                screened = screened[
                    ~screened["SMILES"].str.contains(group_screen, regex=False)
                ]
            else:
                screened = screened[
                    screened["SMILES"].str.contains(group_screen, regex=False)
                ]
        screened = screened[screened["Charge"] == charge_screen]
        
        # 另外创建文件夹, 并依次处理SMILES码
        screened_dir = self.converted_dir / f"{group_name}_{charge_screen}"

        # 记录成功转换的分子数量
        success_count = 0
        for _, row in screened.iterrows():
            result_flag, _ = self._convert_SMILES(
                dir_path=screened_dir,
                smiles=row["SMILES"],
                basename=row[self.base_name],
                charge=row["Charge"],
            )
            if result_flag is True:
                success_count += 1
        
        # 将统计信息输出并记录到log文件中
        screened_message = f"\nNumber of ions with charge of [{charge_screen}] and {group_name} group: {success_count}\n"
        logging.info(screened_message)


    def dpdisp_gaussian_tasks(
        self,
        folders: List[str],
        machine_path: str,
        resources_path: str,
        nodes: int = 1,
    ):
        """
        Based on the dpdispatcher module, prepare and submit files for optimization on remote server or local machine.

        params:
            folders: List of folders containing .gjf files to be processed, if empty, all folders in the converted directory will be processed.
            machine: The machine configuration file for dpdispatcher, can be a JSON or YAML file.
            resources: The resources configuration file for dpdispatcher, can be a JSON or YAML file.
            nodes: The number of nodes to distribute the tasks to, default is 1.
        """
        if not folders:
            logging.error(
                "No available folders for dpdispatcher to process Gaussian tasks."
            )
            return
        # 读取machine和resources的参数
        machine, resources, parent = machine_resources_prep(
            machine_path=machine_path, resources_path=resources_path
        )

        for folder in folders:
            folder_dir = self.converted_dir / folder
            if not folder_dir.exists():
                folder_dir = self.base_dir / folder
                if not folder_dir.exists():
                    logging.error(
                        f"Provided folder {folder} is not either in the work directory or the converted directory.\n"
                    )
                    continue
            # 获取文件夹中所有以 .gjf 结尾的文件
            gjf_files = [
                f for f in folder_dir.iterdir() if f.suffix == ".gjf"
            ]
            if not gjf_files:
                logging.error(f"No .gjf files found in folder: {folder}")
                continue
            # 创建一个嵌套列表来存储每个节点的任务并将文件平均依次分配给每个节点
            # 例如：对于10个结构文件任务分发给4个节点的情况，则4个节点领到的任务分别[0, 4, 8], [1, 5, 9], [2, 6], [3, 7]
            node_jobs = [[] for _ in range(nodes)]
            for index, file in enumerate(gjf_files):
                node_index = index % nodes
                node_jobs[node_index].append(index)

            task_list = []

            for pop in range(nodes):
                forward_files = ["g16_sub.sh"]
                backward_files = ["log", "err"]
                # 将所有参数文件各复制一份到每个 task_dir 目录下
                task_dir = self.converted_dir / f"{parent}pop{pop}"
                task_dir.mkdir(parents=True, exist_ok=True)
                for file_name in forward_files:
                    src = self.param_dir / file_name
                    dst = task_dir / file_name
                    shutil.copyfile(str(src), str(dst))
                for job_i in node_jobs[pop]:
                    # 将分配好的 .gjf 文件添加到对应的上传文件中
                    gjf_file = gjf_files[job_i]
                    # 只传文件名，路径由 task_work_path 处理
                    forward_files.append(gjf_file.name)
                    base_name = gjf_file.stem
                    # 每个 .gjf 文件在优化后都取回对应的 .log、.fchk 输出文件
                    for ext in ["log", "fchk"]:
                        backward_files.append(f"{base_name}.{ext}")
                    dst = task_dir / gjf_file.name
                    shutil.copyfile(str(gjf_file), str(dst))

                remote_task_dir = f"{parent}pop{pop}"
                command = "chmod +x g16_sub.sh && ./g16_sub.sh"
                task = Task(
                    command=command,
                    task_work_path=remote_task_dir,
                    forward_files=forward_files,
                    backward_files=backward_files,
                )
                task_list.append(task)

            submission = Submission(
                work_base=str(self.converted_dir),
                machine=machine,
                resources=resources,
                task_list=task_list,
            )
            submission.run_submission()

            # 创建用于存放优化后文件的 gaussian_optimized 目录
            optimized_folder_dir = self.gaussian_optimized_dir / folder
            optimized_folder_dir.mkdir(parents=True, exist_ok=True)
            for pop in range(nodes):
                # 从传回目录下的 pop 文件夹中将结果文件取到 gaussian_optimized 目录
                task_dir = self.converted_dir / f"{parent}pop{pop}"
                # 按照给定的 .gjf 结构文件读取 .log、 文件并复制
                for job_i in node_jobs[pop]:
                    gjf_file = gjf_files[job_i]
                    base_name = gjf_file.stem
                    # 在优化后都取回每个 .gjf 文件对应的 .log、.fchk 输出文件
                    for ext in ["gjf", "log", "fchk"]:
                        src = task_dir / f"{base_name}.{ext}"
                        dst = optimized_folder_dir / f"{base_name}.{ext}"
                        if src.exists():
                            shutil.copyfile(str(src), str(dst))
                        else:
                            logging.error(f"File not found during copying: {src}")
                # 在成功完成Gaussian优化后，删除 1_1_SMILES_gjf/{csv}/{parent}/pop{n} 临时目录
                shutil.rmtree(task_dir, ignore_errors=True)
    
        if machine.serialize()["context_type"] == "SSHContext":
            # 如果调用远程服务器，则删除data级目录
            shutil.rmtree(self.converted_dir / parent)
        logging.info("Batch Gaussian optimization completed!!!")
