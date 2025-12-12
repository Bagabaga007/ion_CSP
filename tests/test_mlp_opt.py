import os
import sys
import pytest
import signal
import numpy as np
from unittest.mock import patch, MagicMock, ANY

# 在导入 mlp_opt 之前，深度模拟 torch.load 和 DP 类
sys.modules["torch"] = MagicMock()
sys.modules["torch"].load = MagicMock()
sys.modules["deepmd"] = MagicMock()
sys.modules["deepmd.calculator"] = MagicMock()
sys.modules["deepmd.calculator"].DP = MagicMock()

from ion_CSP.mlp_opt import (get_element_num, write_CONTCAR, write_OUTCAR,
                             get_indexes, run_opt, stop_handler, main)


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
    

# ==================== 测试 stop_handler 函数 ====================
@patch("ion_CSP.mlp_opt.print")
@patch("ion_CSP.mlp_opt.pool", new_callable=MagicMock)
def test_stop_handler_terminates_pool(mock_pool, mock_print):
    """测试 stop_handler 是否正确终止进程池并退出"""
    # 模拟收到 SIGINT 信号
    with pytest.raises(SystemExit) as exc_info:  # 捕获 sys.exit(0)
        stop_handler(signal.SIGINT, None)

    # 验证退出码是 0
    assert exc_info.value.code == 0

    # 验证是否打印了退出信息
    mock_print.assert_any_call("\nReceived signal 2 (Ctrl+C or SIGTERM), shutting down gracefully...")
    mock_print.assert_any_call("Terminating multiprocessing pool...")
    mock_print.assert_any_call("All child processes terminated. Exiting.")

    # 验证是否调用了 pool.terminate() 和 pool.join()
    mock_pool.terminate.assert_called_once()
    mock_pool.join.assert_called_once()


# ==================== 测试 main 函数 ====================
@patch("ion_CSP.mlp_opt.signal.signal")
@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
@patch("ion_CSP.mlp_opt.sys.exit")
def test_main_normal_exit(
    mock_exit,
    mock_get_indexes,
    mock_get_context,
    mock_signal,
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
    mock_signal.assert_any_call(signal.SIGINT, ANY)  # 注册了 SIGINT
    mock_signal.assert_any_call(signal.SIGTERM, ANY)  # 注册了 SIGTERM
    mock_get_context.assert_called_once_with("spawn")

    # 6. 验证 Pool 和 map 调用
    mock_context.Pool.assert_called_once_with(8)
    mock_pool_instance.map.assert_called_once_with(func=run_opt, iterable=[1, 2])
    mock_pool_instance.close.assert_called_once()
    mock_pool_instance.join.assert_called_once()
    mock_exit.assert_not_called()


@patch("ion_CSP.mlp_opt.signal.signal")
@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
@patch("ion_CSP.mlp_opt.stop_handler")
def test_main_keyboard_interrupt(
    mock_stop_handler,
    mock_get_indexes,
    mock_get_context,
    mock_signal,
    tmp_path,
    monkeypatch,
):
    """测试 main 函数在收到 Ctrl+C 时的行为"""
    # 1. 模拟 get_indexes 返回任务
    mock_get_indexes.return_value = [1]

    # 2. 模拟 multiprocessing.get_context 返回一个 mock 上下文
    mock_context = MagicMock()
    mock_get_context.return_value = mock_context

    # 3. 模拟 ctx.Pool(8) 返回一个 mock 实例
    mock_pool_instance = MagicMock()
    mock_context.Pool.return_value = mock_pool_instance
    mock_pool_instance.map.side_effect = KeyboardInterrupt()  # 模拟用户中断

    # 4. 调用 main
    main()

    # 5. 验证：main 中的 except KeyboardInterrupt 触发了 stop_handler
    mock_stop_handler.assert_called_once_with(signal.SIGINT, None)

    # 6. 验证：其他调用是否正常
    mock_signal.assert_any_call(signal.SIGINT, ANY)
    mock_signal.assert_any_call(signal.SIGTERM, ANY)
    mock_get_context.assert_called_once_with("spawn")
    mock_context.Pool.assert_called_once_with(8)
    mock_pool_instance.map.assert_called_once_with(func=run_opt, iterable=[1])
    # 注意：不需要验证 close/join，因为 map 抛异常后直接退出，不会执行 finally 中的 close/join


@patch("ion_CSP.mlp_opt.signal.signal")
@patch("multiprocessing.get_context")
@patch("ion_CSP.mlp_opt.get_indexes")
def test_main_no_files(
    mock_get_indexes,
    mock_get_context,
    mock_signal,
    tmp_path,
    monkeypatch,
):
    """测试 main 函数在没有任务文件时的退出行为"""
    # 1. 模拟没有任务文件
    mock_get_indexes.return_value = []  # ← 关键：没有文件！

    # 2. 调用 main
    main()

    # 3. 验证：初始化逻辑仍被调用
    mock_signal.assert_any_call(signal.SIGINT, ANY)
    mock_signal.assert_any_call(signal.SIGTERM, ANY)

    # 4. 验证进程池没有启动
    mock_get_context.assert_not_called()
    # 由于 get_context 没被调用，Pool 也不会被调用，无需额外验证


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=ion_CSP.mlp_opt"])
