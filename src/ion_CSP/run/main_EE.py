import logging
import csv
from pathlib import Path
from ion_CSP.convert_SMILES import SmilesProcessing
from ion_CSP.empirical_estimate import EmpiricalEstimation
from ion_CSP.log_and_time import StatusLogger
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
        # 中央离子库路径：命中的离子(规范化SMILES+电荷)直接复用其优化产物，跳过Gaussian。
        # 设为空字符串则禁用去重(向后兼容)。
        "database_dir": "",
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
}


def setup_ion_links(work_dir: str, config: dict):
    """
    自动创建到 Database_Ions 的软链接，避免复制离子文件。
    只链接 config["empirical_estimate"]["folders"] 中指定的 charge 文件夹。

    智能判断逻辑：
    - 如果目标文件夹已有非软链接文件（项目自己的离子），跳过链接
    - 如果目标文件夹不存在或为空，创建软链接到 Database_Ions
    """
    database_dir = config.get("convert_SMILES", {}).get("database_dir", "")
    if not database_dir:
        logging.info("database_dir not configured, skipping automatic ion linking")
        return

    database_path = Path(database_dir) / "3_For_CSP_module"
    if not database_path.exists():
        logging.warning(f"Database path not found: {database_path}, skipping ion linking")
        return

    folders = config.get("empirical_estimate", {}).get("folders", [])
    work_path = Path(work_dir)
    project_charge_folders = set()
    csv_name = config.get("convert_SMILES", {}).get("csv_file", "")
    csv_path = work_path / csv_name
    if csv_name and csv_path.is_file():
        try:
            with csv_path.open(newline="", encoding="utf-8") as csv_file:
                for row in csv.DictReader(csv_file):
                    project_charge_folders.add(f"charge_{int(row['Charge'])}")
        except (KeyError, TypeError, ValueError, OSError) as error:
            logging.warning(
                f"Unable to determine project charge folders from {csv_path}: {error}"
            )

    for folder in folders:
        target_folder = work_path / folder
        source_folder = database_path / folder

        if folder in project_charge_folders:
            logging.info(
                f"{folder} contains project input ions, skipping database linking"
            )
            continue

        if not source_folder.exists():
            logging.warning(f"Database folder not found: {source_folder}, skipping")
            continue

        # 智能判断：如果目标文件夹已存在且有非软链接的 gjf 文件，说明是项目自己的离子
        if target_folder.exists():
            gjf_files = list(target_folder.glob("*.gjf"))
            if gjf_files and any(not f.is_symlink() for f in gjf_files):
                logging.info(f"{folder} already has local ions ({len(gjf_files)} files), skipping linking")
                continue

        # 创建软链接
        target_folder.mkdir(parents=True, exist_ok=True)
        linked_count = 0
        for source_file in source_folder.glob("*"):
            if not source_file.is_file():
                continue
            target_file = target_folder / source_file.name
            if target_file.is_symlink() and not target_file.exists():
                target_file.unlink()
            if target_file.exists() or target_file.is_symlink():
                continue
            target_file.symlink_to(source_file.resolve())
            linked_count += 1

        if linked_count > 0:
            logging.info(f"✓ Linked {folder} from Database_Ions: {linked_count} files (symlink)")


@log_and_time
def main(work_dir, config):
    logging.info(f"Using config: {config}")

    tasks = {
        "0_convertion": lambda: convertion_task(work_dir, config),
        "0_estimation": lambda: estimation_task(work_dir, config),
        "0_update_combo": lambda: combination_task(work_dir, config),
    }
    for task_name, task_func in tasks.items():
        task_logger = StatusLogger(work_dir=work_dir, task_name=task_name)
        if not task_logger.is_successful():
            try:
                task_logger.set_running()
                task_func()
                task_logger.set_success()
            except Exception:
                task_logger.set_failure()
                raise
        if task_name == "0_convertion":
            # 转换后再链接，避免新项目的自有电荷目录被数据库离子预先占用。
            setup_ion_links(work_dir, config)

    if config["empirical_estimate"]["update"]:
        task_logger = StatusLogger(work_dir=work_dir, task_name="0_update_combo")
        try:
            task_logger.set_running()
            combination_task(work_dir, config)
            task_logger.set_success()
        except Exception:
            task_logger.set_failure()
            raise


def convertion_task(work_dir, config):
    # 给定与脚本同目录的csv文件名
    convertion = SmilesProcessing(
        work_dir=work_dir, csv_file=config["convert_SMILES"]["csv_file"]
    )
    # 根据电荷进行分组创建文件夹并将SMILES码转换为对应的结构文件
    convertion.charge_group()
    # 复用中央离子库中已优化的离子(规范化SMILES+电荷匹配)，避免重复 Gaussian 计算
    convertion.reuse_from_database(
        database_dir=config["convert_SMILES"].get("database_dir", "")
    )
    if config["convert_SMILES"]["screen"]:
        # 根据提供的官能团和电荷进行筛选, 在本数据集中硝基的SMILES码为[N+](=O)[O-]
        convertion.screen(
            charge_screen=config["convert_SMILES"]["charge_screen"],
            group_screen=config["convert_SMILES"]["group_screen"],
            group_name=config["convert_SMILES"]["group_name"],
            group_screen_invert=config["convert_SMILES"]["group_screen_invert"],
        )
    # 基于 dpdispatcher 模块，在远程CPU服务器上批量准备并提交Gaussian优化任务
    convertion.dpdisp_gaussian_tasks(
        # 注意，此处需要人为指定文件夹以避免浪费计算资源，默认通过empirical_estimate中的folders来确定
        folders=config["empirical_estimate"]["folders"],
        machine_path=config["convert_SMILES"]["machine"],
        resources_path=config["convert_SMILES"]["resources"],
        nodes=config["convert_SMILES"]["nodes"],
    )


def estimation_task(work_dir, config):
    # 在工作目录下准备 Gaussian 优化处理后具有 .gjf、.fchk 和 .log 文件的文件夹, 并提供对应的离子配比
    estimation = EmpiricalEstimation(
        work_dir=work_dir,
        folders=config["empirical_estimate"]["folders"],
        ratios=config["empirical_estimate"]["ratios"],
        sort_by=config["empirical_estimate"]["sort_by"],
    )
    # 对 .fchk 文件用 Multiwfn 进行静电势分析, 并将经验公式所需的分析结果保存到同名 JSON 文件中
    estimation.multiwfn_process_fchk_to_json()
    # 由于后续晶体生成不支持 .log 文件，需要将 Gaussian 优化得到的 .log 文件最后一帧转为 .gjf 结构文件
    estimation.gaussian_log_to_optimized_gjf()


def combination_task(work_dir, config):
    # 在工作目录下准备 Gaussian 优化处理后具有 .gjf、.fchk 和 .log 文件的文件夹, 并提供对应的离子配比
    combination = EmpiricalEstimation(
        work_dir=work_dir,
        folders=config["empirical_estimate"]["folders"],
        ratios=config["empirical_estimate"]["ratios"],
        sort_by=config["empirical_estimate"]["sort_by"],
    )
    # 如果依据密度排序，则需要经验公式根据配比生成离子晶体组合，读取 .json 文件并将静电势分析得到的各离子性质代入经验公式
    if config["empirical_estimate"]["sort_by"] == "density":
        # 最终将预测的离子晶体密度以及对应的组分输出到 .csv 文件并根据密度从大到小排序
        combination.empirical_estimate()
    # 如果依据氮含量排序，则调用另一套根据 .gjf 文件中化学分布信息
    elif config["empirical_estimate"]["sort_by"] == "nitrogen":
        # 最终将预测的离子晶体氮含量以及对应的组分输出到 .csv 文件并根据氮含量从大到小排序
        combination.nitrogen_content_estimate()
    elif config["empirical_estimate"]["sort_by"] == "NC_ratio":
        combination.carbon_nitrogen_ratio_estimate()
    # 基于排序依据 sort_by 对应的 .csv 文件创建 combo_n 文件夹，并复制相应的 .gjf 结构文件。
    if config["empirical_estimate"]["make_combo_dir"]:
        combination.make_combo_dir(
            target_dir=config["empirical_estimate"]["target_dir"],
            num_combos=config["empirical_estimate"]["num_combos"],
            ion_numbers=config["empirical_estimate"]["ion_numbers"],
        )


if __name__ == "__main__":
    # 获取工作目录和配置
    work_dir, config = get_work_dir_and_config()
    # 合并配置（假设有merge_config函数）
    modules = ["convert_SMILES", "empirical_estimate"]
    for module in modules:
        config[module] = merge_config(
            default_config=DEFAULT_CONFIG, user_config=config, key=module
        )
    # 调用主函数
    main(work_dir, config)
