import os
import shutil
import logging
import pandas as pd
import importlib.resources
from typing import List
from rdkit import Chem
from rdkit.Chem import AllChem
from dpdispatcher import Machine, Resources, Task, Submission
from ion_CSP.log_and_time import redirect_dpdisp_logging


class SmilesProcessing:
    
    def __init__(self, work_dir: str, csv_file: str, converted_folder: str = '1_1_SMILES_gjf', optimized_dir: str = '1_2_Gaussian_optimized'):
        """
        This class is used to process SMILES codes from a CSV file, convert them into Gaussian input files, and prepare for optimization tasks. It also supports grouping by charge and filtering based on functional groups.

        params:
            work_dir: the path of the working directory.
            csv_file: the csv file name in the working directory.
            converted_folder: the folder name for storing converted SMILES files.
            optimized_dir: the folder name for storing Gaussian optimized files.
        """
        redirect_dpdisp_logging(os.path.join(work_dir, "dpdispatcher.log"))
        # 读取csv文件并处理数据, csv文件的表头包括 SMILES, Charge, Refcode或Number
        self.base_dir = work_dir
        os.chdir(work_dir)
        if not csv_file:
            raise Exception('Necessary .csv file not provided!')
        csv_path = os.path.join(self.base_dir, csv_file)
        self.converted_dir = os.path.join(
            self.base_dir, converted_folder, os.path.splitext(csv_file)[0]
        )
        self.gaussian_optimized_dir = os.path.join(self.base_dir, optimized_dir)
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
        self.csv = csv_file.split(".csv")[0]
        self.df = df
        self.grouped = grouped

    def _convert_SMILES(
        self, dir: str, smiles: str, basename: str, charge: int
    ):
        """
        Private method: 
        Use the rdkit module to read SMILES code and convert it into the required file types such as gjf, xyz, mol, etc.

        params:
            dir: The directory used for outputting files, regardless of existence of the directory.
            smiles: SMILES code to be converted.
            basename: The reference code or number corresponding to SMILES code.
            charge: The charge carried by ions.
            
        return:
            result_code: Result code 0 or -1, representing success and failure respectively.
            basename: The corresponding basename.
        """
        mol = Chem.MolFromSmiles(smiles)
        try:
            mol = Chem.AddHs(mol)
        except Exception as e:
            logging.error(
                f"Error occurred while adding hydrogens to molecule {basename} with charge {charge}: {e}"
            )
            return 1, basename  # 返回错误码1表示失败
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
            filename = f"{dir}/{basename}.gjf"    
            # 创建gjf文件内容
            gjf_content = f"%nprocshared=8\n%chk={basename}.chk\n#p B3LYP/6-31G** opt\n\n{basename}\n\n{num_charge} {multiplicity}\n"
            for atom in range(num_atoms):
                pos = conf.GetAtomPosition(atom)
                atom_symbol = mol.GetAtomWithIdx(atom).GetSymbol()
                gjf_content += (
                    f"{atom_symbol} {pos.x:.6f} {pos.y:.6f} {pos.z:.6f}\n"
                )
            # 写入gjf文件
            with open(filename, "w") as gjf_file:
                # gjf文件末尾需要空行，否则Gaussian会报End of file in ZSymb错误(l101.exe)
                gjf_file.write(f"{gjf_content}\n\n")
            result_code = 0
        except Exception as e:  # 捕获运行过程中的错误
            logging.error(
                f"Error occurred while optimizing molecule of {basename} with charge {charge}: {e}"
            )
            result_code = 1
        # 第一项返回值为结果码0或1, 分别代表成功和失败; 第二项返回值为对应的refcode或序号
        return result_code, basename

    def charge_group(self):
        """
        Create folders by grouping according to charges and convert SMILES codes into corresponding structural files.
        """
        # 分别记录生成结构成功和失败的refcode或序号
        success, fail = [], []
        for charge, group in self.grouped:
            # 根据文件类型与电荷分组创建对应的文件夹
            charge_dir = (
                f"{self.converted_dir}/charge_{charge}"
            )
            os.makedirs(charge_dir, exist_ok=True)
            # 通过_convert_SMILES函数依次处理SMILES码
            for _, row in group.iterrows():
                result_code, basename = self._convert_SMILES(
                    dir=charge_dir,
                    smiles=row["SMILES"],
                    basename=row[self.base_name],
                    charge=row["Charge"]
                )
                # 根据私有方法_convert_SMILES的返回值记录refcode对应的分子是否能够成功生成结构文件
                if result_code == 0:
                    success.append(basename)
                elif result_code == 1:
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
        if charge_screen:
            screened = screened[screened["Charge"] == charge_screen]
        screened_message = f"\nNumber of ions with charge of [{charge_screen}] and {group_name} group: {len(screened)}\n"
        logging.info(screened_message)
        # 另外创建文件夹, 并依次处理SMILES码
        screened_dir = f"{self.converted_dir}/{group_name}_{charge_screen}"
        os.makedirs(screened_dir, exist_ok=True)
        for _, row in screened.iterrows():
            self._convert_SMILES(
                dir=screened_dir,
                smiles=row["SMILES"],
                basename=row[self.base_name],
                charge=row["Charge"]
            )

    def dpdisp_gaussian_tasks(self,
        folders: List[str] = [],
        machine: str = "",
        resources: str = "",
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
        if os.path.exists(self.gaussian_optimized_dir):
            logging.error(f'The directory {self.gaussian_optimized_dir} has already existed.')
            return
        if not folders:
            logging.error('No available folders for dpdispatcher to process Gaussian tasks.')
            return
        # 调整工作目录，减少错误发生
        os.chdir(self.converted_dir)
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
        if machine_inform["context_type"] == "SSHContext":
            # 如果调用远程服务器，则创建二级目录
            parent = "data/"
        elif machine_inform["context_type"] == "LocalContext":
            # 如果在本地运行作业，则只在后续创建一级目录
            parent = ""

        for folder in folders:
            folder_dir = os.path.join(self.converted_dir, folder)
            if not os.path.exists(folder_dir):
                folder_dir = os.path.join(self.base_dir, folder)
                if not os.path.exists(folder_dir):
                    logging.error(f'Provided folder {folder} is not either in the work directory or the converted directory.\n')
                    continue
            # 获取文件夹中所有以 .gjf 结尾的文件
            gjf_files = [
                f for f in os.listdir(folder_dir) if f.endswith(".gjf")
            ]
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
                task_dir = os.path.join(self.converted_dir, f"{parent}pop{pop}")
                os.makedirs(task_dir, exist_ok=True)
                for file in forward_files:
                    shutil.copyfile(self.param_dir.joinpath(file), f"{task_dir}/{file}")
                for job_i in node_jobs[pop]:
                    # 将分配好的 .gjf 文件添加到对应的上传文件中
                    forward_files.append(gjf_files[job_i])
                    base_name, _ = os.path.splitext(gjf_files[job_i])
                    # 每个 .gjf 文件在优化后都取回对应的 .log、.fchk 输出文件
                    for ext in ['log', 'fchk']:
                        backward_files.append(f'{base_name}.{ext}')
                    shutil.copyfile(
                        f"{folder_dir}/{gjf_files[job_i]}",
                        f"{task_dir}/{gjf_files[job_i]}",
                    )

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
                work_base=self.converted_dir,
                machine=machine,
                resources=resources,
                task_list=task_list,
            )
            submission.run_submission()

            # 创建用于存放优化后文件的 gaussian_optimized 目录
            optimized_folder_dir = os.path.join(self.gaussian_optimized_dir, folder)
            os.makedirs(optimized_folder_dir, exist_ok=True)
            for pop in range(nodes):
                # 从传回目录下的 pop 文件夹中将结果文件取到 gaussian_optimized 目录
                task_dir = os.path.join(self.converted_dir, f"{parent}pop{pop}")
                # 按照给定的 .gjf 结构文件读取 .log、 文件并复制
                for job_i in node_jobs[pop]:
                    base_name, _ = os.path.splitext(gjf_files[job_i])
                    # 在优化后都取回每个 .gjf 文件对应的 .log、.fchk 输出文件
                    try:
                        for ext in ['gjf', 'log', 'fchk']:
                            shutil.copyfile(
                                f"{task_dir}/{base_name}.{ext}",
                                f"{optimized_folder_dir}/{base_name}.{ext}"
                            )
                    except FileNotFoundError as e:
                        logging.error(f"File not found during copying, please check the configuration and state of Gaussian: {e}")
                # 在成功完成Gaussian优化后，删除 1_1_SMILES_gjf/{csv}/{parent}/pop{n} 文件夹以节省空间
                shutil.rmtree(task_dir)
        shutil.copyfile(
            os.path.join(self.base_dir, "config.yaml"),
            os.path.join(optimized_folder_dir, "config.yaml"),
        )
        if machine_inform["context_type"] == "SSHContext":
            # 如果调用远程服务器，则删除data级目录
            shutil.rmtree(os.path.join(self.converted_dir, parent))
        logging.info("Batch Gaussian optimization completed!!!")
