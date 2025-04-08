import os
import yaml
import argparse
from ion_CSP.log_and_time import log_and_time
from ion_CSP.convert_SMILES import SmilesProcessing

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
    # 通过file_type参数来指定SMILES码所要转化的结构文件类型, 目前只包括gjf, xyz, mol
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The conversion from SMILES to .gjf files")
    parser.add_argument("work_dir", type=str, help="The working directory to run the script in")
    args = parser.parse_args()
    # 尝试读取配置文件
    try:
        with open(os.path.join(args.work_dir, "config.yaml"), "r") as file:
            config = yaml.safe_load(file)
    except FileNotFoundError:
        print(f"config.yaml not found in {args.work_dir}.")
    # 合并默认配置与读取的配置
    config["convert_SMILES"] = {
        **DEFAULT_CONFIG["convert_SMILES"],
        **config.get("convert_SMILES", {}),
    }
    # 获取当前脚本的名称
    script_name = os.path.basename(__file__)
    # 调用主函数
    main(script_name, args.work_dir, config)
