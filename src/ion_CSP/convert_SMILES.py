import os
import logging
from rdkit import Chem
from rdkit.Chem import AllChem
import pandas as pd


class SmilesProcessing:
    
    def __init__(self, work_dir: str, csv_file: str):
        """
        args:
            work_dir: the path of the working directory.
            csv_file: the csv file name in the working directory.
        """
        # 读取csv文件并处理数据, csv文件的表头包括 SMILES, Charge, Refcode或Number
        self.base_dir = work_dir
        if not csv_file:
            raise Exception('Necessary .csv file not provided!')
        csv_path = os.path.join(self.base_dir, csv_file)
        original_df = pd.read_csv(csv_path)
        logging.info(f"Processing {csv_path}")
        # 对SMILES码去重
        df = original_df.drop_duplicates(subset="SMILES")
        try:
            # 根据Refcode进行排序
            df = df.sort_values(by="Refcode")
            self.basename = "Refcode"
        except KeyError:
            # 如果不存在Refcode，则根据Number进行排序
            df = df.sort_values(by="Number")
            self.basename = "Number"
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
        Private method: Use the rdkit module to read SMILES code and convert it into the required file types such as gjf, xyz, mol, etc.

        args:
            dir: The directory used for outputting files, regardless of existence of the directory.
            smiles: SMILES code to be converted.
            basename: The reference code or number corresponding to SMILES code.
            charge: The charge carried by ions.
        return:
            result_code: Result code 0 or -1, representing success and failure respectively.
            basename: The corresponding basename.
        """
        mol = Chem.MolFromSmiles(smiles)
        mol = Chem.AddHs(mol)
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
                f"Error occurred while optimizing molecule of {basename} with {charge} charge: {e}"
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
                f"{self.base_dir}/{self.csv}/charge_{charge}"
            )
            os.makedirs(charge_dir, exist_ok=True)
            # 通过SMILE_to函数依次处理SMILES码
            for _, row in group.iterrows():
                result_code, basename = self._convert_SMILES(
                    dir=charge_dir,
                    smiles=row["SMILES"],
                    basename=row[self.basename],
                    charge=row["Charge"]
                )
                # 根据私有方法_convert_SMILES的返回值记录refcode对应的分子是否能够成功生成结构文件
                if result_code == 0:
                    success.append(basename)
                elif result_code == 1:
                    fail.append(basename)
        # 将统计信息输出并记录到log文件中
        generation_message = f"\nDuring the .gjf file generation process\n Successfully generated .gjf files: {len(success)}\n Errors encounted: {len(fail)}\n Error {self.basename}: {fail}"
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
        """
        # 另外筛选出符合条件的离子
        screened = self.df
        if group_screen:
            if group_screen_invert:
                screened = screened[
                    screened["SMILES"].str.contains(group_screen, regex=False)
                ]
            else:
                screened = screened[
                    ~screened["SMILES"].str.contains(group_screen, regex=False)
                ]
        if charge_screen:
            screened = screened[screened["Charge"] == charge_screen]
        screened_message = f"\nNumber of ions with charge of [{charge_screen}] and {group_name} group: {len(screened)}\n"
        logging.info(screened_message)
        # 另外创建文件夹, 并依次处理SMILES码
        screened_dir = f"{self.base_dir}/{self.csv}/{group_name}_{charge_screen}"
        os.makedirs(screened_dir, exist_ok=True)
        for _, row in screened.iterrows():
            self._convert_SMILES(
                dir=screened_dir,
                smiles=row["SMILES"],
                basename=row[self.basename],
                charge=row["Charge"]
            )
