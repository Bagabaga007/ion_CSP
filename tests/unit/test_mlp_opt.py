import os
import sys
import json
from pathlib import Path
import pytest
import numpy as np
from io import StringIO
from unittest.mock import patch, MagicMock, ANY

# 在导入 mlp_opt 之前，深度模拟 torch.load 和 DP 类
sys.modules["torch"] = MagicMock()
sys.modules["torch"].load = MagicMock()
sys.modules["deepmd"] = MagicMock()
sys.modules["deepmd.calculator"] = MagicMock()
sys.modules["deepmd.calculator"].DP = MagicMock()

from ion_CSP.mlp_opt import (
    MLPWorkerError,
    _probe_calculator,
    _configure_thread_defaults,
    _is_supported_torch_version,
    _runtime_distribution_versions,
    _resolve_workers,
    _run_opt_worker,
    _verify_runtime_metadata,
    get_mlp_calc,
    get_element_num,
    write_CONTCAR,
    write_OUTCAR,
    get_indexes,
    run_opt,
    main,
)


@pytest.fixture(autouse=True)
def reset_pool():
    """Reset global pool variable to None before each test."""
    mlp_opt = sys.modules["ion_CSP.mlp_opt"]
    mlp_opt.pool = None  # 强制重置


# 全局 fixture 设置 base_dir 和模拟 model.pt
@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path, monkeypatch):
    # 1. 设置 base_dir 为临时目录
    monkeypatch.setattr("ion_CSP.mlp_opt.base_dir", str(tmp_path))
    # 2. 在临时目录中创建一个假的 model.pt 文件
    fake_model_path = os.path.join(tmp_path, "model.pt")
    with open(fake_model_path, "w") as f:
        f.write("Fake model content")

    # 3. 保存原始的文件存在检查函数
    original_isfile = os.path.isfile

    # 4. 定义模拟文件存在检查
    def mock_isfile(path):
        # 对于 model.pt 文件总是返回 True
        if "model.pt" in path:
            return True
        # 对于其他文件使用原始函数
        return original_isfile(path)

    # 5. 应用模拟
    monkeypatch.setattr("os.path.isfile", mock_isfile)
    monkeypatch.setattr("torch.load", MagicMock())
    monkeypatch.setattr("deepmd.calculator.DP", MagicMock())

    return tmp_path

# ==================== 测试 get_element_num 函数 ====================
def test_get_element_num():
    elements = ["H", "O", "H", "O", "C"]
    unique_elements, element_count = get_element_num(elements)
    assert unique_elements == ["H", "O", "C"]
    assert element_count == {"H": 2, "O": 2, "C": 1}


def test_get_mlp_calc_mattersim(monkeypatch):
    calculator_class = MagicMock()
    forcefield_module = MagicMock()
    forcefield_module.MatterSimCalculator = calculator_class
    monkeypatch.setitem(sys.modules, "mattersim", MagicMock())
    monkeypatch.setitem(sys.modules, "mattersim.forcefield", forcefield_module)

    get_mlp_calc(
        relative_path="MatterSim-v1.0.0-5M.pth",
        backend="mattersim",
        device="cuda",
    )

    calculator_class.assert_called_once_with(
        load_path="MatterSim-v1.0.0-5M.pth",
        device="cuda",
    )


def test_get_mlp_calc_dpa4(monkeypatch):
    calculator_class = MagicMock()
    calculator_module = MagicMock()
    calculator_module.DP = calculator_class
    monkeypatch.setitem(sys.modules, "deepmd", MagicMock())
    monkeypatch.setitem(sys.modules, "deepmd.calculator", calculator_module)

    get_mlp_calc(
        relative_path="DPA4-Nano-OMat24-v20260805",
        backend="dpa4",
    )

    calculator_class.assert_called_once_with("DPA4-Nano-OMat24-v20260805")


def test_get_mlp_calc_dpa4_ion_ft(monkeypatch):
    calculator_class = MagicMock()
    calculator_module = MagicMock()
    calculator_module.DP = calculator_class
    monkeypatch.setitem(sys.modules, "deepmd", MagicMock())
    monkeypatch.setitem(sys.modules, "deepmd.calculator", calculator_module)

    get_mlp_calc(
        relative_path="/models/dpa4_ion_ft.pt",
        backend="dpa4_ion_ft",
        device="cuda",
    )

    calculator_class.assert_called_once_with("/models/dpa4_ion_ft.pt")


def test_get_mlp_calc_rejects_unknown_backend():
    with pytest.raises(ValueError, match="Unsupported MLP backend"):
        get_mlp_calc(backend="unknown")


# ==================== 测试 write_CONTCAR 函数 ====================
def test_write_contcar(setup_test_environment):
    element = ["H", "O"]
    ele = {"H": 2, "O": 1}
    lat = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    pos = np.array([[0.0, 0.0, 0.0], [0.5, 0.5, 0.5], [0.0, 0.5, 0.5]])
    index = 1
    filename = os.path.join(setup_test_environment, f"CONTCAR_{index}")

    write_CONTCAR(element, ele, lat, pos, index)

    # 检查文件是否创建在临时目录
    assert os.path.exists(filename)

    with open(filename, "r") as f:
        content = f.readlines()

    assert content[0].strip() == "ASE-MLP-Optimization"
    assert "H" in content[5] and "O" in content[5]
    assert content[6].strip() == "2  1"


# ==================== 测试 write_OUTCAR 函数 ====================
def test_write_outcar(setup_test_environment):
    element = ["H", "O"]
    ele = {"H": 2, "O": 1}
    masses = 3.0  # 2 H + 1 O
    volume = 1.0
    lat = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    pos = np.array([[0.0, 0.0, 0.0], [0.5, 0.5, 0.5], [0.0, 0.5, 0.5]])
    ene = -1.0
    force = np.zeros((3, 3))
    stress = np.zeros(6)
    pstress = 0.0
    index = 1
    filename = os.path.join(setup_test_environment, f"OUTCAR_{index}")

    write_OUTCAR(
        element, ele, masses, volume, lat, pos, ene, force, stress, pstress, index
    )

    # 检查文件是否创建在临时目录
    assert os.path.exists(filename)

    with open(filename, "r") as f:
        content = f.readlines()

    assert "density =" in content[-3]
    assert "enthalpy TOTEN" in content[-1]


# ==================== 测试 get_indexes 函数 ====================
def test_get_indexes(monkeypatch):
    # 模拟 POSCAR 文件
    test_files = ["POSCAR_1", "POSCAR_2", "POSCAR_10", "POSCAR_3"]
    monkeypatch.setattr("os.listdir", lambda _: test_files)

    indexes = get_indexes()
    assert indexes == [1, 2, 3, 10]


def test_get_indexes_non_digit_index(monkeypatch):
    test_files = [
        "POSCAR_1",
        "POSCAR_abc",
        "POSCAR_2.5",
        "POSCAR_",
        "POSCAR_10",
        "POSCAR_3",
        "POSCAR_abc.txt",
    ]
    monkeypatch.setattr("os.listdir", lambda _: test_files)

    def mock_exists(path):
        return "CONTCAR_1" in path or "CONTCAR_10" in path

    monkeypatch.setattr("os.path.exists", mock_exists)

    indexes = get_indexes()
    assert indexes == [3], (
        f"Expected [3], got {indexes}"
    )


# ==================== 测试 run_opt 函数 ====================
@patch("ion_CSP.mlp_opt.read_vasp")
@patch("ion_CSP.mlp_opt.UnitCellFilter")
@patch("ion_CSP.mlp_opt.LBFGS")
def test_run_opt(
    mock_LBFGS, mock_UnitCellFilter, mock_read_vasp, setup_test_environment
):
    # 创建模拟的 POSCAR 文件
    poscar_path = os.path.join(setup_test_environment, "POSCAR_1")
    with open(poscar_path, "w") as f:
        f.write("Mock POSCAR content")

    # 设置模拟的 Atoms 对象
    mock_atoms = MagicMock()
    mock_atoms.cell = np.eye(3)
    mock_atoms.positions = np.array([[0, 0, 0], [0.5, 0.5, 0.5]])
    mock_atoms.get_chemical_symbols.return_value = ["Si", "Si"]
    mock_atoms.get_potential_energy.return_value = -10.0
    mock_atoms.get_forces.return_value = np.array([[0.1, 0.1, 0.1], [-0.1, -0.1, -0.1]])
    mock_atoms.get_stress.return_value = np.array([0.1, 0.1, 0.1, 0.0, 0.0, 0.0])
    mock_atoms.get_masses.return_value = [28, 28]
    mock_atoms.get_volume.return_value = 100.0

    mock_read_vasp.return_value = mock_atoms

    # 运行优化
    run_opt(1)

    # 检查输出文件是否创建在临时目录
    assert os.path.exists(os.path.join(setup_test_environment, "CONTCAR_1"))
    assert os.path.exists(os.path.join(setup_test_environment, "OUTCAR_1"))


@patch("ion_CSP.mlp_opt.read_vasp")
@patch("ion_CSP.mlp_opt.UnitCellFilter")
@patch("ion_CSP.mlp_opt.LBFGS")
@patch("ion_CSP.mlp_opt.shutil.move")
@patch("os.path.isfile")
def test_run_opt_with_existing_outcar(
    mock_isfile,
    mock_move,
    mock_LBFGS,
    mock_UnitCellFilter,
    mock_read_vasp,
    setup_test_environment,
):
    """测试当 OUTCAR 存在时，是否被正确重命名为 OUTCAR-last"""
    # 1. 设置测试环境
    index = 1
    output_dir = setup_test_environment

    # 2. 模拟 OUTCAR 文件存在
    mock_isfile.return_value = True  # ← 关键：模拟 OUTCAR 存在

    # 3. 模拟 Atoms 对象
    mock_atoms = MagicMock()
    mock_atoms.cell = np.eye(3)
    mock_atoms.positions = np.array([[0, 0, 0], [0.5, 0.5, 0.5]])
    mock_atoms.get_chemical_symbols.return_value = ["Si", "Si"]
    mock_atoms.get_potential_energy.return_value = -10.0
    mock_atoms.get_forces.return_value = np.array([[0.1, 0.1, 0.1], [-0.1, -0.1, -0.1]])
    mock_atoms.get_stress.return_value = np.array([0.1, 0.1, 0.1, 0.0, 0.0, 0.0])
    mock_atoms.get_masses.return_value = [28, 28]
    mock_atoms.get_volume.return_value = 100.0
    mock_read_vasp.return_value = mock_atoms

    # 4. 调用 run_opt
    run_opt(index)

    # 5. 验证：shutil.move 被调用，参数正确
    mock_move.assert_called_once_with(
        os.path.join(output_dir, "OUTCAR"), os.path.join(output_dir, "OUTCAR-last")
    )

    # 6. 验证：OUTCAR 文件被创建（原测试已覆盖）
    assert os.path.exists(os.path.join(output_dir, f"CONTCAR_{index}"))
    assert os.path.exists(os.path.join(output_dir, f"OUTCAR_{index}"))

    # 7. 验证：原 OUTCAR 被移动，不再是原文件
    # 注意：我们没有实际创建 OUTCAR，但 mock_move 已验证行为


# ==================== 测试 worker 诊断与实际计算探针 ====================
def test_worker_failure_is_json_serializable_and_keeps_traceback(monkeypatch):
    """Worker failures must not carry an unpicklable exception object."""

    class UnpicklableTritonError(RuntimeError):
        def __reduce__(self):
            raise TypeError("cannot pickle this synthetic exception")

    with patch(
        "ion_CSP.mlp_opt.run_opt",
        side_effect=UnpicklableTritonError("TritonMissing: compiler unavailable"),
    ):
        result = _run_opt_worker((17, None, "deepmd", "./model.pt", None))

    # This is the exact boundary sent through multiprocessing's result queue.
    json.dumps(result)
    assert result["ok"] is False
    assert result["index"] == 17
    assert result["error_type"] == "UnpicklableTritonError"
    assert "TritonMissing" in result["message"]
    assert "UnpicklableTritonError" in result["traceback"]


@patch("ion_CSP.mlp_opt.run_opt")
@patch("ion_CSP.mlp_opt._probe_calculator")
def test_worker_probe_reuses_calculator_for_first_structure(
    mock_probe, mock_run_opt
):
    calculator = MagicMock(name="calculator")
    mock_probe.return_value = calculator

    with patch("ion_CSP.mlp_opt._verify_runtime_metadata") as mock_verify:
        result = _run_opt_worker((3, None, "deepmd", "./model.pt", "cuda", True))

    assert result == {"ok": True, "index": 3}
    mock_verify.assert_called_once_with(
        index=3,
        backend="deepmd",
        model_path="./model.pt",
        device="cuda",
    )
    mock_probe.assert_called_once_with(
        3,
        backend="deepmd",
        model_path="./model.pt",
        device="cuda",
    )
    mock_run_opt.assert_called_once_with(
        3,
        output_dir=None,
        backend="deepmd",
        model_path="./model.pt",
        device="cuda",
        calculator=calculator,
    )


@patch("ion_CSP.mlp_opt.read_vasp")
@patch("ion_CSP.mlp_opt.get_mlp_calc")
def test_probe_exercises_forces_and_stress(mock_get_calc, mock_read_vasp):
    atoms = MagicMock()
    calculator = MagicMock(name="calculator")
    mock_read_vasp.return_value = atoms
    mock_get_calc.return_value = calculator

    result = _probe_calculator(
        5,
        backend="deepmd",
        model_path="./model.pt",
        device="cuda",
    )

    assert result is calculator
    mock_read_vasp.assert_called_once_with("POSCAR_5")
    mock_get_calc.assert_called_once_with(
        relative_path="./model.pt",
        backend="deepmd",
        device="cuda",
    )
    atoms.get_forces.assert_called_once_with()
    atoms.get_stress.assert_called_once_with()


def test_resolve_workers_uses_safe_default_and_preserves_cpu_parallelism():
    assert _resolve_workers(0, "deepmd") == 1
    assert _resolve_workers(1, "deepmd") == 1
    assert _resolve_workers(4, "deepmd") == 4
    assert _resolve_workers(0, "mattersim") == 1
    with pytest.raises(ValueError, match="positive integer"):
        _resolve_workers(-1, "deepmd")
    with pytest.raises(ValueError, match="requires workers=1"):
        _resolve_workers(2, "mattersim")


def test_runtime_metadata_accepts_supported_deepmd_pair(monkeypatch):
    expected = {
        "deepmd-kit": "3.2.0",
        "torch": "2.11.0",
        "triton": "3.6.0",
        "ase": "3.23.0",
    }

    def version(name):
        return expected[name]

    monkeypatch.setattr("ion_CSP.mlp_opt.importlib.metadata.version", version)
    monkeypatch.setattr("ion_CSP.mlp_opt.sys.version_info", (3, 12))

    assert _runtime_distribution_versions("deepmd") == expected
    assert _verify_runtime_metadata(
        index=12,
        backend="dpa4",
        model_path="./model.pt",
        device="cuda",
    ) == expected


def test_runtime_metadata_accepts_torch_cuda_local_version_and_preserves_it(monkeypatch):
    actual = {
        "deepmd-kit": "3.2.0",
        "torch": "2.11.0+cu126",
        "triton": "3.6.0",
        "ase": "3.23.0",
    }
    monkeypatch.setattr(
        "ion_CSP.mlp_opt.importlib.metadata.version",
        lambda name: actual[name],
    )
    monkeypatch.setattr("ion_CSP.mlp_opt.sys.version_info", (3, 12))

    assert _is_supported_torch_version("2.11.0+cu126")
    assert _runtime_distribution_versions("deepmd")["torch"] == "2.11.0+cu126"
    assert _verify_runtime_metadata(
        index=13,
        backend="deepmd",
        model_path="./model.pt",
        device="cuda",
    ) == actual


def test_runtime_metadata_fails_with_poscar_context_on_torch_drift(monkeypatch):
    actual = {
        "deepmd-kit": "3.2.0",
        "torch": "2.12.0",
        "triton": "3.6.0",
        "ase": "3.23.0",
    }
    monkeypatch.setattr(
        "ion_CSP.mlp_opt.importlib.metadata.version",
        lambda name: actual[name],
    )
    monkeypatch.setattr("ion_CSP.mlp_opt.sys.version_info", (3, 12))

    with pytest.raises(RuntimeError, match="POSCAR_12.*torch expected 2.11.0"):
        _verify_runtime_metadata(
            index=12,
            backend="deepmd",
            model_path="./model.pt",
            device="cuda",
        )


def test_runtime_metadata_requires_triton_distribution(monkeypatch):
    actual = {
        "deepmd-kit": "3.2.0",
        "torch": "2.11.0",
        "ase": "3.23.0",
    }

    def version(name):
        if name == "triton":
            from importlib.metadata import PackageNotFoundError

            raise PackageNotFoundError(name)
        return actual[name]

    monkeypatch.setattr("ion_CSP.mlp_opt.importlib.metadata.version", version)
    monkeypatch.setattr("ion_CSP.mlp_opt.sys.version_info", (3, 12))

    with pytest.raises(RuntimeError, match="triton expected 3.6.0, found None"):
        _verify_runtime_metadata(
            index=2,
            backend="deepmd",
            model_path="./model.pt",
        )


def test_runtime_metadata_rejects_python_311(monkeypatch):
    expected = {
        "deepmd-kit": "3.2.0",
        "torch": "2.11.0",
        "triton": "3.6.0",
        "ase": "3.23.0",
    }
    monkeypatch.setattr(
        "ion_CSP.mlp_opt.importlib.metadata.version",
        lambda name: expected[name],
    )
    monkeypatch.setattr("ion_CSP.mlp_opt.sys.version_info", (3, 11))

    with pytest.raises(RuntimeError, match="POSCAR_9.*python expected 3.12.x"):
        _verify_runtime_metadata(
            index=9,
            backend="deepmd",
            model_path="./model.pt",
        )


def test_thread_defaults_honor_scheduler_environment(monkeypatch):
    """Import-time defaults must not overwrite an allocated job's settings."""
    monkeypatch.setenv("OMP_NUM_THREADS", "7")
    monkeypatch.setenv("MKL_NUM_THREADS", "5")
    monkeypatch.setenv("NUMEXPR_NUM_THREADS", "3")

    _configure_thread_defaults()

    assert os.environ["OMP_NUM_THREADS"] == "7"
    assert os.environ["MKL_NUM_THREADS"] == "5"
    assert os.environ["NUMEXPR_NUM_THREADS"] == "3"


# ==================== 测试 main 函数 ====================
@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
def test_main_normal_exit(
    mock_get_indexes,
    mock_get_context,
):
    """显式 CPU 并行仍可用，且首个任务先通过真实探针。"""
    # 1. 模拟 get_indexes 返回任务
    mock_get_indexes.return_value = [1, 2]

    # 2. 模拟 multiprocessing.get_context 返回一个 mock 上下文
    mock_context = MagicMock()
    mock_get_context.return_value = mock_context

    # 3. 在 mock 上下文中，mock .Pool 方法（这是关键！）
    mock_pool_instance = MagicMock()
    mock_context.Pool.return_value = mock_pool_instance

    # 4. 调用 main
    mock_pool_instance.apply.return_value = {"ok": True, "index": 1}
    mock_pool_instance.imap_unordered.return_value = [{"ok": True, "index": 2}]
    main(["--workers", "2"])

    # 5. 验证调用顺序
    mock_get_context.assert_called_once_with("spawn")

    # 6. 验证 Pool 和 map 调用
    mock_context.Pool.assert_called_once_with(2)
    mock_pool_instance.apply.assert_called_once_with(
        _run_opt_worker,
        ((1, None, "deepmd", "./model.pt", None, True),),
    )
    mock_pool_instance.imap_unordered.assert_called_once_with(
        _run_opt_worker,
        [(2, None, "deepmd", "./model.pt", None, False)],
    )
    mock_pool_instance.close.assert_called_once()
    mock_pool_instance.join.assert_called_once()


@patch("ion_CSP.mlp_opt.get_indexes")
@patch("ion_CSP.mlp_opt.run_opt")
@patch("ion_CSP.mlp_opt._probe_calculator")
def test_main_default_deepmd_uses_one_worker(
    mock_probe,
    mock_run_opt,
    mock_get_indexes,
):
    """The implicit DeepMD default is serial, avoiding GPU/model fan-out."""
    mock_get_indexes.return_value = [1, 2]
    calculator = MagicMock(name="calculator")
    mock_probe.return_value = calculator

    with patch("ion_CSP.mlp_opt._verify_runtime_metadata") as mock_verify:
        main()

    mock_verify.assert_called_once_with(
        index=1,
        backend="deepmd",
        model_path="./model.pt",
        device=None,
    )
    mock_probe.assert_called_once_with(
        1,
        backend="deepmd",
        model_path="./model.pt",
        device=None,
    )
    assert mock_run_opt.call_count == 2
    assert all(call.kwargs["calculator"] is calculator for call in mock_run_opt.call_args_list)


@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
@patch("ion_CSP.mlp_opt.run_opt")
def test_main_worker_failure_terminates_without_serial_retry(
    mock_run_opt,
    mock_get_indexes,
    mock_get_context,
):
    """A worker runtime failure is terminal and is not retried serially."""
    mock_get_indexes.return_value = [4, 5]
    mock_context = MagicMock()
    mock_get_context.return_value = mock_context
    mock_pool = MagicMock()
    mock_context.Pool.return_value = mock_pool
    mock_pool.apply.return_value = {"ok": True, "index": 4}
    mock_pool.imap_unordered.return_value = [
        {
            "ok": False,
            "index": 5,
            "error_type": "TritonMissing",
            "message": "Triton compiler unavailable",
            "traceback": "Traceback ... TritonMissing",
            "backend": "deepmd",
            "model": "./model.pt",
            "device": None,
        }
    ]

    with pytest.raises(MLPWorkerError, match="POSCAR_5") as raised:
        main(["--workers", "2"])

    assert raised.value.index == 5
    assert raised.value.error_type == "TritonMissing"
    mock_pool.terminate.assert_called_once()
    mock_pool.join.assert_called_once()
    mock_pool.close.assert_not_called()
    mock_run_opt.assert_not_called()

    failure_log = Path(sys.modules["ion_CSP.mlp_opt"].base_dir) / "mlp_worker_failures.jsonl"
    record = json.loads(failure_log.read_text(encoding="utf-8").splitlines()[-1])
    assert record["index"] == 5
    assert record["error_type"] == "TritonMissing"


@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
def test_main_no_files(
    mock_get_indexes,
    mock_get_context,
    tmp_path,
    monkeypatch,
):
    """测试 main 函数在没有任务文件时的退出行为"""
    # 1. 模拟没有任务文件
    mock_get_indexes.return_value = []  # ← 关键：没有文件！

    # 2. 调用 main
    main()

    # 3. 验证进程池没有启动
    mock_get_context.assert_not_called()
    # 由于 get_context 没被调用，Pool 也不会被调用，无需额外验证
    failure_log = Path(sys.modules["ion_CSP.mlp_opt"].base_dir) / "mlp_worker_failures.jsonl"
    assert failure_log.exists()
    assert failure_log.read_text(encoding="utf-8") == ""


@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
@patch("ion_CSP.mlp_opt.run_opt")
def test_main_pool_creation_fails(
    mock_run_opt,
    mock_get_indexes,
    mock_get_context,
    tmp_path,
    monkeypatch,
):
    """池创建失败时应直接失败，不隐式串行重跑整个批次。"""
    # 1. 模拟有任务
    mock_get_indexes.return_value = [1, 2]

    # 2. 模拟 get_context 返回一个 mock 上下文
    mock_context = MagicMock()
    mock_get_context.return_value = mock_context

    # 3. 关键：模拟 ctx.Pool 方法抛异常
    mock_context.Pool.side_effect = MemoryError("Failed to create process pool")

    # 4. 调用 main()；资源/运行时错误是终态错误
    with pytest.raises(MemoryError, match="Failed to create process pool"):
        main(["--workers", "2"])

    # 5. 验证：ctx.Pool 被调用一次
    mock_context.Pool.assert_called_once_with(2)

    # 6. 验证：不进入串行 fallback
    mock_run_opt.assert_not_called()

    failure_log = Path(sys.modules["ion_CSP.mlp_opt"].base_dir) / "mlp_worker_failures.jsonl"
    assert failure_log.exists()
    record = json.loads(failure_log.read_text(encoding="utf-8").splitlines()[-1])
    assert record["index"] is None
    assert "Failed to create process pool" in record["message"]


@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
@patch("ion_CSP.mlp_opt.run_opt")
def test_main_finally_block_pool_is_none(
    mock_run_opt,
    mock_get_indexes,
    mock_get_context,
):
    # 1. 设置模拟行为
    # 让 get_indexes 返回一些任务，确保程序会尝试进入多进程流程
    mock_get_indexes.return_value = [1, 2, 3]

    # 2. 模拟 get_context() 调用抛出异常，阻止 pool 被创建
    # 这使得 pool 变量不会被赋值，保持为 None
    mock_get_context.side_effect = RuntimeError(
        "Simulated failure during context acquisition"
    )

    # 3. 模拟一个假的 Pool 类来跟踪调用
    fake_pool_class = MagicMock()
    mock_get_context.return_value.Pool = fake_pool_class

    # 4. 执行测试：异常应向上传播（不再被静默吞掉）
    with pytest.raises(RuntimeError, match="Simulated failure during context acquisition"):
        main(["--workers", "2"])

    # 5. 验证关键行为
    # 5.1 验证 get_context 被调用过（尝试创建 pool）
    mock_get_context.assert_called_once_with("spawn")

    # 5.2 由于 pool 为 None，没有任何方法在其上被调用
    mock_run_opt.assert_not_called()

    # 5.3 Pool 构造函数未被调用以及类方法未被调用
    fake_pool_class.assert_not_called()
    assert not fake_pool_class.called


@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
def test_main_finally_else_branch(
    mock_get_indexes,
    mock_get_context,
):
    """测试：当 get_context() 抛异常时，pool 保持为 None，finally 执行 else 分支"""

    # 1. 捕获 stdout
    captured_output = StringIO()
    with patch("sys.stdout", new=captured_output):
        # 2. 设置模拟行为
        mock_get_indexes.return_value = [1, 2, 3]  # 有任务，进入流程
        # 3. 关键：让 get_context() 抛异常，pool 不会被赋值（保持 None）
        mock_get_context.side_effect = RuntimeError(
            "Simulated failure during context acquisition"
        )

        # 4. 执行 main()：异常应向上传播（不再被静默吞掉）
        with pytest.raises(RuntimeError, match="Simulated failure during context acquisition"):
            main(["--workers", "2"])

        # 5. 获取打印输出
        output = captured_output.getvalue()

        # 6. 验证：get_context 被调用（尝试创建池）
        mock_get_context.assert_called_once_with("spawn")

        # 7. 关键：验证 else 分支的打印内容（finally 仍在异常传播前执行）
        assert "No process pool to clean up." in output, (
            f"Expected 'No process pool to clean up.' in output, got:\n{output}"
        )

        # 8. 验证：没有打印 "Process pool cleaned up successfully."（因为没进 if）
        assert "Process pool cleaned up successfully." not in output, (
            "Unexpected 'Process pool cleaned up successfully.' printed when pool is None"
        )

        # 9. 验证：有预期的异常提示
        assert (
            "Unexpected error during multiprocessing optimization" in output
        )


@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
def test_main_keyboard_interrupt(
    mock_get_indexes,
    mock_get_context,
):
    """测试：当 pool.map 抛出 KeyboardInterrupt 时，main() 优雅地关闭进程池"""
    # 1. 模拟有任务
    mock_get_indexes.return_value = [1, 2, 3]

    # 2. 模拟 get_context 返回一个 mock 上下文
    mock_context = MagicMock()
    mock_get_context.return_value = mock_context

    # 3. 模拟 Pool 实例
    mock_pool_instance = MagicMock()
    mock_context.Pool.return_value = mock_pool_instance

    # 4. 关键：让首个探针后的批量迭代抛出 KeyboardInterrupt
    mock_pool_instance.apply.return_value = {"ok": True, "index": 1}
    mock_pool_instance.imap_unordered.side_effect = KeyboardInterrupt("User interrupted")

    # 5. 捕获 stdout
    captured_output = StringIO()
    with patch("sys.stdout", new=captured_output):
        # 6. 执行 main()，应该捕获并重新抛出 KeyboardInterrupt
        try:
            main(["--workers", "2"])
            assert False, "Expected KeyboardInterrupt to be raised"
        except KeyboardInterrupt:
            pass  # 预期的异常

        # 7. 获取打印输出
        output = captured_output.getvalue()

        # 8. 验证：Pool 被创建
        mock_context.Pool.assert_called_once_with(2)

        # 9. 验证：首个任务先执行探针，随后进入批量迭代
        mock_pool_instance.apply.assert_called_once_with(
            _run_opt_worker,
            ((1, None, "deepmd", "./model.pt", None, True),),
        )
        mock_pool_instance.imap_unordered.assert_called_once_with(
            _run_opt_worker,
            [
                (2, None, "deepmd", "./model.pt", None, False),
                (3, None, "deepmd", "./model.pt", None, False),
            ],
        )

        # 10. 验证：KeyboardInterrupt 处理逻辑被执行
        assert "Received KeyboardInterrupt, shutting down gracefully..." in output
        assert "Terminating multiprocessing pool..." in output
        assert "All child processes terminated." in output

        # 11. 验证：pool.terminate() 和 pool.join() 被调用
        mock_pool_instance.terminate.assert_called_once()
        mock_pool_instance.join.assert_called_once()


@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
def test_main_keyboard_interrupt_pool_none(
    mock_get_indexes,
    mock_get_context,
):
    """测试：当 get_context 抛出 KeyboardInterrupt 且 pool 为 None 时的处理"""
    # 1. 模拟有任务
    mock_get_indexes.return_value = [1, 2, 3]

    # 2. 关键：让 get_context 抛出 KeyboardInterrupt，pool 不会被创建
    mock_get_context.side_effect = KeyboardInterrupt("User interrupted during setup")

    # 3. 捕获 stdout
    captured_output = StringIO()
    with patch("sys.stdout", new=captured_output):
        # 4. 执行 main()，应该捕获并重新抛出 KeyboardInterrupt
        try:
            main(["--workers", "2"])
            assert False, "Expected KeyboardInterrupt to be raised"
        except KeyboardInterrupt:
            pass  # 预期的异常

        # 5. 获取打印输出
        output = captured_output.getvalue()

        # 6. 验证：get_context 被调用
        mock_get_context.assert_called_once_with("spawn")

        # 7. 验证：KeyboardInterrupt 处理逻辑被执行
        assert "Received KeyboardInterrupt, shutting down gracefully..." in output
        assert "All child processes terminated." in output

        # 8. 验证：由于 pool 为 None，不会打印 "Terminating multiprocessing pool..."
        assert "Terminating multiprocessing pool..." not in output


def test_main_entry_point(monkeypatch, tmp_path):
    """测试 if __name__=='__main__' 入口点"""
    import subprocess
    import sys

    # 创建一个临时的测试脚本，模拟运行 mlp_opt.py
    test_script = tmp_path / "test_mlp_opt_main.py"
    test_script.write_text("""
import sys
sys.path.insert(0, 'src')
from unittest.mock import patch, MagicMock

# 模拟所有依赖
sys.modules['torch'] = MagicMock()
sys.modules['torch'].load = MagicMock()
sys.modules['deepmd'] = MagicMock()
sys.modules['deepmd.calculator'] = MagicMock()
sys.modules['deepmd.calculator'].DP = MagicMock()

# 模拟 get_indexes 返回空列表，并把运行目录隔离到临时脚本所在位置
from ion_CSP import mlp_opt
mlp_opt.base_dir = __import__('os').path.dirname(__file__)
with patch('ion_CSP.mlp_opt.get_indexes', return_value=[]):
    mlp_opt.main()
""")

    result = subprocess.run(
        [sys.executable, str(test_script)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2])
    )

    # 验证脚本成功执行
    assert result.returncode == 0, f"Script failed with stderr: {result.stderr}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=ion_CSP.mlp_opt"])
