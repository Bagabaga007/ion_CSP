#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Machine Learning Potential (MLP) optimization module.

This module provides functionality for optimizing crystal structures using
machine learning potentials such as DeePMD, MACE, and other ML-based force fields.
"""

import os
import re

def _configure_thread_defaults():
    """Set safe numerical-thread defaults without overriding a scheduler."""
    # These values must be set before DeepMD/Torch is imported in a spawn
    # child process.
    for _thread_env_name in ("MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "OMP_NUM_THREADS"):
        os.environ.setdefault(_thread_env_name, "1")


# Keep the worker safe on a new machine while respecting scheduler/container
# settings supplied by the caller.
_configure_thread_defaults()

import sys
import time
import argparse
import json
import importlib.metadata
import shutil
import traceback
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
_FAILURE_LOG_NAME = "mlp_worker_failures.jsonl"
_DEEPMD_BACKENDS = {"deepmd", "dpa4", "dpa4_ion_ft"}
_SUPPORTED_RUNTIME_VERSIONS = {
    "deepmd-kit": "3.2.0",
    "torch": "2.11.0",
    "triton": "3.6.0",
    "ase": "3.23.0",
}
_SUPPORTED_TORCH_VERSION = re.compile(
    r"^2\.11\.0(?:\+[0-9A-Za-z]+(?:[-_.][0-9A-Za-z]+)*)?$",
    re.IGNORECASE,
)


def _is_supported_torch_version(version):
    """Accept Torch 2.11.0 and its PEP 440 local CUDA build suffixes.

    Keep this helper local and stdlib-only: ``mlp_opt.py`` is copied into
    dispatcher task directories and must not import the ion_CSP package just
    to validate runtime metadata.
    """

    return isinstance(version, str) and _SUPPORTED_TORCH_VERSION.fullmatch(version) is not None


class MLPWorkerError(RuntimeError):
    """A worker failure represented only by serializable primitive values."""

    def __init__(
        self,
        index,
        error_type: str,
        message: str,
        traceback_text: str,
    ):
        self.index = index
        self.error_type = str(error_type)
        self.message = str(message)
        self.traceback = str(traceback_text)
        location = f"POSCAR_{index}" if index is not None else "MLP worker pool"
        super().__init__(f"{location} failed with {self.error_type}: {self.message}")

    @classmethod
    def from_record(cls, record):
        return cls(
            record.get("index"),
            record.get("error_type", "UnknownError"),
            record.get("message", ""),
            record.get("traceback", ""),
        )


def _safe_exception_text(error):
    """Convert a possibly unusual exception to a plain string."""
    try:
        return str(error)
    except Exception:  # pragma: no cover - pathological third-party exception
        return f"<{type(error).__name__} could not be rendered>"


def _runtime_distribution_versions(backend):
    """Read installed distribution metadata without importing Torch/DeepMD."""
    if backend not in _DEEPMD_BACKENDS:
        return {}
    versions = {}
    for distribution in _SUPPORTED_RUNTIME_VERSIONS:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
        except Exception as error:  # pragma: no cover - unusual metadata backend
            versions[distribution] = f"<metadata error: {_safe_exception_text(error)}>"
    return versions


def _verify_runtime_metadata(*, index, backend, model_path, device=None):
    """Fail closed when the active DeepMD/Torch runtime is not supported."""
    backend = backend.lower()
    if backend not in _DEEPMD_BACKENDS:
        return {}
    python_actual = f"{sys.version_info[0]}.{sys.version_info[1]}"
    mismatches = {}
    if tuple(sys.version_info[:2]) != (3, 12):
        mismatches["python"] = {
            "expected": "3.12.x",
            "actual": python_actual,
        }
    actual = _runtime_distribution_versions(backend)
    for package, expected in _SUPPORTED_RUNTIME_VERSIONS.items():
        actual_version = actual.get(package)
        matches = (
            _is_supported_torch_version(actual_version)
            if package == "torch"
            else actual_version == expected
        )
        if not matches:
            mismatches[package] = {
                "expected": expected,
                "actual": actual_version,
            }
    if mismatches:
        details = "; ".join(
            f"{package} expected {values['expected']}, found {values['actual']!r}"
            for package, values in mismatches.items()
        )
        raise RuntimeError(
            f"POSCAR_{index}: unsupported MLP runtime for backend={backend}, "
            f"model={model_path!r}, device={device!r}; {details}; "
            f"python={sys.executable}"
        )
    return actual


def _failure_record(index, error, *, backend=None, model=None, device=None):
    """Build a JSON-serializable worker failure record."""
    return {
        "ok": False,
        "index": int(index) if isinstance(index, (int, np.integer)) else index,
        "error_type": type(error).__name__,
        "message": _safe_exception_text(error),
        "traceback": traceback.format_exc(),
        "backend": backend,
        "model": model,
        "device": device,
    }


def _write_failure_record(record, *, output_dir=None):
    """Append a failure record and flush it durably before the pool is stopped."""
    destination_dir = base_dir if output_dir is None else output_dir
    destination = os.path.join(destination_dir, _FAILURE_LOG_NAME)
    payload = dict(record)
    payload["logged_at"] = time.time()
    payload["pid"] = os.getpid()
    try:
        with open(destination, "a", encoding="utf-8") as failure_file:
            failure_file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            failure_file.flush()
            os.fsync(failure_file.fileno())
    except OSError as error:
        # Preserve the scientific failure as the exception propagated to the
        # caller, but make inability to write the diagnostic visible.
        print(f"Unable to persist MLP worker failure report {destination}: {error}")


def _run_opt_worker(task):
    """Run one structure and return only plain data across multiprocessing."""
    if len(task) == 5:
        index, output_dir, backend, model_path, device = task
        probe = False
    else:
        index, output_dir, backend, model_path, device, probe = task
    try:
        calculator = None
        if probe:
            _verify_runtime_metadata(
                index=index,
                backend=backend,
                model_path=model_path,
                device=device,
            )
            calculator = _probe_calculator(
                index,
                backend=backend,
                model_path=model_path,
                device=device,
            )
        run_opt(
            index,
            output_dir=output_dir,
            backend=backend,
            model_path=model_path,
            device=device,
            calculator=calculator,
        )
    except Exception as error:
        return _failure_record(
            index,
            error,
            backend=backend,
            model=model_path,
            device=device,
        )
    return {
        "ok": True,
        "index": int(index) if isinstance(index, (int, np.integer)) else index,
    }


def _shutdown_pool(*, terminate):
    """Close or terminate the global pool exactly once and wait for children."""
    global pool
    active_pool = pool
    pool = None
    if active_pool is None:
        return False
    try:
        if terminate:
            active_pool.terminate()
        else:
            active_pool.close()
    finally:
        active_pool.join()
    return True


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


def _probe_calculator(index, *, backend, model_path, device, calculator=None):
    """Exercise forces and stress before the bulk optimizer is started."""
    atoms = read_vasp("POSCAR_" + str(index))
    if calculator is None:
        calculator = get_mlp_calc(
            relative_path=model_path,
            backend=backend,
            device=device,
        )
    atoms.calc = calculator
    # These calls deliberately cover the DeepMD/Torch neighbor-list and
    # stress paths that a package-import-only preflight cannot validate.
    atoms.get_forces()
    atoms.get_stress()
    return calculator


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
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="number of MLP workers (default: 1; set explicitly for CPU parallelism)",
    )
    return parser.parse_args(argv)


def _resolve_workers(requested, backend):
    """Resolve the safe worker default while preserving explicit CPU parallelism."""
    if not isinstance(requested, int):
        raise ValueError("workers must be a positive integer")
    # Configuration files from older releases used 0 for "automatic".  Keep
    # that input compatible, but make its effective value the safe default of
    # one worker instead of the old DeepMD value of eight.
    workers = 1 if requested == 0 else requested
    if workers < 1:
        raise ValueError("workers must be a positive integer")
    if backend in {"dpa4", "dpa4_ion_ft", "mattersim"} and workers != 1:
        raise ValueError(
            f"{backend} currently requires workers=1 to reuse one GPU model"
        )
    return workers


def main(argv=None):
    """
    Main function to run the optimization with a bounded worker default.

    A real force/stress calculation is performed on the first POSCAR before
    bulk optimization.  Multiprocessing workers return only plain diagnostic
    dictionaries, allowing the parent to preserve the original traceback and
    terminate the pool immediately without retrying the batch serially.
    """
    global pool
    options = _parse_args([] if argv is None else argv)
    workers = _resolve_workers(options.workers, options.backend)
    total_start = time.time()

    # 获取需要优化的结构索引
    # dpdispatcher collects this path even when every optimization succeeds.
    # Keep previous-attempt diagnostics intact if the same task is resumed.
    with open(os.path.join(base_dir, _FAILURE_LOG_NAME), "a", encoding="utf-8"):
        pass
    indexes = get_indexes()
    if not indexes:
        print("No POSCAR_*.vasp files found. Nothing to optimize.")
        return

    failure_recorded = False
    had_pool = False
    pool_cleanup_message = None
    try:
        print(
            f"Starting optimization for {len(indexes)} structures with "
            f"backend={options.backend}, workers={workers}..."
        )
        if workers == 1:
            # Reuse the probed calculator for serial optimization so the exact
            # object that passed the force/stress gate performs the work.
            current_index = indexes[0]
            try:
                _verify_runtime_metadata(
                    index=current_index,
                    backend=options.backend,
                    model_path=options.model,
                    device=options.device,
                )
                calculator = _probe_calculator(
                    current_index,
                    backend=options.backend,
                    model_path=options.model,
                    device=options.device,
                )
                for index in indexes:
                    current_index = index
                    run_opt(
                        index,
                        backend=options.backend,
                        model_path=options.model,
                        device=options.device,
                        calculator=calculator,
                    )
            except Exception as error:
                record = _failure_record(
                    current_index,
                    error,
                    backend=options.backend,
                    model=options.model,
                    device=options.device,
                )
                _write_failure_record(record)
                failure_recorded = True
                raise
        else:
            ctx = multiprocessing.get_context("spawn")
            pool = ctx.Pool(workers)
            had_pool = True

            # Run the gate in a real pool worker and include the first
            # optimization in that same worker invocation.  No bulk task can
            # start until this calculator has successfully produced forces and
            # stress, which catches Triton/ABI/CUDA failures before fan-out.
            first_task = (
                indexes[0],
                None,
                options.backend,
                options.model,
                options.device,
                True,
            )
            first_result = pool.apply(_run_opt_worker, (first_task,))
            if not isinstance(first_result, dict) or not first_result.get("ok"):
                if not isinstance(first_result, dict):
                    raise RuntimeError("MLP worker returned a malformed probe result")
                _write_failure_record(first_result)
                failure_recorded = True
                raise MLPWorkerError.from_record(first_result)

            tasks = [
                (
                    index,
                    None,
                    options.backend,
                    options.model,
                    options.device,
                    False,
                )
                for index in indexes[1:]
            ]
            for result in pool.imap_unordered(_run_opt_worker, tasks):
                if not isinstance(result, dict):
                    raise RuntimeError("MLP worker returned a malformed result")
                if not result.get("ok"):
                    _write_failure_record(result)
                    failure_recorded = True
                    raise MLPWorkerError.from_record(result)

            _shutdown_pool(terminate=False)
            pool_cleanup_message = "Process pool cleaned up successfully."
        print("All optimizations completed successfully.")
    except KeyboardInterrupt:
        # 捕获KeyboardInterrupt，优雅地关闭进程池
        print("\nReceived KeyboardInterrupt, shutting down gracefully...")
        if pool is not None:
            print("Terminating multiprocessing pool...")
            _shutdown_pool(terminate=True)
            pool_cleanup_message = "Process pool terminated after interruption."
        elif had_pool:
            # The pool may already have been stopped while propagating the
            # signal; retain the user-facing cleanup message.
            print("Terminating multiprocessing pool...")
            pool_cleanup_message = "Process pool terminated after interruption."
        print("All child processes terminated.")
        raise
    except Exception as error:
        # A worker or pool error is terminal for this batch.  In particular,
        # do not rerun the same structures serially after an OSError/MemoryError
        # from a broken runtime or an exhausted GPU.
        if not failure_recorded:
            record = _failure_record(
                None,
                error,
                backend=options.backend,
                model=options.model,
                device=options.device,
            )
            _write_failure_record(record)
            failure_recorded = True
        print("Unexpected error during multiprocessing optimization:", error)
        if pool is not None:
            print("Terminating multiprocessing pool...")
            _shutdown_pool(terminate=True)
            pool_cleanup_message = "Process pool terminated after failure."
        raise
    finally:
        if pool is not None:
            _shutdown_pool(terminate=True)
            pool_cleanup_message = pool_cleanup_message or "Process pool terminated during cleanup."
        if pool_cleanup_message:
            print(pool_cleanup_message)
        else:
            print("No process pool to clean up.")
    total_stop = time.time()
    print(f"Total optimization time: {total_stop - total_start:.2f}s")


if __name__=='__main__':
    main(sys.argv[1:])
