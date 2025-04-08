import os
import csv
import time
import glob
import yaml
import logging
import argparse
from typing import List, Dict
from collections import defaultdict, Counter
from ase.io import ParseError, read
from ase.io.vasp import read_vasp_out
from ase.neighborlist import NeighborList, natural_cutoffs

class vasp_processing:

    def __init__(self, work_dir: str):
        self.base_dir = work_dir
        target_folder = f'{work_dir}/4_vasp_optimized'
        os.chdir(target_folder)
        self.folder_dir = target_folder
        logging.info(f'Processing {target_folder}')
        
    def _identify_molecules(self, atoms, check_N5: bool=True, count_N5: int=2) -> List[Dict[str, int]]:
        visited = set()  # 用于记录已经访问过的原子索引
        identified_molecules = []   # 用于存储识别到的独立分子
        # 基于共价半径为每个原子生成径向截止
        cutoffs = natural_cutoffs(atoms, mult=0.7)
        # 获取成键原子，考虑周期性边界条件
        nl = NeighborList(cutoffs=cutoffs, bothways=True, self_interaction=False)
        nl.update(atoms)  # 更新邻居列表
        # 遍历所有原子
        for i in range(len(atoms)):
            # 如果当前原子尚未被访问
            if i not in visited:
                current_molecule = defaultdict(int)  # 用于统计元素及其数量
                stack = [i]  # 使用栈进行深度优先搜索，初始化栈为当前原子索引
                # 深度优先搜索
                while stack:
                    atom_index = stack.pop()  # 从栈中取出一个原子索引
                    if atom_index not in visited:
                        visited.add(atom_index)  # 标记为已访问
                        atom_symbol = atoms[atom_index].symbol  # 获取原子的元素符号
                        current_molecule[atom_symbol] += 1  # 统计该元素的数量
                        # 获取与当前原子成键的原子索引
                        bonded_indices, _ = nl.get_neighbors(atom_index)
                        # 将未访问的成键原子索引添加到栈中
                        stack.extend(idx for idx in bonded_indices if idx not in visited)
                # 如果当前分子包含元素信息，则将其添加到分子列表中
                if current_molecule:
                    identified_molecules.append(current_molecule) 
        # 用于合并分子及其计数
        merged_molecules = defaultdict(int)
        # 将识别到的分子转换为集合，方便与初始分子进行比较
        identified_set = set()
        for molecule in identified_molecules:
            # 将分子信息转换为可哈希的元组形式，以便合并
            molecule_tuple = frozenset(molecule.items())
            merged_molecules[molecule_tuple] += 1  # 计数相同的分子
            identified_set.add(frozenset(molecule.items()))
        # 获取上级目录下所有 .gjf 文件
        gjf_files = glob.glob('../*.gjf')
        initial_counts = defaultdict(int)
        for gjf in gjf_files:
            # 提取 .gjf 文件中的元素与原子数量
            gjf_atoms = read(gjf)
            elements = gjf_atoms.get_chemical_symbols()
            counts = Counter(elements)
            # 将元素计数转换为 frozenset 以便于比较
            initial_counts[frozenset(counts.items())] += 1
        # 将初始的分子转换为集合，方便与识别到的分子进行比较
        initial_set = set(initial_counts.keys())
        molecules_flag = (initial_set ==  identified_set)
        # 设置标志表示 N5 环的检测结果，0表示并未进行检测，1表示有完整 N5 环，-1表示无完整 N5 环
        N5_found, N5_flag = False, 0
        if check_N5:
            # 检查是否存在 N5 分子
            for molecule, count in merged_molecules.items():
                # 确保只有氮元素且数量为 5
                if (dict(molecule).get('N', 0) == 5 and len(molecule) == 1 and count == count_N5):
                    N5_found = True
                    break
            N5_flag = 1 if N5_found else -1
        # 返回合并后的分子及其数量, N_flag 标志表示 N5 环的检测结果, ions_flag 标志表示离子数与初始的比对结果
        return merged_molecules, N5_flag, molecules_flag

    def _molecules_information(self, molecules: List[Dict[str, int]], molecules_flag: int, if_log: bool=False):
        """
        Set the output format of the molecule. Output simplified element information in the specified order of C, N, O, H, which may include other elements.
        """
        # 定义固定顺序的元素
        fixed_order = ['C', 'N', 'O', 'H']
        if if_log:
            logging.info('Identified independent molecules:')
        for idx, (molecule, count) in enumerate(molecules.items()):
            molecule = dict(molecule)
            total_atoms = sum(molecule.values())  # 计算当前分子的原子总数
            # 构建输出字符串
            output = []
            for element in fixed_order:
                if element in molecule:
                    output.append(f"{element} {molecule[element]}") 
            # 如果有其他元素，添加到输出中
            for element in molecule:
                if element not in fixed_order:
                    output.append(f"{element} {molecule[element]}")
            formatted_output =  ' '.join(output)
            logging.info(f'  Molecule {idx + 1} (Total Atoms: {total_atoms}, Count: {count}): {formatted_output}')
        if molecules_flag:
            logging.info('Molecular Comparison Successful\n')
        else:
            logging.warning('Molecular Comparison Failed\n')

    def read_vaspout_save_csv(self, fine_dir: str = 'fine', N5_screen: bool = True, count_N5: int = 2, molecules_check: bool = True):
        """
        Batch read VASP output files and save energy and density to corresponding CSV files in the directory
        
        :param if_fine: If there is step-by-step optimization, further read the refined optimization folder, which defaults to the fine folder in the vasp result directory
        """
        vasp_opt_dir = self.folder_dir
        numbers, mlp_densities, mlp_energies = [], [], []
        rough_densities, rough_energies = [], []
        fine_densities, fine_energies = [], []
        fine_if_N5s, ions_checks = [], []
        for folder in os.listdir(vasp_opt_dir):
            vasp_opt_path = os.path.join(vasp_opt_dir, folder)
            if os.path.isdir(vasp_opt_path):
                mlp_density, number = folder.split('_')[0], folder.split('_')[1]
                numbers.append(number)
                mlp_densities.append(mlp_density)
                # 读取一级目录下的OUTCAR文件
                OUTCAR_file_path = os.path.join(vasp_opt_path, 'OUTCAR')
                logging.info(f'CONTCAR_{mlp_density}_{number}')
                try:
                    with open(f'{vasp_opt_dir}/OUTCAR_{mlp_density}_{number}') as mlp_out:
                        lines = mlp_out.readlines()
                        for line in lines:
                            if 'TOTEN' in line:
                                values = line.split()
                                mlp_energy = round(float(values[-2]), 2)
                except FileNotFoundError:
                    logging.error(f'  No avalible MLP OUTCAR_{mlp_density}_{number} found')
                    mlp_energy = False

                try:
                    rough_atoms = read_vasp_out(OUTCAR_file_path)
                    atoms_volume = rough_atoms.get_volume()  # 体积单位为立方埃（Å³）
                    atoms_masses = sum(rough_atoms.get_masses())  # 质量单位为原子质量单位(amu)
                    # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
                    rough_density = round(1.66054 * atoms_masses / atoms_volume, 4)
                    rough_energy = round(rough_atoms.get_total_energy(), 2)
                    logging.info(f'  MLP_Density: {mlp_density}, MLP_Energy: {mlp_energy}')
                    logging.info(f'  Rough_Density: {rough_density}, Rough_Energy: {rough_energy}')
                except (ParseError, FileNotFoundError):
                    logging.error(f'  Unfinished optimization job of CONTCAR_{mlp_density}_{number}')
                    rough_density, rough_energy = False, False
                
                if fine_dir:
                    # 读取二级目录下的OUTCAR文件
                    fine_OUTCAR_file_path = os.path.join(vasp_opt_path, fine_dir, 'OUTCAR')               
                    try:
                        fine_atoms = read_vasp_out(fine_OUTCAR_file_path)
                        if N5_screen:
                            molecules, N5_flag, mols_flag = self._identify_molecules(fine_atoms, check_N5=True, count_N5=count_N5)
                            fine_N5_flag = True if N5_flag == 1 else False
                        fine_atoms_volume = fine_atoms.get_volume()  # 体积单位为立方埃（Å³）
                        fine_atoms_masses = sum(fine_atoms.get_masses())  # 质量单位为原子质量单位(amu)
                        # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
                        fine_density = round(1.66054 * fine_atoms_masses / fine_atoms_volume, 4)
                        fine_energy = round(fine_atoms.get_total_energy(), 2)
                        logging.info(f'  Fine_Density: {fine_density}, Fine_Energy: {fine_energy}')
                        self._molecules_information(molecules, mols_flag, if_log=True)
                    except (ParseError, FileNotFoundError):
                        logging.error(f'  Unfinished fine optimization job of CONTCAR_{mlp_density}_{number}')
                        fine_density, fine_energy = False, False
                        fine_N5_flag, mols_flag = False, False
                else:
                    fine_density, fine_energy = False, False
                    fine_N5_flag, mols_flag = False, False
                mlp_energies.append(mlp_energy)
                rough_densities.append(rough_density)
                rough_energies.append(rough_energy)
                fine_densities.append(fine_density)
                fine_energies.append(fine_energy)
                fine_if_N5s.append(fine_N5_flag)
                ions_checks.append(mols_flag)

        with open(f'{self.base_dir}/vasp_density_energy.csv', 'w', newline='', encoding='utf-8') as csv_file:
            writer = csv.writer(csv_file)
            header = ['Number', 'MLP_E', 'Rough_E', 'Fine_E', 'MLP_Density', 'Rough_Density', 'Fine_Density']
            if N5_screen:
                header.append('N5_Flag')
            if molecules_check:
                header.append('Ions_Check')
            datas = list(zip(numbers, mlp_energies, rough_energies, fine_energies, mlp_densities, rough_densities, fine_densities, *(fine_if_N5s,) if N5_screen else (), *(ions_checks, ) if molecules_check else ()))
            datas.sort(key=lambda x: (not x[-2], -float(x[6])))
            writer.writerow(header)
            for data in datas:
                writer.writerow(data)

def log_and_time(func):
    """Decorator for recording log information and script runtime""" 
    def wrapper(work_dir, *args, **kwargs):
        # 获取脚本所在目录, 在该目录下生成日志
        script_name = os.path.basename(__file__) 
        log_file_path = os.path.join(work_dir, f'{script_name}_output.log')
        # 配置日志记录
        logging.basicConfig(
            filename = log_file_path,  # 日志文件名
            level = logging.INFO,  # 指定日志级别
            format='%(asctime)s - %(levelname)s - %(message)s'  # 日志格式
        )   
        # 获取程序开始执行时的CPU时间和Wall Clock时间
        start_cpu, start_clock = time.process_time(), time.perf_counter()
        # 记录程序开始信息
        logging.info(f'Start running: {script_name}')
        # 调用实际的函数, 如果出现错误, 报错的同时也将错误信息记录到日志中
        result = None
        try:
            result = func(work_dir, *args, **kwargs)
        except Exception as e:
            logging.error(f'Error occurred: {e}', exc_info=True)
            raise
        print(f'The script {script_name} has run successfully, and the output content has been recorded in the output.log file in the same directory.')
        # 获取程序结束时的CPU时间和Wall Clock时间
        end_cpu, end_clock = time.process_time(), time.perf_counter()
        # 计算CPU时间和Wall Clock时间的差值
        cpu_time, wall_time = end_cpu-start_cpu, end_clock-start_clock
        # 记录程序结束信息
        logging.info(f'End running: {script_name}\nWall time: {wall_time:.4f} sec, CPU time: {cpu_time:.4f} sec\n')
        return result
    return wrapper

@ log_and_time
def main(work_dir, config):
    result = vasp_processing(work_dir)
    # 批量读取 VASP 分步优化的输出文件，并将能量和密度以及 N5 环标志保存到目录中的相应CSV文件
    result.read_vaspout_save_csv(N5_screen=config['vasp_processing']['N5_screen'], 
                                 count_N5=config['vasp_processing']['count_N5'], 
                                 molecules_check=config['vasp_processing']['molecules_check']) 

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process files in a specified working directory')
    parser.add_argument('work_dir', type=str, help='The working directory to run the script in')
    args = parser.parse_args()
    # 尝试读取配置文件
    try:
        with open(os.path.join(args.work_dir, 'config.yaml'), 'r') as file:
            config = yaml.safe_load(file)
    except FileNotFoundError:
        print('config.yaml not found.')
        raise
    # 调用主函数
    main(args.work_dir, config)
