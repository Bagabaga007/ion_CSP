import os
import csv
import time
import logging
from typing import List, Dict
from collections import defaultdict
from ase.io import ParseError
from ase.io.vasp import read_vasp_out
from ase.neighborlist import NeighborList, natural_cutoffs

class vasp_processing:

    def __init__(self, target_folder):
        # 获取脚本的当前目录
        self.base_dir = os.path.dirname(__file__)
        os.chdir(self.base_dir)
        # 寻找同一目录下的optimized文件夹
        logging.info(f'Processing {target_folder}')
        self.folder_dir = os.path.join(self.base_dir, target_folder)
        
    def _identify_molecules(self, atoms, check_N5=True, count_N5=2) -> List[Dict[str, int]]:
        visited = set()  # 用于记录已经访问过的原子索引
        molecules = []   # 用于存储识别到的独立分子
        # 基于共价半径为每个原子生成径向截止
        # threshold = 0.48
        # cutoffs = [threshold] * len(atoms)
        cutoffs = natural_cutoffs(atoms, mult=0.8)
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
                    molecules.append(current_molecule) 
        merged_molecules = defaultdict(int)  # 用于合并分子及其计数
        for molecule in molecules:
            # 将分子信息转换为可哈希的元组形式，以便合并
            molecule_tuple = frozenset(molecule.items())
            merged_molecules[molecule_tuple] += 1  # 计数相同的分子
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
        # 返回合并后的分子及其数量, flag 标志表示 N5 环的检测结果
        return merged_molecules, N5_flag

    def _molecules_information(self, molecules: List[Dict[str, int]], if_log=False):
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
            logging.info(f'Molecule {idx + 1} (Total Atoms: {total_atoms}, Count: {count}): {formatted_output}')

    def read_vaspout_save_csv(self, fine_dir=None, N5_screen=True, count_N5=2, detail_log=True):
        """
        Batch read VASP output files and save energy and density to corresponding CSV files in the directory
        
        :param if_fine: If there is step-by-step optimization, further read the refined optimization folder, which defaults to the fine folder in the vasp result directory
        """
        vasp_opt_dir = self.folder_dir
        numbers, pred_densities = [], []
        rough_densities, rough_energies = [], []
        fine_densities, fine_energies = [], []
        if_N5s = []
        for folder in os.listdir(vasp_opt_dir):
            vasp_opt_path = os.path.join(vasp_opt_dir, folder)
            if os.path.isdir(vasp_opt_path):
                pred_density, number = folder.split('_')[0], folder.split('_')[1]
                numbers.append(number)
                pred_densities.append(pred_density)
                # 读取一级目录下的OUTCAR文件
                OUTCAR_file_path = os.path.join(vasp_opt_path, 'OUTCAR')
                try:
                    atoms = read_vasp_out(OUTCAR_file_path)
                    atoms_volume = atoms.get_volume()  # 体积单位为立方埃（Å³）
                    atoms_masses = sum(atoms.get_masses())  # 质量单位为原子质量单位(amu)
                    # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
                    density = round(1.66054 * atoms_masses / atoms_volume, 4)
                    energy = round(atoms.get_total_energy(), 4)
                    print(f'{folder}: density: {density}, energy: {energy}')
                    logging.info(f'{folder}: density: {density}, energy: {energy}')
                except (ParseError, FileNotFoundError):
                    logging.error(f'Unfinished optimization job of CONTCAR_{pred_density}_{number}')
                    density, energy = 'NaN', 'NaN'
                
                if fine_dir:
                    # 读取二级目录下的OUTCAR文件
                    fine_OUTCAR_file_path = os.path.join(vasp_opt_path, fine_dir, 'OUTCAR')               
                    try:
                        fine_atoms = read_vasp_out(fine_OUTCAR_file_path)
                        if N5_screen:
                            molecules, flag = self._identify_molecules(atoms, check_N5=True, count_N5=count_N5)
                            N5_flag = True if flag == 1 else False
                            if_N5s.append(N5_flag)
                            if detail_log:
                                logging.info(f'CONTCAR_{pred_density}_{number}')
                                self._molecules_information(molecules, if_log=True)
                        fine_atoms_volume = fine_atoms.get_volume()  # 体积单位为立方埃（Å³）
                        fine_atoms_masses = sum(fine_atoms.get_masses())  # 质量单位为原子质量单位(amu)
                        # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
                        fine_density = round(1.66054 * fine_atoms_masses / fine_atoms_volume, 4)
                        fine_energy = round(fine_atoms.get_total_energy(), 4)
                        print(f'{folder}: fine_density: {fine_density}, fine_energy: {fine_energy}')
                        logging.info(f'{folder}: fine_density: {fine_density}, fine_energy: {fine_energy}')
                    except (ParseError, FileNotFoundError):
                        logging.error(f'Unfinished fine optimization job of CONTCAR_{pred_density}_{number}')
                        fine_density, fine_energy = 'NaN', 'NaN'
                else:
                    fine_density, fine_energy = 'NaN', 'NaN'
                rough_densities.append(density)
                rough_energies.append(energy)
                fine_densities.append(fine_density)
                fine_energies.append(fine_energy)

        with open(f'{vasp_opt_dir}/vasp_density_energy.csv', 'w', newline='', encoding='utf-8') as csv_file:
            writer = csv.writer(csv_file)
            header = ['Number', 'Pred_density', 'Rough_density', 'Rough_energy', 'Fine_density', 'Fine_energy']
            if N5_screen:
                header.append('N5_flag')
                datas = list(zip(numbers, pred_densities, rough_densities, rough_energies, fine_densities, fine_energies, if_N5s))
            else:
                datas = list(zip(numbers, pred_densities, rough_densities, rough_energies, fine_densities, fine_energies))
            datas.sort(key=lambda x: float(x[1]), reverse=True)
            writer.writerow(header)
            for data in datas:
                writer.writerow(data)


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
    result = vasp_processing('vasp_optimized_results/vasp_combo_3/primitive_cell')
    result.read_vaspout_save_csv(fine_dir='fine', N5_screen=True, count_N5=2, detail_log=True)


if __name__ == "__main__":
    main()