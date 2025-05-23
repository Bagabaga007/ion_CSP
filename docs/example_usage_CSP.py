import os
from src.main_CSP import main as main_CSP
from ion_CSP.log_and_time import merge_config, get_work_dir_and_config

# 默认配置
DEFAULT_CONFIG = {
    "convert_SMILES": {
        "csv_file": "",  # 默认CSV文件名
        "screen": False,  # 默认不进行筛选
        "charge_screen": "",  # 默认电荷筛选为空
        "group_screen": "",  # 默认官能团筛选为空
        "group_name": "",  # 默认分组名称
        "group_screen_invert": False,  # 默认不进行反向筛选
        "machine": "/your/cpu/machine/config/path",  # 进行Gaussian优化计算的机器参数
        "resources": "/your/cpu/resources/config/path",  # 进行Gaussian优化计算的资源参数
    },
    "empirical_estimate": {
        "folders": [],  # 默认文件夹列表
        "ratios": [],  # 默认离子配比
        "sort_by": "density",  # 默认排序方式
        "make_combo_dir": True,  # 默认不创建组合目录
        "target_dir": "",  # 默认目标目录
        "num_combos": 100,  # 默认组合数量
        "ion_numbers": [],  # 默认离子数量
        "update": True,  # 默认每次运行都会更新组合文件夹
    },
    "gen_opt": {
        "num_per_group": 500,  # 每个空间群要生成的晶体结构数量
        "space_groups_limit": 75,  # 空间群搜索的限制
        "machine": "/your/gpu/machine/config/path",  # 进行机器学习势优化计算的机器参数，建议GPU
        "resources": "/your/gpu/resources/config/path",  # 进行机器学习势优化计算的资源参数
        "nodes": 1,  # 占用GPU节点数
    },
    "read_mlp_density": {
        "n_screen": 10,  # 筛选机器学习势优化后密度最大的n个CONTCAR与对应的OUTCAR
        "molecules_screen": True,  # 是否排除离子改变的晶体结构
        "detail_log": False,  # 是否额外生成详细的筛选日志文件
    },
    "vasp_processing": {
        "machine": "/your/cpu/machine/config/path",  # 进行VASP分步优化计算的机器参数
        "resources": "/your/cpu/resources/config/path",  # 进行VASP分步优化计算的资源参数
        "nodes": 2,  # 占用CPU节点数
        "molecules_prior": True,  # 是否检查离子晶体结构中所有离子的结构
    },
}


if __name__ == "__main__":    
    # 获取工作目录和配置
    work_dir, config = get_work_dir_and_config()
    # 合并配置
    modules = ["gen_opt", "read_mlp_density", "vasp_processing"]
    for module in modules:
        config[module] = merge_config(
            default_config=DEFAULT_CONFIG, user_config=config, key=module
        )
    # 调用主函数
    main_CSP(os.path.basename(__file__), work_dir, config)
