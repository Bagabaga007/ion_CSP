#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import time
import numpy as np
import multiprocessing
from ase.io.vasp import read_vasp
from ase.optimize import LBFGS
# from ase.constraints import UnitCellFilter
from ase.filters import ExpCellFilter
from deepmd.calculator import DP
# from mattersim.forcefield import MatterSimCalculator

# 根据脚本位置确定model.pt文件的位置, 减少错误发生
base_dir = os.path.dirname(__file__)
relative_path = './model.pt'
file_path = os.path.join(base_dir, relative_path)
calc = DP(file_path)
# calc = MatterSimCalculator(device="cuda")


def get_element_num(elements):
    """
    Using the Atoms.symples to Know Element and Number

    :params
        elements: list of elements in the structure

    :returns
        element: list of unique elements in the structure
        ele: dictionary with elements as keys and their counts as values
    """
    element = []
    ele = {}
    element.append(elements[0])
    for x in elements: 
        if x not in element :
            element.append(x)
    for x in element: 
        ele[x] = elements.count(x)
    return element, ele 
        
def write_CONTCAR(element, ele, lat, pos, index):
    """
    Write CONTCAR file in VASP format

    :params
        element: list of elements in the structure
        ele: dictionary of element counts
        lat: lattice vectors
        pos: atomic positions in direct coordinates
        index: index for the output file""" 
    f = open(f'{base_dir}/CONTCAR_'+str(index),'w')
    f.write('ASE-MLP-Optimization\n')
    f.write('1.0\n') 
    for i in range(3):
        f.write('%15.10f %15.10f %15.10f\n' % tuple(lat[i]))
    for x in element: 
        f.write(x + '  ')
    f.write('\n') 
    for x in element:
        f.write(str(ele[x]) + '  ') 
    f.write('\n') 
    f.write('Direct\n')
    na = sum(ele.values())
    dpos = np.dot(pos,np.linalg.inv(lat))
    for i in range(na): 
        f.write('%15.10f %15.10f %15.10f\n' % tuple(dpos[i]))
        
def write_OUTCAR(element, ele, masses, volume, lat, pos, ene, force, stress, pstress, index):
    """
    Write OUTCAR file in VASP format
    :params
        element: list of elements in the structure
        ele: dictionary of element counts
        masses: total mass of the atoms
        volume: volume of the unit cell
        lat: lattice vectors
        pos: atomic positions in direct coordinates
        ene: total energy of the system
        force: forces on the atoms
        stress: stress tensor components
        pstress: external pressure
        index: index for the output file
    """
    f = open(f'{base_dir}/OUTCAR_'+str(index),'w')
    for x in element: 
        f.write('VRHFIN =' + str(x) + '\n')
    f.write('ions per type =')
    for x in element:
        f.write('%5d' % ele[x])
    f.write('\nDirection     XX             YY             ZZ             XY             YZ             ZX\n') 
    f.write('in kB') 
    f.write('%15.6f' % stress[0])
    f.write('%15.6f' % stress[1])
    f.write('%15.6f' % stress[2])
    f.write('%15.6f' % stress[3])
    f.write('%15.6f' % stress[4])
    f.write('%15.6f' % stress[5])
    f.write('\n') 
    ext_pressure = np.sum(stress[0] + stress[1] + stress[2])/3.0 - pstress
    f.write('external pressure = %20.6f kB    Pullay stress = %20.6f  kB\n'% (ext_pressure, pstress))
    f.write('volume of cell : %20.6f\n' % volume)
    f.write('direct lattice vectors\n')
    for i in range(3):
        f.write('%10.6f %10.6f %10.6f\n' % tuple(lat[i]))
    f.write('POSITION                                       TOTAL-FORCE(eV/Angst)\n')  
    f.write('-------------------------------------------------------------------\n')
    na = sum(ele.values()) 
    for i in range(na): 
        f.write('%15.6f %15.6f %15.6f' % tuple(pos[i])) 
        f.write('%15.6f %15.6f %15.6f\n' % tuple(force[i]))
    f.write('-------------------------------------------------------------------\n')
    # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
    atoms_density = 1.66054 * masses / volume
    f.write('density = %20.6f\n' % atoms_density)
    f.write('energy  without entropy= %20.6f %20.6f\n' % (ene, ene/na))
    enthalpy = ene + pstress * volume / 1602.17733      
    f.write('enthalpy TOTEN    = %20.6f %20.6f\n' % (enthalpy, enthalpy/na)) 
        
def get_indexes():
    """
    Get the indexes of POSCAR files in the current directory.
    This function scans the current directory for files starting with 'POSCAR_' and extracts their numeric indexes.

    :returns
        A sorted list of indexes extracted from the POSCAR files.
    """
    base_dir = os.path.dirname(__file__)
    POSCAR_files = [f for f in os.listdir(base_dir) if f.startswith('POSCAR_')]
    indexes = []
    for filename in POSCAR_files:
        index_part = filename[len('POSCAR_'):]
        if index_part.isdigit():
            index = int(index_part)
            indexes.append(index)       
    indexes.sort(key=lambda indexes: indexes)
    return indexes

def run_opt(index: int): 
    """
    Using the ASE & MLP to Optimize Configures
    :params
        index: index of the POSCAR file to be optimized
    """
    if os.path.isfile(f'{base_dir}/OUTCAR'):
        os.system(f'mv {base_dir}/OUTCAR {base_dir}/OUTCAR-last')
    fmax, pstress = 0.03, 0

    print('Start to Optimize Structures by MLP----------')
        
    Opt_Step = 2000
    start = time.time() 
    # pstress kbar
    # kBar to eV/A^3
    # 1 eV/A^3 = 160.21766028 GPa
    # 1 / 160.21766028 ~ 0.006242
    aim_stress = 1.0 * pstress * 0.01 * 0.6242 / 10.0 
    atoms = read_vasp('POSCAR_'+str(index)) 
    atoms.calc = calc 
    # ucf = UnitCellFilter(atoms, scalar_pressure=aim_stress)
    ucf = ExpCellFilter(atoms, scalar_pressure=aim_stress)
    # optimization
    opt = LBFGS(ucf) 
    opt.run(fmax=fmax,steps=Opt_Step) 
    # atoms will be optimized and updated during the opt.run process
    atoms_lat = atoms.cell 
    atoms_pos = atoms.positions
    atoms_force = atoms.get_forces() 
    atoms_stress = atoms.get_stress() 
    # eV/A^3 to GPa
    atoms_stress = atoms_stress/(0.01*0.6242)
    atoms_symbols = atoms.get_chemical_symbols() 
    atoms_ene = atoms.get_potential_energy() 
    atoms_masses = sum(atoms.get_masses())
    atoms_vol = atoms.get_volume()
    element, ele = get_element_num(atoms_symbols) 

    write_CONTCAR(element, ele, atoms_lat, atoms_pos, index)
    write_OUTCAR(element, ele, atoms_masses, atoms_vol, atoms_lat, atoms_pos, atoms_ene, atoms_force, -10.0 * atoms_stress, pstress, index)

    stop = time.time()
    _cwd = os.getcwd()
    _cwd = os.path.basename(_cwd)
    print(f'{_cwd} is done, time: {stop-start}')


def main():
    """
    Main function to run the optimization in parallel.
    It initializes a multiprocessing pool and maps the run_opt function to the indexes of POSCAR files.
    """
    ctx=multiprocessing.get_context("spawn")
    pool=ctx.Pool(8)
    indexes = get_indexes()
    pool.map(func=run_opt, iterable=indexes)
    pool.close()
    pool.join()

if __name__=='__main__':
    main()
