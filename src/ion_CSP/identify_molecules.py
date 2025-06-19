import os
import logging
from typing import Tuple, List, Dict
from collections import defaultdict, Counter
from ase.io import read
from ase.neighborlist import NeighborList, natural_cutoffs


def identify_molecules(atoms) -> Tuple[List[Dict[str, int]], bool]:
    """
    Identify independent molecules in a given set of atoms.
    This function uses a depth-first search (DFS) approach to find connected components in the atomic structure,
    treating each connected component as a separate molecule.
    params:
        atoms: ASE Atoms object containing the atomic structure.
    returns:
        A tuple containing:
        - A list of dictionaries, each representing a molecule with element counts.
        - A boolean flag indicating whether the identified molecules match the initial set of molecules.
    """
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
    # 获取当前目录下所有 .gjf 文件
    initial_gjf_files = [f for f in os.listdir('./') if f.endswith('.gjf')]
    initial_counts = defaultdict(int)
    for gjf in initial_gjf_files:
        # 提取 .gjf 文件中的元素与原子数量
        gjf_atoms = read(gjf)
        elements = gjf_atoms.get_chemical_symbols()
        counts = Counter(elements)
        # 将元素计数转换为 frozenset 以便于比较
        initial_counts[frozenset(counts.items())] += 1
    # 将初始的分子转换为集合，方便与识别到的分子进行比较
    initial_set = set(initial_counts.keys())
    molecules_flag = (initial_set ==  identified_set)
    initial_information = [{element: count for element, count in mol} for mol in initial_set]
    # 返回合并后的分子及其数量, molecules_flag 标志表示离子数与初始的比对结果
    return merged_molecules, molecules_flag, initial_information

def molecules_information(molecules: List[Dict[str, int]], molecules_flag: bool, initial_information: List[Dict[str, int]]):
    """
    Set the output format of the molecule. Output simplified element information in the specified order of C, N, O, H, which may include other elements.
    params:
        molecules: A list of dictionaries representing identified molecules with element counts.
        molecules_flag: A boolean flag indicating whether the identified molecules match the initial set of molecules.
        initial_information: A list of dictionaries representing the initial set of molecules with element counts.
    """
    # 定义固定顺序的元素
    fixed_order = ['C', 'N', 'O', 'H']
    logging.info(f"Initial molecules: {initial_information}")
    logging.info('Identified independent molecules:')
    for idx, (molecule, count) in enumerate(molecules.items()):
        molecule = dict(molecule)
        total_atoms = sum(molecule.values())  # 计算当前分子的原子总数
        # 构建输出字符串
        output = []
        for element in fixed_order:
            if element in molecule:
                output.append(f"{element}{molecule[element]}") 
        # 如果有其他元素，添加到输出中
        for element in molecule:
            if element not in fixed_order:
                output.append(f"{element}{molecule[element]}")
        formatted_output = ''.join(output)
        logging.info(f'  Molecule {idx + 1} (Total Atoms: {total_atoms}, Count: {count}): {formatted_output}')
    if molecules_flag:
        logging.info('Molecular Comparison Successful\n')
    else:
        logging.warning('Molecular Comparison Failed\n')