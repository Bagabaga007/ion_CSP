import os
import logging
import pytest
from ase import Atoms
from ase.build import molecule
from ion_CSP.identify_molecules import identify_molecules, molecules_information

# 配置测试日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# 测试夹具
@pytest.fixture
def water_molecule():
    """创建水分子测试用例"""
    return molecule("H2O")


@pytest.fixture
def methane_molecule():
    """创建甲烷分子测试用例"""
    return molecule("CH4")


@pytest.fixture
def sample_gjf(tmp_path):
    """生成测试用的.gjf文件"""
    gjf_content = """
    %chk=test.chk
    #p test
    H2O
    """
    gjf_file = tmp_path / "test.gjf"
    gjf_file.write_text(gjf_content)
    return gjf_file


def test_molecule_identification(water_molecule, methane_molecule, tmp_path):
    """测试分子识别功能"""
    # 创建测试结构
    test_system = water_molecule + methane_molecule

    # 生成测试用的.gjf文件
    gjf_file = tmp_path / "test.gjf"
    gjf_file.write_text("H2O O 1 0 0 0 0 0")  # 简化的测试内容

    # 修改当前工作目录
    original_dir = os.getcwd()
    os.chdir(tmp_path)

    try:
        # 执行分子识别
        merged, flag, initial = identify_molecules(test_system)

        # 验证识别结果
        assert len(merged) == 2
        assert ("H2", 2) in merged[0].values()
        assert ("C", 1) in merged[1].values()

        # 验证初始分子匹配
        expected_initial = [{"H": 2, "O": 1}]  # 根据.gjf内容调整
        assert initial == expected_initial
        assert flag is True

    finally:
        # 恢复原始工作目录
        os.chdir(original_dir)


def test_log_output(caplog, water_molecule):
    """测试日志输出格式"""
    test_system = water_molecule
    merged, _, _ = identify_molecules(test_system)

    with caplog.at_level(logging.INFO):
        molecules_information(merged, True, [{"H": 2, "O": 1}])

        # 验证日志内容
        assert "Molecule 1 (Total Atoms: 3, Count: 1): H2O" in caplog.text
        assert "Molecular Comparison Successful" in caplog.text


def test_empty_input():
    """测试空输入处理"""
    with pytest.raises(ValueError, match="atoms参数不能为空"):
        identify_molecules(Atoms())


def test_invalid_input():
    """测试无效输入类型"""
    with pytest.raises(TypeError, match="atoms参数必须是ASE Atoms对象"):
        identify_molecules("invalid_input")


def test_molecule_ordering(caplog):
    """测试元素排序功能"""
    test_mol = {"O": 1, "H": 2, "C": 1}
    ordered = molecules_information([test_mol], True, [{"C": 1, "H": 2, "O": 1}])

    # 验证元素顺序
    output = caplog.records[-1].msg
    assert output.startswith("Molecule 1 (Total Atoms: 4, Count: 1): CH2O")


def test_molecule_count(caplog):
    """测试分子计数功能"""
    test_system = molecule("H2O") * 3
    merged, _, _ = identify_molecules(test_system)

    with caplog.at_level(logging.INFO):
        molecules_information(merged, True, [{"H": 2, "O": 1}])
        assert "Count: 3" in caplog.text


if __name__ == "__main__":
    pytest.main(["-v", "--capture=no"])
