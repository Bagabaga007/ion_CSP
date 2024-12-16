import os
from typing import List, Dict
from collections import defaultdict
from ase.io import read
from ase.neighborlist import NeighborList, natural_cutoffs


def identify_molecules(atoms) -> List[Dict[str, int]]:
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
    # 返回合并后的分子及其数量
    return merged_molecules

def _format_molecule_output(molecule: Dict[str, int]) -> str:
    """
    Set the output format of the molecule. Output simplified element information in the specified order of C, N, O, H, which may include other elements.
    """
    # 定义固定顺序的元素
    fixed_order = ['C', 'N', 'O', 'H']
    # 构建输出字符串
    output = []
    for element in fixed_order:
        if element in molecule:
            output.append(f"{element} {molecule[element]}") 
    # 如果有其他元素，添加到输出中
    for element in molecule:
        if element not in fixed_order:
            output.append(f"{element} {molecule[element]}")
    return ' '.join(output)

def molecules_information(molecules: List[Dict[str, int]]):
    # 检查是否存在 N5 分子
    N5_found = False
    for molecule in molecules:
        if dict(molecule).get('N', 0) == 5 and len(molecule) == 1:  # 确保只有氮元素且数量为 5
            N5_found = True
    if N5_found:
        print('Identified N5 molecule in the ionic crystal.')
        N5_flag = True  # 设置标志为 True
    else:
        print('N5 molecule not found in the ionic crystal.')
        N5_flag = False  # 设置标志为 False

    print('Identified independent molecules:')
    for idx, (molecule, count) in enumerate(molecules.items()):
        total_atoms = sum(dict(molecule).values())  # 计算当前分子的原子总数
        formatted_output = _format_molecule_output(dict(molecule))
        print(f'Molecule {idx + 1} (Total Atoms: {total_atoms}, Count: {count}): {formatted_output}')
    # 返回 flag 表示是否有完整 N5 环
    return N5_flag

base_dir = os.path.dirname(__file__)
os.chdir(base_dir)
# 读取优化后的结构文件
atoms = read('CONTCAR')  # 替换为你的文件名
# 识别独立分子
molecules = identify_molecules(atoms)
# 输出识别到的独立分子及其元素信息
N5_flag = molecules_information(molecules)
print(N5_flag)
