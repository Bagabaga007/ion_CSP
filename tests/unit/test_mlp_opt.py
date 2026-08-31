import os
import sys
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

from ion_CSP.mlp_opt import (get_mlp_calc, get_element_num, write_CONTCAR, write_OUTCAR,
                             get_indexes, run_opt, main)


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


# ==================== 测试 main 函数 ====================
@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
@patch("ion_CSP.mlp_opt.sys.exit")
def test_main_normal_exit(
    mock_exit,
    mock_get_indexes,
    mock_get_context,
    tmp_path,
    monkeypatch,
):
    """测试 main 函数在正常情况下（无中断）的完整流程"""
    # 1. 模拟 get_indexes 返回任务
    mock_get_indexes.return_value = [1, 2]

    # 2. 模拟 multiprocessing.get_context 返回一个 mock 上下文
    mock_context = MagicMock()
    mock_get_context.return_value = mock_context

    # 3. 在 mock 上下文中，mock .Pool 方法（这是关键！）
    mock_pool_instance = MagicMock()
    mock_context.Pool.return_value = mock_pool_instance
    mock_pool_instance.map.return_value = None  # 模拟 map 完成

    # 4. 调用 main
    main()

    # 5. 验证调用顺序
    mock_get_context.assert_called_once_with("spawn")

    # 6. 验证 Pool 和 map 调用
    mock_context.Pool.assert_called_once_with(8)
    mock_pool_instance.map.assert_called_once_with(func=run_opt, iterable=[1, 2])
    mock_pool_instance.close.assert_called_once()
    mock_pool_instance.join.assert_called_once()
    mock_exit.assert_not_called()


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
    """测试当 ctx.Pool 创建失败时，main() 降级为串行执行"""
    # 1. 模拟有任务
    mock_get_indexes.return_value = [1, 2]

    # 2. 模拟 get_context 返回一个 mock 上下文
    mock_context = MagicMock()
    mock_get_context.return_value = mock_context

    # 3. 关键：模拟 ctx.Pool 方法抛异常
    mock_context.Pool.side_effect = MemoryError("Failed to create process pool")

    # 4. 调用 main()
    main()

    # 5. 验证：ctx.Pool 被调用一次
    mock_context.Pool.assert_called_once_with(8)

    # 6. 验证：run_opt 被调用了 2 次（串行执行）
    assert mock_run_opt.call_count == 2
    mock_run_opt.assert_any_call(1)
    mock_run_opt.assert_any_call(2)


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
        main()

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
            main()

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

    # 4. 关键：让 pool.map 抛出 KeyboardInterrupt
    mock_pool_instance.map.side_effect = KeyboardInterrupt("User interrupted")

    # 5. 捕获 stdout
    captured_output = StringIO()
    with patch("sys.stdout", new=captured_output):
        # 6. 执行 main()，应该捕获并重新抛出 KeyboardInterrupt
        try:
            main()
            assert False, "Expected KeyboardInterrupt to be raised"
        except KeyboardInterrupt:
            pass  # 预期的异常

        # 7. 获取打印输出
        output = captured_output.getvalue()

        # 8. 验证：Pool 被创建
        mock_context.Pool.assert_called_once_with(8)

        # 9. 验证：pool.map 被调用
        mock_pool_instance.map.assert_called_once_with(func=run_opt, iterable=[1, 2, 3])

        # 10. 验证：KeyboardInterrupt 处理逻辑被执行
        assert "Received KeyboardInterrupt, shutting down gracefully..." in output
        assert "Terminating multiprocessing pool..." in output
        assert "All child processes terminated." in output

        # 11. 验证：pool.terminate() 和 pool.join() 被调用
        mock_pool_instance.terminate.assert_called_once()
        mock_pool_instance.join.assert_called()  # 可能被调用两次（except 和 finally）


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
            main()
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

# 模拟 get_indexes 返回空列表
with patch('ion_CSP.mlp_opt.get_indexes', return_value=[]):
    # 直接运行模块
    import runpy
    runpy.run_module('ion_CSP.mlp_opt', run_name='__main__')
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
