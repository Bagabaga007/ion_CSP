from ion_CSP.empirical_estimate import EmpiricalEstimation
from ion_CSP.log_and_time import log_and_time, merge_config, get_work_dir_and_config

# 默认配置
DEFAULT_CONFIG = {
    "empirical_estimate": {
        "folders": [],  # 默认文件夹列表
        "ratios": [],  # 默认离子配比
        "sort_by": "density",  # 默认排序方式
        "make_combo_dir": True,  # 默认不创建组合目录
        "target_dir": "",  # 默认目标目录
        "num_combos": 100,  # 默认组合数量
        "ion_numbers": [],  # 默认离子数量
    }
}

@log_and_time
def main(work_dir, config):
    # 在工作目录下准备 Gaussian 优化处理后具有 .gjf、.fchk 和 .log 文件的文件夹, 并提供对应的离子配比
    result = EmpiricalEstimation(
        work_dir=work_dir,
        folders=config["empirical_estimate"]["folders"],
        ratios=config["empirical_estimate"]["ratios"],
        sort_by=config["empirical_estimate"]["sort_by"],
    )
    # 对 .fchk 文件用 Multiwfn 进行静电势分析, 并将经验公式所需的分析结果保存到同名 JSON 文件中
    result.multiwfn_process_fchk_to_json()
    # 由于后续晶体生成不支持 .log 文件，需要将 Gaussian 优化得到的 .log 文件最后一帧转为 .gjf 结构文件
    result.gaussian_log_to_optimized_gjf()  
    # 如果依据密度排序，则需要经验公式根据配比生成离子晶体组合，读取 .json 文件并将静电势分析得到的各离子性质代入经验公式
    if config["empirical_estimate"]["sort_by"] == 'density':
        # 最终将预测的离子晶体密度以及对应的组分输出到 .csv 文件并根据密度从大到小排序
        result.empirical_estimate()
    # 如果依据氮含量排序，则调用另一套根据 .gjf 文件中化学分布信息
    elif config["empirical_estimate"]["sort_by"] == 'nitrogen':
        # 最终将预测的离子晶体氮含量以及对应的组分输出到 .csv 文件并根据氮含量从大到小排序
        result.nitrogen_content_estimate()
    # 基于排序依据 sort_by 对应的 .csv 文件创建 combo_n 文件夹，并复制相应的 .gjf 结构文件。
    if config["empirical_estimate"]["make_combo_dir"]:
        result.make_combo_dir(
            target_dir=config["empirical_estimate"]["target_dir"],
            num_combos=config["empirical_estimate"]["num_combos"],
            ion_numbers=config["empirical_estimate"]["ion_numbers"],
        )


if __name__ == "__main__":
    # 获取工作目录和配置
    work_dir, config = get_work_dir_and_config()
    # 合并配置（假设有merge_config函数）
    config["empirical_estimate"] = merge_config(
        default_config=DEFAULT_CONFIG, user_config=config, key="empirical_estimate"
    )
    # 调用主函数
    main(work_dir, config)
