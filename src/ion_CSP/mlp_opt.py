#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Machine Learning Potential (MLP) optimization module.

This module provides functionality for optimizing crystal structures using
machine learning potentials such as DeePMD, MACE, and other ML-based force fields.
"""

import os

os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "3"
# os.environ["DP_INTRA_OP_PARALLELISM_THREADS"] = "3"
# os.environ["DP_INTER_OP_PARALLELISM_THREADS"] = "2"

import sys
import time
import argparse
import shutil
import numpy as np
import multiprocessing
from ase.optimize import LBFGS
from ase.io.vasp import read_vasp
try:
    from ase.filters import UnitCellFilter
except ImportError:
    from ase.constraints import UnitCellFilter


base_dir = os.path.dirname(__file__)
pool = None  # 用于KeyboardInterrupt处理中终止进程池


def get_mlp_calc(relative_path="./model.pt", backend="deepmd", device=None):
    """
    Get the MLP calculator for ASE.
    This function initializes the DP calculator with a model file located in the same directory as this script.
    """
    backend = backend.lower()
    if backend == "deepmd":
        from deepmd.calculator import DP

        file_path = os.path.join(base_dir, relative_path)
        return DP(file_path)
    if backend in {"dpa4", "dpa4_ion_ft"}:
        from deepmd.calculator import DP

        model_path = relative_path
        local_model_path = os.path.join(base_dir, relative_path)
        if os.path.isfile(local_model_path):
            model_path = local_model_path
        return DP(model_path)
    if backend == "mattersim":
        from mattersim.forcefield import MatterSimCalculator

        model_path = relative_path
        local_model_path = os.path.join(base_dir, relative_path)
        if os.path.isfile(local_model_path):
            model_path = local_model_path
        kwargs = {"load_path": model_path}
        if device:
            kwargs["device"] = device
        return MatterSimCalculator(**kwargs)
    raise ValueError(f"Unsupported MLP backend: {backend}")


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
        
        
def write_CONTCAR(element, ele, lat, pos, index, output_dir=None):
    """
    Write CONTCAR file in VASP format

    :params
        element: list of elements in the structure
        ele: dictionary of element counts
        lat: lattice vectors
        pos: atomic positions in direct coordinates
        index: index for the output file
        output_dir: directory where the CONTCAR file will be saved
    """

    output_dir = base_dir if not output_dir else output_dir
    with open(os.path.join(output_dir, f"CONTCAR_{index}"), "w") as f:
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


def write_OUTCAR(element, ele, masses, volume, lat, pos, ene, force, stress, pstress, index, output_dir=None):
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
        output_dir: directory where the OUTCAR file will be saved
    """
    output_dir = base_dir if not output_dir else output_dir
    with open(os.path.join(output_dir, f"OUTCAR_{index}"), "w") as f:
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
    POSCAR_files = [f for f in os.listdir(base_dir) if f.startswith('POSCAR_')]
    indexes = []
    for filename in POSCAR_files:
        index_part = filename[len('POSCAR_'):]
        if index_part.isdigit() and not os.path.exists(os.path.join(base_dir, f'CONTCAR_{index_part}')):
            index = int(index_part)
            indexes.append(index)       
    indexes.sort(key=lambda indexes: indexes)
    return indexes


def run_opt(
    index: int,
    output_dir=None,
    backend="deepmd",
    model_path="./model.pt",
    device=None,
    calculator=None,
):
    """
    Using the ASE & MLP to Optimize Configures
    :params
        index: index of the POSCAR file to be optimized
        output_dir: directory where the output files will be saved
    """
    output_dir = base_dir if not output_dir else output_dir
    # 修改文件读写路径
    if os.path.isfile(os.path.join(output_dir, "OUTCAR")):
        shutil.move(
            os.path.join(output_dir, "OUTCAR"), os.path.join(output_dir, "OUTCAR-last")
        )
    fmax, pstress = 0.03, 0

    print('Start to Optimize Structures by MLP----------')
        
    Opt_Step = 2000
    start = time.time() 
    # pstress 的单位为 kbar，kbar 与 GPa 的转换关系为 1 kbar = 0.1 GPa
    # GPa 与 eV/A^3 的转换关系为 160.2177 GPa = 1 eV/A^3 
    # 因此 kbar 与 eV/A^3 的转换关系为 1 kbar = 0.1 / 160.2177 = 6.2415*10e-4 eV/A^3
    aim_stress = pstress / 10.0 / 160.2177
    atoms = read_vasp('POSCAR_'+str(index)) 
    atoms.calc = calculator or get_mlp_calc(
        relative_path=model_path, backend=backend, device=device
    )
    ucf = UnitCellFilter(atoms, scalar_pressure=aim_stress)
    # 选择LBFGS优化器进行结构优化
    opt = LBFGS(ucf)
    opt.run(fmax=fmax,steps=Opt_Step)
    # 在 opt.run 期间，atoms 会被持续优化和更新
    atoms_lat = atoms.cell 
    atoms_pos = atoms.positions
    atoms_force = atoms.get_forces() 
    atoms_stress = atoms.get_stress() 
    # eV/A^3 转换回 kbar 用于输出，负号表示该压力由内向外
    atoms_stress = (-atoms_stress) * 10.0 * 160.2177
    atoms_symbols = atoms.get_chemical_symbols() 
    atoms_ene = atoms.get_potential_energy() 
    atoms_masses = sum(atoms.get_masses())
    atoms_vol = atoms.get_volume()
    element, ele = get_element_num(atoms_symbols) 

    write_CONTCAR(element, ele, atoms_lat, atoms_pos, index, output_dir)
    write_OUTCAR(element, ele, atoms_masses, atoms_vol, atoms_lat, atoms_pos, atoms_ene, atoms_force, atoms_stress, pstress, index, output_dir)

    stop = time.time()
    _cwd = os.path.basename(os.getcwd())
    print(f'{_cwd} is done, time: {stop-start}')


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Optimize POSCAR files with an ASE MLP calculator.")
    parser.add_argument(
        "--backend",
        choices=("deepmd", "dpa4", "dpa4_ion_ft", "mattersim"),
        default="deepmd",
    )
    parser.add_argument("--model", default="./model.pt")
    parser.add_argument("--device", default=None)
    parser.add_argument("--workers", type=int, default=0)
    return parser.parse_args(argv)


def main(argv=None):
    """
    Main function to run the optimization in parallel.
    It initializes a multiprocessing pool and maps the run_opt function to the indexes of POSCAR files.
    """
    options = _parse_args([] if argv is None else argv)
    gpu_backends = {"dpa4", "dpa4_ion_ft", "mattersim"}
    workers = options.workers or (1 if options.backend in gpu_backends else 8)
    if workers < 1:
        raise ValueError("workers must be a positive integer")
    if options.backend in gpu_backends and workers != 1:
        raise ValueError(
            f"{options.backend} currently requires workers=1 to reuse one GPU model"
        )
    total_start = time.time()
    # 不再注册信号处理器 - 让父进程的StatusLogger统一处理
    # 子进程会自动继承父进程的信号处理行为
    # signal.signal(signal.SIGINT, stop_handler)
    # signal.signal(signal.SIGTERM, stop_handler)

    # 获取需要优化的结构索引
    indexes = get_indexes()
    if not indexes:
        print("No POSCAR_*.vasp files found. Nothing to optimize.")
        return

    try:
        # 初始化进程池
        print(
            f"Starting optimization for {len(indexes)} structures with "
            f"backend={options.backend}, workers={workers}..."
        )
        if workers == 1:
            calculator = get_mlp_calc(
                relative_path=options.model,
                backend=options.backend,
                device=options.device,
            )
            for index in indexes:
                run_opt(
                    index,
                    backend=options.backend,
                    model_path=options.model,
                    device=options.device,
                    calculator=calculator,
                )
        else:
            ctx = multiprocessing.get_context("spawn")
            global pool
            pool = ctx.Pool(workers)
            if (
                options.backend == "deepmd"
                and options.model == "./model.pt"
                and options.device is None
            ):
                pool.map(func=run_opt, iterable=indexes)
            else:
                pool.starmap(
                    run_opt,
                    [
                        (index, None, options.backend, options.model, options.device)
                        for index in indexes
                    ],
                )
        print("All optimizations completed successfully.")
    except KeyboardInterrupt:
        # 捕获KeyboardInterrupt，优雅地关闭进程池
        print("\nReceived KeyboardInterrupt, shutting down gracefully...")
        if pool is not None:
            print("Terminating multiprocessing pool...")
            pool.terminate()
            pool.join()
        print("All child processes terminated.")
        raise  # 重新抛出，让父进程处理
    except (MemoryError, OSError, PermissionError) as e:
        print("Falling back to serial execution due to resource constraints:", e)
        for index in indexes:
            if (
                options.backend == "deepmd"
                and options.model == "./model.pt"
                and options.device is None
            ):
                run_opt(index)
            else:
                run_opt(
                    index,
                    backend=options.backend,
                    model_path=options.model,
                    device=options.device,
                )
        print("All optimizations completed successfully in serial mode.")
    except Exception as e:
        # worker 抛回的异常或池初始化失败都应向上传播，避免把失败的优化误报为成功
        print("Unexpected error during multiprocessing optimization:", e)
        raise
    finally:
        if pool is not None:
            pool.close()
            pool.join()
            print("Process pool cleaned up successfully.")
        else:
            print("No process pool to clean up.")
    total_stop = time.time()
    print(f"Total optimization time: {total_stop - total_start:.2f}s")


if __name__=='__main__':
    main(sys.argv[1:])
