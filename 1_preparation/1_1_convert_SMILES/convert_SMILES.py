import os
import time
import logging
from rdkit import Chem
from rdkit.Chem import AllChem
import pandas as pd

class InvalidFileTypeError(Exception):
    '''Custom exception class for handling invalid file type errors'''
    pass

class SMILES_processing:
    def __init__(self, csv_file, target_file_type):
        '''
        args:
            csv_file: the csv file name in the same directory as the script.
            convert_file_type: The targe structure file type to be converted by SMILES code, currently only include gjf, xyz, and mol.
        '''
        # 读取csv文件并处理数据, csv文件分三列, SMILES, Refcode, Charge
        self.base_dir = os.path.dirname(__file__)
        original_df = pd.read_csv(f'{self.base_dir}/{csv_file}')
        # 对SMILES码去重
        df = original_df.drop_duplicates(subset='SMILES')
        # 根据Refcode进行排序
        df = df.sort_values(by='Refcode')
        # 根据Charge分组
        grouped = df.groupby('Charge')
        duplicate_message = f'\nOriginal SMILES dataset: {len(original_df)}\nAfter SMILES deduplication\n Valid SMILES: {len(df)}\n Duplicate SMILES: {len(original_df)-len(df)}'
        print(duplicate_message)
        logging.info(duplicate_message)
        # 支持的文件类型列表，如果不符合则抛出异常
        expeceted_file_types = ['gjf', 'xyz', 'mol']
        if target_file_type not in expeceted_file_types:
            raise InvalidFileTypeError 
        self.file_type = target_file_type
        self.df = df
        self.grouped = grouped
    
    def _convert_SMILES(self, dir:str, smiles:str, refcode:str, charge:int, file_type:str):
        '''
        Private method: Use the rdkit module to read SMILES code and convert it into the required file types such as gjf, xyz, mol, etc.
        
        args: 
            dir: The directory used for outputting files, regardless of existence of the directory.
            smiles: SMILES code to be converted.
            refcode: The reference code corresponding to SMILES code.
            charge: The charge carried by ions.
            file_type: The file type to be converted to.
        return:
            result_code: Result code 0 or -1, representing success and failure respectively.
            refcode: The corresponding refcode.
        '''
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
                logging.error(f'{refcode}: charge wrong! calculated {num_charge} and given {charge}')
            multiplicity = 2 * num_unpaired_electrons + 1
            # 根据type参数判断要生成什么类型的结构文件, 目前只支持gjf, xyz, mol格式
            filename = f'{dir}/{refcode}.{file_type}'
            if file_type == 'gjf':
                # 创建gjf文件内容
                gjf_content = f"%nprocshared=8\n%chk={refcode}.chk\n#p B3LYP/6-31G* opt\n\n{refcode}\n\n{num_charge} {multiplicity}\n"           
                for atom in range(num_atoms):
                    pos = conf.GetAtomPosition(atom)
                    atom_symbol = mol.GetAtomWithIdx(atom).GetSymbol()
                    gjf_content += f"{atom_symbol} {pos.x:.6f} {pos.y:.6f} {pos.z:.6f}\n"            
                # 写入gjf文件
                with open(filename, 'w') as gjf_file:
                    # gjf文件末尾需要空行，否则Gaussian会报End of file in ZSymb错误(l101.exe)
                    gjf_file.write(f'{gjf_content}\n\n')
            elif file_type == 'xyz':
                # 创建xyz文件内容
                xyz_content = f'{num_atoms}\n\n'
                for atom in range(num_atoms):
                    pos = conf.GetAtomPosition(atom)
                    atom_symbol = mol.GetAtomWithIdx(atom).GetSymbol()
                    xyz_content += f'{atom_symbol} {pos.x} {pos.y} {pos.z}\n'
                # 写入xyz文件
                with open(filename, 'w') as xyz_file:
                    xyz_file.write(xyz_content)
            elif file_type == 'mol':
                # 写入mol文件
                with open (filename, 'w') as mol_file:
                    mol_file.write(Chem.MolToMolBlock(mol))
            result_code = 0
        except Exception as e:  # 捕获运行过程中的错误
            print(f'Error occurred while optimizing molecule of {refcode} of {charge}: {e}')
            logging.error(f'Error occurred while optimizing molecule of {refcode} of {charge}: {e}')
            result_code = 1
        # 第一项返回值为结果码0或1, 分别代表成功和失败; 第二项返回值为对应的refcode
        return result_code, refcode

    def charge_group(self):
        '''
        Create folders by grouping according to charges and convert SMILES codes into corresponding structural files.
        '''
        # 分别记录生成结构成功和失败的refcode
        success, fail = [], []
        for charge, group in self.grouped:
            # 根据文件类型与电荷分组创建对应的文件夹
            charge_dir = f'{self.base_dir}/{self.file_type}_files/charge_{charge}'
            os.makedirs(charge_dir, exist_ok=True)
            # 通过SMILE_to函数依次处理SMILES码
            for _, row in group.iterrows():
                result_code, refcode = self._convert_SMILES(dir=charge_dir, smiles=row['SMILES'], refcode=row['Refcode'], charge=row['Charge'], file_type=self.file_type)
                # 根据私有方法_convert_SMILES的返回值记录refcode对应的分子是否能够成功生成结构文件
                if result_code == 0:
                    success.append(refcode)
                elif result_code == -1:
                    fail.append(refcode)
        # 将统计信息输出并记录到log文件中
        generation_message = f'\nDuring the {self.file_type} file generation process\n Successfully generated {self.file_type} files: {len(success)}\n Errors encounted: {len(fail)}\n Error refcode{fail}'
        print(generation_message)
        logging.info(generation_message)

    def screen(self, charge_screen=None, functional_group_screen=None, group_name=None):
        '''
        Screen based on the provided functional groups and charges.
        '''
        # 另外筛选出符合条件的离子
        screened = self.df
        if functional_group_screen:
            screened = screened[screened['SMILES'].str.contains(functional_group_screen, regex=True)]
        if charge_screen:
            screened = screened[screened['Charge'] == charge_screen]
        screened_message = f'\nNumber of ions with charge of [{charge_screen}] and {group_name} group: {len(screened)}\n'
        print(screened_message)
        logging.info(screened_message)
        # 另外创建文件夹, 并依次处理SMILES码
        screened_dir = f'{self.base_dir}/{self.file_type}_files/{group_name}_{charge_screen}'
        os.makedirs(screened_dir, exist_ok=True)
        for _, row in screened.iterrows():
            self._convert_SMILES(dir=screened_dir, smiles=row['SMILES'], refcode=row['Refcode'], charge=row['Charge'], file_type=self.file_type)

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
    # 给定与脚本同目录的csv文件名
    # 通过file_type参数来指定SMILES码所要转化的结构文件类型, 目前只包括gjf, xyz, mol
    result = SMILES_processing(csv_file='ions.csv', target_file_type='gjf')
    # 根据电荷进行分组创建文件夹并将SMILES码转换为对应的结构文件
    result.charge_group()
    # 根据提供的官能团和电荷进行筛选, 在本数据集中硝基的SMILES码为[N+](=O)[O-]
    result.screen(charge_screen=-1, functional_group_screen='\[N\+\]\(=O\)\[O-\]', group_name='nitro')

if __name__ == '__main__':   
    main()
