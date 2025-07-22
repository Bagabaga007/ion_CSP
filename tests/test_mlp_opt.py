import os
import sys
import pytest
import numpy as np
from unittest.mock import patch, MagicMock

# 在导入 mlp_opt 之前，深度模拟 torch.load 和 DP 类
sys.modules["torch"] = MagicMock()
sys.modules["torch"].load = MagicMock()
sys.modules["deepmd"] = MagicMock()
sys.modules["deepmd.calculator"] = MagicMock()
sys.modules["deepmd.calculator"].DP = MagicMock()

from ion_CSP.mlp_opt import (
    get_element_num,
    write_CONTCAR,
    write_OUTCAR,
    get_indexes,
    run_opt,
)


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


# 测试 get_element_num 函数
def test_get_element_num():
    elements = ["H", "O", "H", "O", "C"]
    unique_elements, element_count = get_element_num(elements)
    assert unique_elements == ["H", "O", "C"]
    assert element_count == {"H": 2, "O": 2, "C": 1}


# 测试 write_CONTCAR 函数
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


# 测试 write_OUTCAR 函数
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


# 测试 get_indexes 函数
def test_get_indexes(monkeypatch):
    # 模拟 POSCAR 文件
    test_files = ["POSCAR_1", "POSCAR_2", "POSCAR_10", "POSCAR_3"]
    monkeypatch.setattr("os.listdir", lambda _: test_files)

    indexes = get_indexes()
    assert indexes == [1, 2, 3, 10]


# 测试 run_opt 函数
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


# 确保不会在真实目录创建文件
@pytest.fixture(autouse=True, scope="session")
def prevent_file_creation_in_real_dir():
    # 在测试开始时备份真实目录中的文件列表
    real_dir = os.path.dirname(os.path.abspath(__file__))
    original_files = set(os.listdir(real_dir))

    yield

    # 在测试结束后检查是否有新文件创建
    current_files = set(os.listdir(real_dir))
    new_files = current_files - original_files

    # 删除测试期间创建的文件
    for filename in new_files:
        if filename.startswith(("CONTCAR_", "OUTCAR_", "POSCAR_")):
            try:
                os.remove(os.path.join(real_dir, filename))
                print(f"Deleted test file: {filename}")
            except Exception as e:
                print(f"Error deleting {filename}: {e}")


if __name__ == "__main__":
    pytest.main()
