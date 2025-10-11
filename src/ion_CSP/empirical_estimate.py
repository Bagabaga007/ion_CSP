import os
import re
import csv
import json
import yaml
import shutil
import logging
import itertools
import subprocess
from typing import List
from pathlib import Path

"""
Gaussian计算后把优化后的结构设为gjf文件准备再次优化:
Multiwfn载入优化任务的out/log文件, 然后输入gi, 再输入要保存的gjf文件名
此时里面的结构就是优化最后一帧的, 还避免了使用完全图形界面

首先对高斯计算产生的chk文件转化为fchk文件
具体命令为formchk x.chk
执行后就会发现计算文件夹中多了一个x.fchk文件
运行Multiwfn后依次输入
x.fchk //指定计算文件
12  //定量分子表面分析功能
0   //开始分析。默认的是分析静电势
示例输出：
       ================= Summary of surface analysis =================
 
 Volume:   504.45976 Bohr^3  (  74.75322 Angstrom^3)
 Estimated density according to mass and volume (M/V):    1.5557 g/cm^3
 Minimal value:   -127.53161 kcal/mol   Maximal value:   -114.64900 kcal/mol
 Overall surface area:         320.06186 Bohr^2  (  89.62645 Angstrom^2)
 Positive surface area:          0.00000 Bohr^2  (   0.00000 Angstrom^2)
 Negative surface area:        320.06186 Bohr^2  (  89.62645 Angstrom^2)
 Overall average value:   -0.19677551 a.u. (   -123.47860 kcal/mol)
 Positive average value:          NaN a.u. (          NaN kcal/mol)
 Negative average value:  -0.19677551 a.u. (   -123.47860 kcal/mol)
 Overall variance (sigma^2_tot):  0.00002851 a.u.^2 (    11.22495 (kcal/mol)^2)
 Positive variance:        0.00000000 a.u.^2 (      0.00000 (kcal/mol)^2)
 Negative variance:        0.00002851 a.u.^2 (     11.22495 (kcal/mol)^2)
 Balance of charges (nu):   0.00000000
 Product of sigma^2_tot and nu:   0.00000000 a.u.^2 (    0.00000 (kcal/mol)^2)
 Internal charge separation (Pi):   0.00453275 a.u. (      2.84434 kcal/mol)
 Molecular polarity index (MPI):   5.35453398 eV (    123.47860 kcal/mol)
 Nonpolar surface area (|ESP| <= 10 kcal/mol):      0.00 Angstrom^2  (  0.00 %)
 Polar surface area (|ESP| > 10 kcal/mol):         89.63 Angstrom^2  (100.00 %)
 Overall skewness:         0.7476810720
 Negative skewness:        0.7476810720
 
 Surface analysis finished!
 Total wall clock time passed during this task:     1 s
 Note: Previous orbital information has been restored
 Citation of molecular polarity index (MPI): Carbon, 171, 514 (2021) DOI: 10.1016/j.carbon.2020.09.048
"""


class EmpiricalEstimation:
    def __init__(
        self,
        work_dir: Path,
        folders: List[str],
        ratios: List[int],
        sort_by: str,
        optimized_dir: str = "1_2_Gaussian_optimized",
    ):
        """
        This class is designed to process Gaussian calculation files, perform electrostatic potential analysis using Multiwfn, and estimate the nitrogen content or density of ion crystal combinations. The class will also generate .csv files containing sorted nitrogen content or density based on the specified sorting criterion.

        :params
            work_dir: The working directory where the Gaussian calculation files are located.
            folders: A list of folder names containing the Gaussian calculation files.
            ratios: A list of integers representing the ratio of each folder in the combination.
            sort_by: A string indicating the sorting criterion, either 'density' or 'nitrogen'.
        """
        self.base_dir = work_dir.resolve()
        self.gaussian_result_dir = self.base_dir / optimized_dir
        # 确保所取的文件夹数与配比数是对应的
        if len(folders) != len(ratios):
            raise ValueError("The number of folders must match the number of ratios.")
        self.folders = folders
        self.ratios = ratios
        self.sort_by = sort_by
        if sort_by not in ("density", "nitrogen", "NC_ratio"):
            raise ValueError(
                f"The sort_by parameter must be either 'density' 'nitrogen' or 'NC_ratio', but got '{sort_by}'"
            )
        self.density_csv = "sorted_density.csv"
        self.nitrogen_csv = "sorted_nitrogen.csv"
        self.NC_ratio_csv = "specific_NC_ratio.csv"
        # 检查Multiwfn可执行文件是否存在
        self.multiwfn_path = self._check_multiwfn_executable()

    def _check_multiwfn_executable(self):
        """
        Private method:
        Check if the Multiwfn executable file exists in the system PATH.
        If not, raise a FileNotFoundError with an appropriate error message.
        """
        multiwfn_path = shutil.which("Multiwfn_noGUI") or shutil.which("Multiwfn")
        if not multiwfn_path:
            error_msg = (
                "Error: No detected Multiwfn executable file (Multiwfn or Multiwfn_GUI), please check:\n "
                "1. Has Multiwfn been installed correctly?\n"
                "2. Has Multiwfn been added to the system PATH environment variable"
            )
            print(error_msg)
            logging.error(error_msg)
            raise FileNotFoundError(
                "No detected Multiwfn executable file (Multiwfn or Multiwfn_GUI)"
            )
        else:
            print(f"Multiwfn executable found at: {multiwfn_path}")
            logging.info(f"Multiwfn executable found at: {multiwfn_path}")
        return multiwfn_path

    def _multiwfn_cmd_build(self, input_content, output_path : Path = None):
        """
        Private method:
        Build the Multiwfn command to be executed based on the input content.
        This method is used to create the input file for Multiwfn.

        :params
            input_content: The content to be written to the input file for Multiwfn.
            output_file: The name of the output file to redirect Multiwfn output. If None, output will not be redirected.
        """
        # 创建 input.txt 用于存储 Multiwfn 命令内容
        input_path = self.gaussian_result_dir / "input.txt"
        input_path.write_text(input_content, encoding="utf-8")
        try:
            if output_path:
                with output_path.open("w", encoding="utf-8") as f:
                    # 通过 input.txt 执行 Multiwfn 命令, 并将输出结果重定向到 output.txt 中
                    subprocess.run(
                        [self.multiwfn_path],
                        stdin=input_path.open("r"),
                        stdout=f,
                        check=True,
                    )
            else:
                subprocess.run(
                    [self.multiwfn_path], stdin=input_path.open("r"), check=True
                )

        except subprocess.CalledProcessError as e:
            logging.error(
                f"Error executing Multiwfn command with input {input_content}: {e}"
            )
            raise
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            raise
        finally:
            # 清理临时文件
            input_path.unlink(missing_ok=True)  # 删除 input.txt

    def multiwfn_process_fchk_to_json(self, specific_directory: str = None):
        """
        If a specific directory is given, this method can be used separately to implement batch processing of FCHK files with Multiwfn and save the desired electrostatic potential analysis results to the corresponding JSON file. Otherwise, the folder list provided during initialization will be processed sequentially.

        :params
            specific_directory: The specific directory to process. If None, all folders will be processed.
        """
        if specific_directory:
            self._multiwfn_process_fchk_to_json(specific_directory)
        else:
            for folder in self.folders:
                (self.gaussian_result_dir / "Optimized" / folder).mkdir(
                    parents=True, exist_ok=True
                )
                self._multiwfn_process_fchk_to_json(folder)

    def _multiwfn_process_fchk_to_json(self, folder: str):
        """
        Private method:
        Perform electrostatic potential analysis on .fchk files using Multiwfn and save the analysis results to a .json file.

        :params
            folder: The folder containing the .fchk files to be processed.
        """
        # 在每个文件夹中获取 .fchk 文件并根据文件名排序, 再用 Multiwfn 进行静电势分析, 最后将分析结果保存到同名 .json 文件中
        folder_path = Path(folder)
        fchk_files = list(folder_path.glob("*.fchk"))
        if not fchk_files:
            raise FileNotFoundError("No availible Gaussian .fchk file to process")
        fchk_files.sort()
        bad_files = []
        for fchk_file in fchk_files:
            base_name = fchk_file.stem
            json_file = folder_path / f"{base_name}.json"
            optimized_json_path = (
                self.gaussian_result_dir / "Optimized" / folder / f"{base_name}.json"
            )
            if json_file.exists():
                if optimized_json_path.exists():
                    logging.info(
                        f"{optimized_json_path} already exists, skipping copy to Optimized directory."
                    )
                else:
                    shutil.copy(src=json_file, dst=optimized_json_path)
            # 进行 fchk_to_json 的处理，并根据返回值记录处理失败的结果
            if not self._single_multiwfn_fchk_to_json(fchk_file):
                bad_files.append(base_name)

        # 如果有错误的 .fchk 文件, 则将其移动到 Bad 的对应的文件夹中
        if bad_files:
            logging.error(f"Bad Gaussian results for {bad_files}")
            bad_dir = self.gaussian_result_dir / "Bad" / folder
            bad_dir.mkdir(parents=True, exist_ok=True)
            # 文件扩展名列表
            for file_stem in bad_files:
                for suffix in ["gjf", "chk", "log", "fchk"]:
                    file_path = folder_path / f"{file_stem}.{suffix}"
                    bad_file_path = bad_dir / f"{file_stem}.{suffix}"
                    if file_path.exists():
                        shutil.move(src=str(file_path), dst=str(bad_file_path))
        logging.info(
            f"\nElectrostatic potential analysis by Multiwfn for {folder} folder has completed, and the results have been stored in the corresponding json files.\n"
        )

    def _single_multiwfn_fchk_to_json(self, fchk_filename: Path):
        """
        Private method:
        Use multiwfn to perform electrostatic potential analysis on each FCHK file separately, and save the required results to a corresponding JSON file.

        :params
            fchk_filename: The FCHK file to be processed.

        :return: True if the processing is successful, False if the FCHK file is invalid.
        """
        print(f"Multiwfn processing {fchk_filename}")
        logging.info(f"Multiwfn processing {fchk_filename}")
        output_path = self.gaussian_result_dir / "output.txt"
        self._multiwfn_cmd_build(
            input_content=f"{fchk_filename}\n12\n0\n-1\n-1\nq\n",
            output_path=output_path,
        )
        print(f"Finished processing {fchk_filename}")

        try:
            output_content = output_path.read_text()
        except Exception as e:
            logging.error(f"Error reading output.txt: {e}")
            raise
        # 提取所需数据
        volume_match = re.search(
            r"Volume:\s*([\d.]+)\s*Bohr\^3\s+\(\s*([\d.]+)\s*Angstrom\^3\)",
            output_content,
        )
        density_match = re.search(
            r"Estimated density according to mass and volume \(M/V\):\s*([\d.]+)\s*g/cm\^3",
            output_content,
        )
        volume = volume_match.group(2) if volume_match else None  # Angstrom^3
        density = density_match.group(1) if density_match else None  # g/cm^3

        overall_surface_area_match = re.search(
            r"Overall surface area:\s*([\d.]+)\s*Bohr\^2\s+\(\s*([\d.]+)\s*Angstrom\^2\)",
            output_content,
        )
        positive_surface_area_match = re.search(
            r"Positive surface area:\s*([\d.]+)\s*Bohr\^2\s+\(\s*([\d.]+)\s*Angstrom\^2\)",
            output_content,
        )
        negative_surface_area_match = re.search(
            r"Negative surface area:\s*([\d.]+)\s*Bohr\^2\s+\(\s*([\d.]+)\s*Angstrom\^2\)",
            output_content,
        )
        overall_surface_area = (
            overall_surface_area_match.group(2) if overall_surface_area_match else "NaN"
        )  # Angstrom^2
        positive_surface_area = (
            positive_surface_area_match.group(2)
            if positive_surface_area_match
            else "NaN"
        )  # Angstrom^2
        negative_surface_area = (
            negative_surface_area_match.group(2)
            if negative_surface_area_match
            else "NaN"
        )  # Angstrom^2

        overall_average_value_match = re.search(
            r"Overall average value:\s*[\d.-]*\s*a\.u\.\s*\(\s*([\d.-]+|NaN)\s*kcal/mol\)",
            output_content,
        )
        positive_average_value_match = re.search(
            r"Positive average value:\s*[\d.-]*\s*a\.u\.\s*\(\s*([\d.-]+|NaN)\s*kcal/mol\)",
            output_content,
        )
        negative_average_value_match = re.search(
            r"Negative average value:\s*[\d.-]*\s*a\.u\.\s*\(\s*([\d.-]+|NaN)\s*kcal/mol\)",
            output_content,
        )
        overall_average_value = (
            overall_average_value_match.group(1)
            if overall_average_value_match
            else "NaN"
        )
        positive_average_value = (
            positive_average_value_match.group(1)
            if positive_average_value_match
            else "NaN"
        )
        negative_average_value = (
            negative_average_value_match.group(1)
            if negative_average_value_match
            else "NaN"
        )

        # 判断阳离子或阴离子
        if (
            positive_surface_area == overall_surface_area
            and positive_average_value == overall_average_value
            and negative_surface_area == "0.00000"
            and negative_average_value == "NaN"
        ):
            ion_type = "cation"

        elif (
            negative_surface_area == overall_surface_area
            and negative_average_value == overall_average_value
            and positive_surface_area == "0.00000"
            and positive_average_value == "NaN"
        ):
            ion_type = "anion"
        else:
            ion_type = "mixed_ion"

        try:
            # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度 g/cm³
            molecular_mass = round(float(volume) * float(density) / 1.66054, 5)
        except TypeError as e:
            print(f"Bad .fchk file: {fchk_filename}: {e}")
            logging.error(f"Bad .fchk file: {fchk_filename}: {e}")
            return False

        # 获取目录以及 .fchk 文件的无后缀文件名, 即 refcode
        folder = fchk_filename.parent
        refcode = fchk_filename.stem

        result = {
            "refcode": refcode,
            "ion_type": ion_type,
            "molecular_mass": molecular_mass,
            "volume": volume,
            "density": density,
            "positive_surface_area": positive_surface_area,
            "positive_average_value": positive_average_value,
            "negative_surface_area": negative_surface_area,
            "negative_average_value": negative_average_value,
        }
        # 保存 JSON文件到当前目录
        json_path = folder / f"{refcode}.json"
        json_path.write_text(json.dumps(result, indent=4))
        optimized_path = (
            self.gaussian_result_dir / "Optimized" / folder.name / f"{refcode}.json"
        )
        shutil.copyfile(src=json_path, dst=optimized_path)
        logging.info(f"Finished processing {fchk_filename}")
        output_path.unlink(missing_ok=True)  # 删除临时输出文件
        return True

    def gaussian_log_to_optimized_gjf(self, specific_directory: str = None):
        """
        If a specific directory is given, this method can be used separately to batch process the last frame of Gaussian optimized LOG files into GJF files using Multiwfn.
        Otherwise, the folder list provided during initialization will be processed in order.

        :params
            specific_directory: The specific directory to process. If None, all folders will be processed.
        """
        if specific_directory:
            self._gaussian_log_to_optimized_gjf(specific_directory)
        else:
            for folder in self.folders:
                (self.gaussian_result_dir / "Optimized" / folder).mkdir(
                    parents=True, exist_ok=True
                )
                self._gaussian_log_to_optimized_gjf(folder)

    def _gaussian_log_to_optimized_gjf(self, folder: str):
        """
        Private method:
        Due to the lack of support of Pyxtal module for LOG files in subsequent crystal generation, it is necessary to convert the last frame of the Gaussian optimized LOG file to a .gjf file with Multiwfn processing.

        :params
            folder: The folder containing the Gaussian LOG files to be processed.
        """
        # 在每个文件夹中获取 .log 文件并根据文件名排序, 再用Multiwfn载入优化最后一帧转换为 gjf 文件
        folder_path = Path(folder)
        log_files = list(folder_path.glob("*.log"))
        if not log_files:
            raise FileNotFoundError(
                f"No availible Gaussian .log file to process in {folder}"
            )
        log_files.sort()
        for log_file in log_files:
            base_name = log_file.stem
            gjf_file = folder_path / f"{base_name}.gjf"
            optimized_gjf_path = (
                self.gaussian_result_dir / "Optimized" / folder / f"{base_name}.gjf"
            )
            if optimized_gjf_path.exists():
                logging.info(
                    f"{gjf_file} already exists, skipping multiwfn log_to_gjf processing."
                )
                continue
            self._single_multiwfn_log_to_gjf(folder, log_file)
        logging.info(
            f"\nThe .log to .gjf conversion by Multiwfn for {folder} folder has completed, and the optimized .gjf structures have been stored in the optimized directory.\n"
        )

    def _single_multiwfn_log_to_gjf(self, folder: str, log_filename: Path):
        """
        Private method:
        Use Multiwfn to convert the last frame of the Gaussian optimized LOG file to a .gjf file.

        :params
            folder: The folder containing the Gaussian LOG file to be processed.
            log_filename: The full path of the LOG file to be processed.
        """
        # 获取目录以及 .fchk 文件的无后缀文件名, 即 refcode
        refcode = log_filename.stem

        try:
            # Multiwfn首先载入优化任务的out/log文件, 然后输入gi, 再输入要保存的gjf文件名, 此时里面的结构就是优化最后一帧的, 还避免了使用完全图形界面
            self._multiwfn_cmd_build(
                input_content=f"{log_filename}\ngi\nOptimized/{folder}/{refcode}.gjf\nq\n"
            )
            gjf_path = (
                self.gaussian_result_dir / "Optimized" / folder / f"{refcode}.gjf"
            )
            if gjf_path.exists():
                print(f"Finished converting {log_filename} to {gjf_path}")
                logging.info(f"Finished converting {log_filename} to {gjf_path}")
            else:
                print(f"Error converting {log_filename} to {gjf_path}")
                logging.error(f"Error converting {log_filename} to {gjf_path}")
        except Exception as e:
            print(f"Error with processing {log_filename}: {e}")
            logging.error(f"Error with processing {log_filename}: {e}")

    def _read_gjf_elements(self, gjf_file: Path):
        """
        Private method:
        Read the elements from a .gjf file and return a dictionary with element counts.

        :params
            gjf_file: The full path of the .gjf file to be processed.

        :return: A dictionary with element symbols as keys and their counts as values.
        """
        # 根据每一个组合中的组分找到对应的 JSON 文件并读取其中的性质内容
        with gjf_file.open("r") as file:
            lines = file.readlines()
        atomic_counts = {}
        # 找到原子信息的开始行
        start_reading = False
        for line in lines:
            line = line.strip()
            # 跳过注释和空行
            if line.startswith("%") or line.startswith("#") or not line:
                continue
            # 检测只包含两个数字的行
            parts = line.split()
            if (
                len(parts) == 2
                and parts[0].lstrip("-").isdigit()
                and parts[1].isdigit()
            ):
                start_reading = True
                continue
            if start_reading:
                element = parts[0]  # 第一个部分是元素符号
                # 更新元素计数
                if element in atomic_counts:
                    atomic_counts[element] += 1
                else:
                    atomic_counts[element] = 1
        return atomic_counts

    def _generate_combinations(self, suffix: str):
        """
        Private method:
        Generate all valid combinations of files based on the specified suffix and ratios.

        :params
            suffix: The file suffix to filter the files in the folders.

        :return: A list of dictionaries representing the combinations of files with their respective ratios.
        """
        # 获取所有符合后缀名条件的文件
        all_files = []
        for folder in self.folders:
            folder_path = self.gaussian_result_dir / "Optimized" / folder
            suffix_files = list(folder_path.glob(f"*{suffix}"))
            suffix_files.sort()
            print(f"Valid {suffix} file number in {folder}: {len(suffix_files)}")
            logging.info(f"Valid {suffix} file number in {folder}: {len(suffix_files)}")
            if not suffix_files:
                raise FileNotFoundError(
                    f"No available {suffix} files in {folder} folder"
                )
            all_files.append(suffix_files)

        # 对所有文件根据其文件夹与配比进行组合
        combinations = []
        for folder_files in itertools.product(*all_files):
            # 根据给定的配比生成字典形式的组合
            ratio_combination = {}
            for folder_index, count in enumerate(self.ratios):
                ratio_combination.update({folder_files[folder_index]: count})
            combinations.append(ratio_combination)
        print(f"Valid combination number: {len(combinations)}")
        logging.info(f"Valid combination number: {len(combinations)}")
        return combinations

    def nitrogen_content_estimate(self):
        """
        Evaluate the priority of ion crystal combinations based on nitrogen content and generate .csv files
        """
        atomic_masses = {"H": 1.008, "C": 12.01, "N": 14.01, "O": 16.00}
        # 获取所有 .gjf 文件
        combinations = self._generate_combinations(suffix=".gjf")
        nitrogen_contents = []
        for combo in combinations:
            total_masses = 0.0
            nitrogen_masses = 0.0
            for gjf_file, ion_count in combo.items():
                atomic_counts = self._read_gjf_elements(Path(gjf_file))
                for element, atom_count in atomic_counts.items():
                    if element in atomic_masses:
                        total_masses += atomic_masses[element] * atom_count * ion_count
                        if element == "N":
                            nitrogen_masses += (
                                atomic_masses[element] * atom_count * ion_count
                            )
                    else:
                        raise "Contains element information not included, unable to calculate nitrogen content"
            nitrogen_content = (
                round((nitrogen_masses / total_masses), 4) if total_masses > 0 else 0
            )
            nitrogen_contents.append(nitrogen_content)
        # 将组合和对应的氮含量合并并排序
        data = []
        for combo, nitrogen in zip(combinations, nitrogen_contents):
            # 转换为需要的格式: "folder_name/file_name"
            cleaned_combo = [
                f"{component.parent.name}/{component.stem}" for component in combo
            ]
            # 将组合和氮含量合并成一行
            data.append(cleaned_combo + [nitrogen])
        # 根据氮含量列进行排序（氮含量在最后一列）
        data.sort(key=lambda x: float(x[-1]), reverse=True)

        # 写入排序后的 .csv 文件
        with open(self.nitrogen_csv, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            # 动态生成表头
            num_components = len(combinations[0]) if combinations else 0
            header = [f"Component {i + 1}" for i in range(num_components)] + [
                "Nitrogen_Content"
            ]
            writer.writerow(header)  # 写入表头
            writer.writerows(data)  # 写入排序后的数

    def carbon_nitrogen_ratio_estimate(self):
        """
        Evaluate the priority of ion crystal combinations based on carbon and nitrogen ratio
        (C:N < 1:8) and sort by oxygen content, then generate .csv files.
        """
        atomic_masses = {"H": 1.008, "C": 12.01, "N": 14.01, "O": 16.00}
        # 获取所有 .gjf 文件
        combinations = self._generate_combinations(suffix=".gjf")
        filtered_data = []

        for combo in combinations:
            total_atoms = 0
            carbon_atoms = 0
            nitrogen_atoms = 0
            oxygen_atoms = 0

            for gjf_file, ion_count in combo.items():
                atomic_counts = self._read_gjf_elements(Path(gjf_file))
                for element, atom_count in atomic_counts.items():
                    if element in atomic_masses:
                        total_atoms += atom_count * ion_count
                        if element == "C":
                            carbon_atoms += atom_count * ion_count
                        elif element == "N":
                            nitrogen_atoms += atom_count * ion_count
                        elif element == "O":
                            oxygen_atoms += atom_count * ion_count
                    else:
                        raise ValueError(
                            "Contains element information not included, unable to calculate ratios"
                        )

            # 计算 C:N 比率
            if carbon_atoms != 0:  # 确保氮的质量大于 0，避免除以零
                nitrogen_carbon_ratio = round(nitrogen_atoms / carbon_atoms, 2)
            else:
                nitrogen_carbon_ratio = 100.0
            filtered_data.append((combo, nitrogen_carbon_ratio, oxygen_atoms))

        # 根据氧含量排序
        filtered_data.sort(key=lambda x: (-x[1], -x[2]))

        # 写入排序后的 .csv 文件
        with open(self.NC_ratio_csv, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            # 动态生成表头
            num_components = len(combinations[0]) if combinations else 0
            header = [f"Component {i + 1}" for i in range(num_components)] + [
                "N_C_Ratio",
                "O_Atoms",
            ]
            writer.writerow(header)  # 写入表头

            # 写入筛选后的组合和氧含量
            for combo, nitrogen_carbon_ratio, oxygen_content in filtered_data:
                # 转换为需要的格式: "folder_name/file_name"
                cleaned_combo = [
                    f"{component.parent.name}/{component.stem}" for component in combo
                ]
                writer.writerow(
                    cleaned_combo + [nitrogen_carbon_ratio, oxygen_content]
                )  # 写入每一行

    def empirical_estimate(self):
        """
        Based on the electrostatic analysis obtained from the .json file, calculate the initial screening density of the ion crystal using empirical formulas, and generate the .csv file according to the sorted density.
        """
        combinations = self._generate_combinations(suffix=".json")
        predicted_crystal_densities = []
        for combo in combinations:
            # 每个组合包含数个离子，分别获取其各项性质，包括质量、体积、密度、正/负电势与面积
            refcodes, ion_types, masses, volumes = [], [], 0, 0
            (
                positive_surface_areas,
                positive_average_values,
                positive_electrostatics,
                negative_surface_areas,
                negative_average_values,
                negative_electrostatics,
            ) = 0, 0, 0, 0, 0, 0
            for json_file, count in combo.items():
                # 根据每一个组合中的组分找到对应的 JSON 文件并读取其中的性质内容
                try:
                    with open(json_file, "r") as json_file:
                        property = json.load(json_file)
                except json.decoder.JSONDecodeError:
                    continue
                refcodes.append(property["refcode"])
                ion_types.append(property["ion_type"])
                # 1.66054 这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度 g/cm³
                mass = property["molecular_mass"] * 1.66054
                masses += mass * count
                molecular_volume = float(property["volume"])
                volumes += molecular_volume * count
                positive_surface_area = property["positive_surface_area"]
                negative_surface_area = property["negative_surface_area"]
                positive_average_value = property["positive_average_value"]
                negative_average_value = property["negative_average_value"]
                if (
                    positive_surface_area != "0.00000"
                    and positive_average_value != "NaN"
                ):
                    positive_surface_areas += float(positive_surface_area) * count
                    positive_average_values += float(positive_average_value) * count
                    positive_electrostatic = float(positive_average_value) / float(
                        positive_surface_area
                    )
                    positive_electrostatics += positive_electrostatic * count
                if (
                    negative_surface_area != "0.00000"
                    and negative_average_value != "NaN"
                ):
                    negative_surface_areas += float(negative_surface_area) * count
                    negative_average_values += float(negative_average_value) * count
                    negative_electrostatic = float(negative_average_value) / float(
                        negative_surface_area
                    )
                    negative_electrostatics += negative_electrostatic * count

            # 1. 拟合经验公式参数来源：Molecular Physics 2010, 108:10, 1391-1396.
            # http://dx.doi.org/10.1080/00268971003702221
            # alpha, beta, gamma, delta = 1.0260, 0.0514, 0.0419, 0.0227
            # 2. 拟合经验公式参数来源：Journal of Computational Chemistry 2013, 34, 2146–2151.
            # https://doi.org/10.1002/jcc.23369
            alpha, beta, gamma, delta = 1.1145, 0.02056, -0.0392, -0.1683

            M_d_Vm = masses / volumes
            predicted_crystal_density = (
                (alpha * M_d_Vm)
                + (beta * positive_electrostatics)
                + (gamma * negative_electrostatics)
                + (delta)
            )
            predicted_crystal_density = round(predicted_crystal_density, 4)
            predicted_crystal_densities.append(predicted_crystal_density)

        # 将组合和对应的密度合并并排序
        data = []
        for combo, density in zip(combinations, predicted_crystal_densities):
            # 转换为需要的格式: "folder_name/file_name"
            cleaned_combo = [
                f"{component.parent.name}/{component.stem}" for component in combo
            ]
            # 将组合和密度合并成一行
            data.append(cleaned_combo + [density])
        # 根据密度列进行排序（密度在最后一列）
        data.sort(key=lambda x: float(x[-1]), reverse=True)

        # 写入排序后的 .csv 文件
        with open(self.density_csv, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            # 动态生成表头
            num_components = len(combinations[0]) if combinations else 0
            header = [f"Component {i + 1}" for i in range(num_components)] + [
                "Pred_Density"
            ]
            writer.writerow(header)  # 写入表头
            writer.writerows(data)  # 写入排序后的数

    def _copy_combo_file(self, combo_path: Path, folder_basename: str, file_type: str):
        """
        Private method:
        Copy the specified file type from the Optimized directory to the combo_n folder.

        :params
            combo_path: The path to the combo_n folder where the file will be copied.
            folder_basename: The basename of the folder containing the file to be copied.
            file_type: The type of file to be copied (e.g., '.gjf', '.json').
        """
        parts = folder_basename.split("/")
        if len(parts) < 2:
            logging.error(
                f"Invalid folder_basename format: {folder_basename}. Expected format: 'charge_2/ABCDEF'."
            )
            return
        folder_name, file_base = parts
        # 使用 pathlib 构建路径
        source_path = (
            self.gaussian_result_dir
            / "Optimized"
            / folder_name
            / f"{file_base}{file_type}"
        )
        if not source_path.exists():
            logging.error(f"Source file {source_path} does not exist.")
            return
        target_path = Path(combo_path) / f"{file_base}{file_type}"
        if target_path.exists():
            logging.info(
                f"{file_base}{file_type} of {os.path.basename(combo_path)} already exists in {os.path.abspath(combo_path)}. Skipping copy."
            )
            return

        # 复制指定后缀名文件到对应的 combo_n 文件夹
        shutil.copy(source_path, target_path)
        logging.info(f"Copied {source_path.name} to {combo_path}")

    def make_combo_dir(self, target_dir: Path, num_combos: int, ion_numbers: List[int]):
        """
        Create a combo_n folder based on the .csv file and copy the corresponding .gjf structure file.

        :params
            target_directory: The target directory of the combo folder to be created
            num_folders: The number of combo folders to be created
            ion_numbers: The number of ions for ionic crystal generation step (generated in config.yaml in the corresponding combo_dir automatically)
        """
        # 根据sort_by选择CSV文件
        csv_file = {
            "density": self.density_csv,
            "nitrogen": self.nitrogen_csv,
            "NC_ratio": self.NC_ratio_csv,
        }.get(self.sort_by, self.density_csv)
        csv_path = self.gaussian_result_dir / csv_file
        if not csv_path.exists():
            raise FileNotFoundError(
                f"CSV file {csv_file} does not exist in the Gaussian optimized directory."
            )
        target_path = (
            target_dir if target_dir else self.base_dir / f"2_{self.sort_by}_combos"
        )
        target_path.mkdir(parents=True, exist_ok=True)

        with csv_path.open(mode="r", newline="") as file:
            reader = csv.DictReader(file)
            # 初始化已处理的文件夹计数
            for index, row in enumerate(reader):
                if index >= num_combos:
                    break  # 达到指定文件夹数量，停止处理
                # 创建 combo_n 文件夹名称
                combo_folder = target_path / f"combo_{index + 1}"
                combo_folder.mkdir(exist_ok=True)

                # 遍历每一列（组分）并复制对应的文件
                gjf_names = []
                for key in [k for k in row.keys() if k.startswith("Component")]:
                    if not row[key]:
                        raise ValueError(
                            f"Missing value in CSV file {csv_file} at row {index + 1}, column {key}."
                        )
                    folder_basename = row[key]
                    self._copy_combo_file(
                        combo_folder, folder_basename, file_type=".gjf"
                    )
                    self._copy_combo_file(
                        combo_folder, folder_basename, file_type=".json"
                    )
                    # gjf_names存放的是不包含目录名，且带 .gjf 后缀名的文件名，用于写入config.yaml
                    gjf_names.append(f"{folder_basename.split('/')[1]}.gjf")

                # 创建配置文件 config.yaml
                config_path = self.base_dir / "config.yaml"
                if not config_path.exists():
                    logging.warning(
                        f"No available config.yaml file in parent directory: {self.base_dir} \n"
                    )
                    logging.info(
                        f"Trying to load config.yaml file from optimized directory: {self.gaussian_result_dir}"
                    )
                    config_path = self.gaussian_result_dir / "config.yaml"
                if config_path.exists():
                    try:
                        config = yaml.safe_load(config_path.read_text())
                    except yaml.YAMLError as e:
                        logging.error(f"YAML configuration file parsing failed: {e}")
                else:
                    logging.error(
                        f"No available config.yaml file either in parent directory: {self.base_dir} and optimized directory {self.gaussian_result_dir} \n"
                    )
                    raise FileNotFoundError(
                        f"No available config.yaml file in either parent directory: {self.base_dir} or optimized directory {self.gaussian_result_dir}"
                    )

                try:
                    # 更新 combo 文件夹中对应的离子名称与数量配置
                    config["gen_opt"]["species"] = gjf_names
                    config["gen_opt"]["ion_numbers"] = ion_numbers
                    logging.info(
                        f"Generated 'species' and 'ion_numbers' config for gen_opt module in config.yaml are respectively: {config['gen_opt']['species']} and {config['gen_opt']['ion_numbers']}"
                    )
                    (combo_folder / "config.yaml").write_text(
                        yaml.dump(config), encoding="utf-8"
                    )
                except Exception as e:
                    logging.error(f"Unexpected error: {e}")
