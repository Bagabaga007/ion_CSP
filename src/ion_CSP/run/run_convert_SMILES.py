from ion_CSP.convert_SMILES import SmilesProcessing
from ion_CSP.log_and_time import log_and_time, merge_config, get_work_dir_and_config

# 默认配置
DEFAULT_CONFIG = {
    "convert_SMILES": {
        "csv_file": "",  # 默认CSV文件名
        "screen": False,  # 默认不进行筛选
        "charge_screen": "",  # 默认电荷筛选为空
        "group_screen": "",  # 默认官能团筛选为空
        "group_name": "",  # 默认分组名称
        "group_screen_invert": False,  # 默认不进行反向筛选
    }
}

@log_and_time
def main(work_dir, config):
    # 给定与脚本同目录的csv文件名
    result = SmilesProcessing(
        work_dir=work_dir,
        csv_file=config["convert_SMILES"]["csv_file"]
    )
    # 根据电荷进行分组创建文件夹并将SMILES码转换为对应的结构文件
    result.charge_group()
    if config["convert_SMILES"]["screen"]:
        # 根据提供的官能团和电荷进行筛选, 在本数据集中硝基的SMILES码为[N+](=O)[O-]
        result.screen(
            charge_screen=config["convert_SMILES"]["charge_screen"],
            group_screen=config["convert_SMILES"]["group_screen"],
            group_name=config["convert_SMILES"]["group_name"],
            group_screen_invert=config["convert_SMILES"]["group_screen_invert"],
        )
    result.dpdisp_gaussian_tasks(
        # 注意，此处需要人为指定文件夹以避免浪费计算资源，默认通过empirical_estimate中的folders来确定
        folders=config["empirical_estimate"]["folders"],
        machine=config["convert_SMILES"]["machine"],
        resources=config["convert_SMILES"]["resources"],
        nodes=config["convert_SMILES"]["nodes"],
    )

if __name__ == "__main__":
    # 获取工作目录和配置
    work_dir, config = get_work_dir_and_config()
    # 合并配置（假设有merge_config函数）
    config["convert_SMILES"] = merge_config(
        default_config=DEFAULT_CONFIG, user_config=config, key="convert_SMILES"
    )
    # 调用主函数
    main(work_dir, config)
