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
        work_dir: str,
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
        self.base_dir = work_dir
        self.gaussian_optimized_dir = os.path.join(self.base_dir, optimized_dir)
        os.chdir(self.gaussian_optimized_dir)
        # 确保所取的文件夹数与配比数是对应的
        if len(folders) != len(ratios):
            raise ValueError('The number of folders must match the number of ratios.')
        self.folders = folders
        self.ratios = ratios
        self.sort_by = sort_by
        if sort_by not in ("density", "nitrogen", "NC_ratio"):
            raise ValueError(f"The sort_by parameter must be either 'density' 'nitrogen' or 'NC_ratio', but got '{sort_by}'")
        self.density_csv = "sorted_density.csv"
        self.nitrogen_csv = "sorted_nitrogen.csv"
        self.NC_ratio_csv = "specific_NC_ratio.csv"
        # 检查Multiwfn可执行文件是否存在
        self.multiwfn_path = self._check_multiwfn_executable()
    
    def _check_multiwfn_executable(self):
        '''
        Private method:
        Check if the Multiwfn executable file exists in the system PATH.
        If not, raise a FileNotFoundError with an appropriate error message.
        '''
        multiwfn_path = shutil.which("Multiwfn_noGUI") or shutil.which("Multiwfn")
        if not multiwfn_path:
            error_msg = (
                "Error: No detected Multiwfn executable file (Multiwfn or Multiwfn_GUI), please check:\n "
                "1. Has Multiwfn been installed correctly?\n"
                "2. Has Multiwfn been added to the system PATH environment variable"
            )
            print(error_msg)
            logging.error(error_msg)
            raise FileNotFoundError("No detected Multiwfn executable file (Multiwfn or Multiwfn_GUI)")
        else:
            print(f"Multiwfn executable found at: {multiwfn_path}")
            logging.info(f"Multiwfn executable found at: {multiwfn_path}")
        return multiwfn_path

    def _multiwfn_cmd_build(self, input_content, output_file=None):
        '''
        Private method:
        Build the Multiwfn command to be executed based on the input content.
        This method is used to create the input file for Multiwfn.

        :params
            input_content: The content to be written to the input file for Multiwfn.
        '''
        # 创建 input.txt 用于存储 Multiwfn 命令内容
        with open('input.txt', 'w') as input_file:
            input_file.write(input_content)
        if output_file:
            with open('output.txt', 'w') as output_file, open('input.txt', 'r') as input_file:
                try:
                    # 通过 input.txt 执行 Multiwfn 命令, 并将输出结果重定向到 output.txt 中
                    subprocess.run([self.multiwfn_path], stdin=input_file, stdout=output_file, check=True)
                except subprocess.CalledProcessError as e:
                    logging.error(
                        f"Error executing Multiwfn command with input {input_content}: {e}"
                    )
                except Exception as e:
                    logging.error(f"Unexpected error: {e}")
                    raise
                finally:
                    # 清理临时文件
                    try:
                        os.remove("input.txt")
                    except Exception as e:
                        logging.warning(f"Cannot remove temporary file input.txt: {str(e)}")
        else:
            with open("input.txt", "r") as input_file:
                try:
                    # 通过 input.txt 执行 Multiwfn 命令, 并将输出结果重定向到 output.txt 中
                    subprocess.run([self.multiwfn_path], stdin=input_file, check=True)
                except subprocess.CalledProcessError as e:
                    logging.error(
                        f"Error executing Multiwfn command with input {input_content}: {e}"
                    )
                except Exception as e:
                    logging.error(f"Unexpected error: {e}")
                    raise
                finally:
                    # 清理临时文件
                    try:
                        os.remove("input.txt")
                    except Exception as e:
                        logging.warning(f"Cannot remove temporary file input.txt: {str(e)}")

    def multiwfn_process_fchk_to_json(self, specific_directory: str = None):
        '''
        If a specific directory is given, this method can be used separately to implement batch processing of FCHK files with Multiwfn and save the desired electrostatic potential analysis results to the corresponding JSON file. Otherwise, the folder list provided during initialization will be processed sequentially.

        :params
            specific_directory: The specific directory to process. If None, all folders will be processed.
        '''
        if specific_directory is None:
            for folder in self.folders:
                os.makedirs(f"Optimized/{folder}", exist_ok=True)
                self._multiwfn_process_fchk_to_json(folder)
        else:
            folder = specific_directory
            self._multiwfn_process_fchk_to_json(folder)

    def _multiwfn_process_fchk_to_json(self, folder: str):
        '''
        Private method:
        Perform electrostatic potential analysis on .fchk files using Multiwfn and save the analysis results to a .json file.

        :params
            folder: The folder containing the .fchk files to be processed.
        '''
        # 在每个文件夹中获取 .fchk 文件并根据文件名排序, 再用 Multiwfn 进行静电势分析, 最后将分析结果保存到同名 .json 文件中
        fchk_files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith('.fchk')]
        if fchk_files == []:
            raise FileNotFoundError("No availible Gaussian .fchk file to process")
        fchk_files.sort()
        bad_files = []
        for fchk_file in fchk_files:
            base_name = os.path.splitext(fchk_file)[0]
            json_file = f'{base_name}.json'
            if os.path.exists(json_file):
                if os.path.exists(f"Optimized/{json_file}"):
                    logging.info(f'{json_file} already exists, skipping multiwfn fchk_to_json processing.')
                else:
                    shutil.copy(src=f"{json_file}", dst=f"Optimized/{json_file}")
            else:
                result_flag = self._single_multiwfn_fchk_to_json(fchk_file)
                if not result_flag:
                    bad_files.append(base_name)
        if bad_files:
            logging.error(f'Bad Gaussian results for {bad_files}')
            os.makedirs(f'Bad/{folder}', exist_ok=True)
            # 文件扩展名列表
            suffixes = ['gjf', 'chk', 'log', 'fchk']
            for file in bad_files:
                try:
                    for suffix in suffixes:
                        shutil.move(src=f"{file}.{suffix}", dst=f"Bad/{file}.{suffix}")
                except FileNotFoundError as e:
                    logging.error(f'Error with moving bad files: {e}')
        logging.info(f'\nElectrostatic potential analysis by Multiwfn for {folder} folder has completed, and the results have been stored in the corresponding json files.\n')

    def _single_multiwfn_fchk_to_json(self, fchk_filename: str):
        '''
        Private method: 
        Use multiwfn to perform electrostatic potential analysis on each FCHK file separately, and save the required results to a corresponding JSON file.

        :params 
            fchk_filename: The full path of the FCHK file to be processed.

        :return: True if the processing is successful, False if the FCHK file is invalid.
        '''
        print(f'Multiwfn processing {fchk_filename}')
        logging.info(f'Multiwfn processing {fchk_filename}')
        result_flag = True
        self._multiwfn_cmd_build(
            input_content=f"{fchk_filename}\n12\n0\n-1\n-1\nq\n", 
            output_file='output.txt')
        print(f'Finished processing {fchk_filename}')

        # 获取目录以及 .fchk 文件的无后缀文件名, 即 refcode
        folder, filename = os.path.split(fchk_filename)
        refcode, _ = os.path.splitext(filename)
        try:
            with open('output.txt', 'r') as output_file:
                output_content = output_file.read()
        except Exception as e:
            logging.error(f"Error reading output.txt: {e}")
            raise
        # 提取所需数据
        volume_match = re.search(r'Volume:\s*([\d.]+)\s*Bohr\^3\s+\(\s*([\d.]+)\s*Angstrom\^3\)', output_content)
        density_match = re.search(r'Estimated density according to mass and volume \(M/V\):\s*([\d.]+)\s*g/cm\^3', output_content)
        volume = volume_match.group(2) if volume_match else None  # Angstrom^3
        density = density_match.group(1) if density_match else None  # g/cm^3
        
        overall_surface_area_match = re.search(r'Overall surface area:\s*([\d.]+)\s*Bohr\^2\s+\(\s*([\d.]+)\s*Angstrom\^2\)', output_content)
        positive_surface_area_match = re.search(r'Positive surface area:\s*([\d.]+)\s*Bohr\^2\s+\(\s*([\d.]+)\s*Angstrom\^2\)', output_content)
        negative_surface_area_match = re.search(r'Negative surface area:\s*([\d.]+)\s*Bohr\^2\s+\(\s*([\d.]+)\s*Angstrom\^2\)', output_content)
        overall_surface_area = overall_surface_area_match.group(2) if overall_surface_area_match else 'NaN'  # Angstrom^2
        positive_surface_area = positive_surface_area_match.group(2) if positive_surface_area_match else 'NaN'  # Angstrom^2
        negative_surface_area = negative_surface_area_match.group(2) if negative_surface_area_match else 'NaN'  # Angstrom^2
        
        overall_average_value_match = re.search(r'Overall average value:\s*[\d.-]*\s*a\.u\.\s*\(\s*([\d.-]+|NaN)\s*kcal/mol\)', output_content)
        positive_average_value_match = re.search(r'Positive average value:\s*[\d.-]*\s*a\.u\.\s*\(\s*([\d.-]+|NaN)\s*kcal/mol\)', output_content)
        negative_average_value_match = re.search(r'Negative average value:\s*[\d.-]*\s*a\.u\.\s*\(\s*([\d.-]+|NaN)\s*kcal/mol\)', output_content)
        overall_average_value = overall_average_value_match.group(1) if overall_average_value_match else 'NaN'
        positive_average_value = positive_average_value_match.group(1) if positive_average_value_match else 'NaN'
        negative_average_value = negative_average_value_match.group(1) if negative_average_value_match else 'NaN'

        # 判断阳离子或阴离子
        if (positive_surface_area == overall_surface_area and
            positive_average_value == overall_average_value and
            negative_surface_area == '0.00000' and
            negative_average_value == 'NaN'):
            ion_type = 'cation'
            
        elif (negative_surface_area == overall_surface_area and
            negative_average_value == overall_average_value and
            positive_surface_area == '0.00000' and
            positive_average_value == 'NaN'):
            ion_type = 'anion'
        else:
            ion_type = 'mixed_ion'

        try:
            # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度 g/cm³
            molecular_mass = round(float(volume) * float(density) / 1.66054, 5)
        except TypeError as e:
            print(f"Bad .fchk file: {fchk_filename}: {e}")
            logging.error(f"Bad .fchk file: {fchk_filename}: {e}")
            result_flag = False
            return result_flag
        
        result = {'refcode':refcode, 'ion_type':ion_type, 'molecular_mass':molecular_mass, 'volume':volume, 'density':density, 'positive_surface_area':positive_surface_area, 'positive_average_value':positive_average_value, 'negative_surface_area':negative_surface_area, 'negative_average_value':negative_average_value}
        if result_flag:
            with open (f"{folder}/{refcode}.json", 'w') as json_file:
                json.dump(result, json_file, indent=4)
            shutil.copyfile(src=f"{folder}/{refcode}.json", dst=f"Optimized/{folder}/{refcode}.json")
        logging.info(f'Finished processing {fchk_filename}')
        try:
            os.remove("output.txt")
        except Exception as e:
            logging.warning(f"Cannot remove temporary file output.txt: {str(e)}")
        return result_flag

    def gaussian_log_to_optimized_gjf(self, specific_directory: str = None):
        """
        If a specific directory is given, this method can be used separately to batch process the last frame of Gaussian optimized LOG files into GJF files using Multiwfn.
        Otherwise, the folder list provided during initialization will be processed in order.

        :params
            specific_directory: The specific directory to process. If None, all folders will be processed.
        """
        if specific_directory is None:
            for folder in self.folders:
                os.makedirs(f"Optimized/{folder}", exist_ok=True)
                self._gaussian_log_to_optimized_gjf(folder)
        else:
            folder = specific_directory
            self._gaussian_log_to_optimized_gjf(folder)
            
    def _gaussian_log_to_optimized_gjf(self, folder: str):
        '''
        Private method:
        Due to the lack of support of Pyxtal module for LOG files in subsequent crystal generation, it is necessary to convert the last frame of the Gaussian optimized LOG file to a .gjf file with Multiwfn processing.

        :params
            folder: The folder containing the Gaussian LOG files to be processed.
        '''
        # 在每个文件夹中获取 .log 文件并根据文件名排序, 再用Multiwfn载入优化最后一帧转换为 gjf 文件
        log_files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith('.log')]
        if not log_files:
            raise FileNotFoundError(f'No availible Gaussian .log file to process in {folder}')
        log_files.sort()
        for log_file in log_files:
            base_name = os.path.splitext(log_file)[0]
            gjf_file = f"{base_name}.gjf"
            if os.path.exists(os.path.join('Optimized', gjf_file)):
                logging.info(f"{gjf_file} already exists, skipping multiwfn log_to_gjf processing.")
            else:
                self._single_multiwfn_log_to_gjf(folder, log_file)
        try:
            os.remove('input.txt')
        except FileNotFoundError:
            pass
        logging.info(f'\nThe .log to .gjf conversion by Multiwfn for {folder} folder has completed, and the optimized .gjf structures have been stored in the optimized directory.\n')

    def _single_multiwfn_log_to_gjf(self, folder: str, log_filename: str):  
        """
        Private method: 
        Use Multiwfn to convert the last frame of the Gaussian optimized LOG file to a .gjf file.

        :params
            folder: The folder containing the Gaussian LOG file to be processed.
            log_filename: The full path of the LOG file to be processed.
        """      
        # 获取目录以及 .fchk 文件的无后缀文件名, 即 refcode
        _, filename = os.path.split(log_filename)
        refcode, _ = os.path.splitext(filename)
        
        try:
            # Multiwfn首先载入优化任务的out/log文件, 然后输入gi, 再输入要保存的gjf文件名, 此时里面的结构就是优化最后一帧的, 还避免了使用完全图形界面  
            self._multiwfn_cmd_build(
                input_content=f"{log_filename}\ngi\nOptimized/{folder}/{refcode}.gjf\nq\n"
            )
            if os.path.exists(f"Optimized/{folder}/{refcode}.gjf"):
                print(f'Finished converting {refcode} .log to .gjf')
                logging.info(f'Finished converting {refcode} .log to .gjf')
            else:
                print(f'Error with converting {refcode} .log to .gjf')
                logging.error(f"Error with converting {refcode} .log to .gjf")
        except Exception as e:
            print(f'Error with processing {log_filename}: {e}')
            logging.error(f'Error with processing {log_filename}: {e}')

    def _read_gjf_elements(self, gjf_file):
        """
        Private method:
        Read the elements from a .gjf file and return a dictionary with element counts.

        :params
            gjf_file: The full path of the .gjf file to be processed.

        :return: A dictionary with element symbols as keys and their counts as values.
        """
        # 根据每一个组合中的组分找到对应的 JSON 文件并读取其中的性质内容
        with open(gjf_file, "r") as file:
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

    def nitrogen_content_estimate(self):
        """
        Evaluate the priority of ion crystal combinations based on nitrogen content and generate .csv files
        """
        atomic_masses = {"H": 1.008, "C": 12.01, "N": 14.01, "O": 16.00}
        # 获取所有 .gjf 文件
        combinations = self._generate_combinations(suffix='.gjf')
        nitrogen_contents = []
        for combo in combinations:
            total_masses = 0.0
            nitrogen_masses = 0.0
            for gjf_file, ion_count in combo.items():
                atomic_counts = self._read_gjf_elements(gjf_file)
                for element, atom_count in atomic_counts.items():
                    if element in atomic_masses:
                        total_masses += atomic_masses[element] * atom_count * ion_count
                        if element == 'N':
                            nitrogen_masses += atomic_masses[element] * atom_count * ion_count
                    else:
                        raise "Contains element information not included, unable to calculate nitrogen content"
            nitrogen_content = round((nitrogen_masses / total_masses), 4) if total_masses > 0 else 0
            nitrogen_contents.append(nitrogen_content)
        # 将组合和对应的氮含量合并并排序
        data = []
        for combo, nitrogen in zip(combinations, nitrogen_contents):
            # 去掉 .gjf 后缀
            cleaned_combo = [name.replace(".gjf", "") for name in combo]
            # 将组合和氮含量合并成一行
            data.append(cleaned_combo + [nitrogen])
        # 根据氮含量列进行排序（氮含量在最后一列）
        data.sort(key=lambda x: float(x[-1]), reverse=True)

        # 写入排序后的 .csv 文件
        with open(self.nitrogen_csv, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            # 动态生成表头
            num_components = len(combinations[0]) if combinations else 0
            header = [f"Component {i+1}" for i in range(num_components)] + ["Nitrogen_Content"]
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
                atomic_counts = self._read_gjf_elements(gjf_file)
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
            header = [f"Component {i + 1}" for i in range(num_components)] + ["N_C_Ratio", "O_Atoms"]
            writer.writerow(header)  # 写入表头

            # 写入筛选后的组合和氧含量
            for combo, nitrogen_carbon_ratio, oxygen_content in filtered_data:
                cleaned_combo = [name.replace(".gjf", "") for name in combo]
                writer.writerow(
                    cleaned_combo + [nitrogen_carbon_ratio, oxygen_content]
                )  # 写入每一行

    def empirical_estimate(self):
        """
        Based on the electrostatic analysis obtained from the .json file, calculate the initial screening density of the ion crystal using empirical formulas, and generate the .csv file according to the sorted density.
        """
        combinations = self._generate_combinations(suffix='.json')
        predicted_crystal_densities = []
        for combo in combinations:
            # 每个组合包含数个离子，分别获取其各项性质，包括质量、体积、密度、正/负电势与面积
            refcodes, ion_types, masses, volumes = [], [], 0, 0
            positive_surface_areas, positive_average_values, positive_electrostatics, negative_surface_areas, negative_average_values, negative_electrostatics = 0, 0, 0, 0, 0, 0
            for json_file, count in combo.items():
                # 根据每一个组合中的组分找到对应的 JSON 文件并读取其中的性质内容
                try:
                    with open(json_file, 'r') as json_file:
                        property = json.load(json_file)
                except json.decoder.JSONDecodeError:
                    continue
                refcodes.append(property['refcode'])
                ion_types.append(property['ion_type'])
                # 1.66054 这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度 g/cm³
                mass = property['molecular_mass'] * 1.66054
                masses += (mass * count)
                molecular_volume = float(property['volume'])
                volumes += molecular_volume * count
                positive_surface_area = property['positive_surface_area']
                negative_surface_area = property['negative_surface_area']
                positive_average_value = property['positive_average_value']
                negative_average_value = property['negative_average_value']
                if (positive_surface_area != '0.00000' and positive_average_value != 'NaN'):
                    positive_surface_areas += float(positive_surface_area) * count
                    positive_average_values += float(positive_average_value) * count
                    positive_electrostatic = float(positive_average_value) / float(positive_surface_area)
                    positive_electrostatics += positive_electrostatic * count
                if (negative_surface_area != '0.00000' and negative_average_value != 'NaN'):
                    negative_surface_areas += float(negative_surface_area) * count
                    negative_average_values += float(negative_average_value) * count
                    negative_electrostatic = float(negative_average_value) / float(negative_surface_area)
                    negative_electrostatics += negative_electrostatic * count

            # 1. 拟合经验公式参数来源：Molecular Physics 2010, 108:10, 1391-1396. 
            # http://dx.doi.org/10.1080/00268971003702221
            # alpha, beta, gamma, delta = 1.0260, 0.0514, 0.0419, 0.0227 
            # 2. 拟合经验公式参数来源：Journal of Computational Chemistry 2013, 34, 2146–2151. 
            # https://doi.org/10.1002/jcc.23369
            alpha, beta, gamma, delta = 1.1145, 0.02056, -0.0392, -0.1683  

            M_d_Vm = masses / volumes
            predicted_crystal_density = (alpha * M_d_Vm) + (beta * positive_electrostatics) + (gamma * negative_electrostatics) + (delta)
            predicted_crystal_density = round(predicted_crystal_density, 4)
            predicted_crystal_densities.append(predicted_crystal_density)

        # 将组合和对应的密度合并并排序
        data = []
        for combo, density in zip(combinations, predicted_crystal_densities):
            # 去掉 .json 后缀
            cleaned_combo = [name.replace('.json', '') for name in combo]
            # 将组合和密度合并成一行
            data.append(cleaned_combo + [density])
        # 根据密度列进行排序（密度在最后一列）
        data.sort(key=lambda x: float(x[-1]), reverse=True)

        # 写入排序后的 .csv 文件
        with open(self.density_csv, 'w', newline='', encoding='utf-8') as csv_file:
            writer = csv.writer(csv_file)
            # 动态生成表头
            num_components = len(combinations[0]) if combinations else 0
            header = [f'Component {i+1}' for i in range(num_components)] + ['Pred_Density']
            writer.writerow(header)  # 写入表头
            writer.writerows(data)  # 写入排序后的数

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
            suffix_files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(suffix)]
            suffix_files.sort()
            print(f'Valid {suffix} file number in {folder}: {len(suffix_files)}')
            logging.info(f"Valid {suffix} file number in {folder}: {len(suffix_files)}")
            if not suffix_files:
                raise FileNotFoundError(f'No available {suffix} files in {folder} folder')
            all_files.append(suffix_files)

        # 对所有文件根据其文件夹与配比进行组合
        combinations = []
        for folder_files in itertools.product(*all_files):
            # 根据给定的配比生成字典形式的组合
            ratio_combination = {}
            for folder_index, count in enumerate(self.ratios):
                ratio_combination.update({folder_files[folder_index]: count})
            combinations.append(ratio_combination)
        print(f'Valid combination number: {len(combinations)}')
        logging.info(f'Valid combination number: {len(combinations)}')
        return combinations

    def _copy_combo_file(self, combo_path, folder_basename, file_type):
        """
        Private method:
        Copy the specified file type from the Optimized directory to the combo_n folder.

        :params
            combo_path: The path to the combo_n folder where the file will be copied.
            folder_basename: The basename of the folder containing the file to be copied.
            file_type: The type of file to be copied (e.g., '.gjf', '.json').
        """
        filename = f"{folder_basename}{file_type}"
        source_path = os.path.join(self.gaussian_optimized_dir, "Optimized", filename)
        # 复制指定后缀名文件到对应的 combo_n 文件夹
        if os.path.exists(source_path):
            if os.path.exists(os.path.join(combo_path, os.path.basename(filename))):
                logging.info(f'{filename} of {os.path.basename(combo_path)} already exists in {os.path.abspath(combo_path)}. Skipping copy.')
            else:
                # 复制对应的指定后缀名文件
                shutil.copy(source_path, combo_path)
                logging.info(f'Copied {os.path.basename(source_path)} to {combo_path}')
        else:
            logging.error(
                f"File of {filename} does not exist in {self.gaussian_optimized_dir}"
            )

    def make_combo_dir(self, target_dir: str, num_combos: int, ion_numbers: List[int]):
        """
        Create a combo_n folder based on the .csv file and copy the corresponding .gjf structure file.
        
        :params 
            target_directory: The target directory of the combo folder to be created
            num_folders: The number of combo folders to be created
            ion_numbers: The number of ions for ionic crystal generation step (generated in config.yaml in the corresponding combo_dir automatically)
        """
        if self.sort_by == 'density':
            base_csv = self.density_csv
        elif self.sort_by == 'nitrogen':
            base_csv = self.nitrogen_csv
        elif self.sort_by == "NC_ratio":
            base_csv = self.NC_ratio_csv
        if not target_dir:
            target_dir = f'../2_{self.sort_by}_combos'
        with open(base_csv, mode='r', newline='') as file:
            reader = csv.DictReader(file)
            # 初始化已处理的文件夹计数
            folder_count = 0
            for index, row in enumerate(reader):
                if folder_count >= num_combos:
                    break  # 达到指定文件夹数量，停止处理
                # 创建 combo_n 文件夹名称
                combo_folder = f'combo_{index+1}'
                combo_path = os.path.join(target_dir, combo_folder)
                os.makedirs(combo_path, exist_ok=True)
                folder_count += 1
                
                # 遍历每一列（组分）并复制对应的文件
                gjf_names = []
                pattern = r'^Component \d+'
                components = [key for key in row.keys() if re.match(pattern, key)]
                for component in components:
                    # folder_basename变量存放的是包含目录名的离子名称，如charge_2/ABCDEF
                    folder_basename = row[component]
                    self._copy_combo_file(combo_path, folder_basename, file_type='.gjf')
                    self._copy_combo_file(combo_path, folder_basename, file_type=".json")
                    # gjf_names存放的是不包含目录名，且带 .gjf 后缀名的文件名，用于写入config.yaml
                    gjf_names.append(f"{folder_basename.split('/')[1]}.gjf")
                
                # 生成上级目录路径并解析 .yaml 文件
                parent_dir = self.base_dir
                parent_config_path = os.path.join(parent_dir, 'config.yaml')
                base_config_path = os.path.join(self.gaussian_optimized_dir, "config.yaml")
                try:
                    with open(parent_config_path, 'r') as file:
                        config = yaml.safe_load(file)
                except FileNotFoundError as e:
                    logging.warning(f"No available config.yaml file in parent directory: {parent_dir} \n{e}")
                    logging.info(f"Trying to load config.yaml file from base directory: {parent_dir}")
                    try:
                        with open(base_config_path, 'r') as file:
                            try:
                                config = yaml.safe_load(file)
                            except yaml.YAMLError as e:
                                logging.error(f"YAML configuration file parsing failed: {e}")
                    except FileNotFoundError as e:
                        logging.error(
                            f"No available config.yaml file either in parent directory: {parent_dir} and base directory {self.gaussian_optimized_dir} \n{e}"
                        )
                        raise
                except Exception as e:
                    logging.error(f'Unexpected error: {e}')
                    raise
                try:
                    # 确保 config.yaml 配置文件中 'gen_opt' 模块存在
                    if 'gen_opt' not in config:
                        config['gen_opt'] = {}
                    # 更新 combo 文件夹中对应的离子名称与数量配置
                    config['gen_opt']['species'] = gjf_names
                    config['gen_opt']['ion_numbers'] = ion_numbers
                    logging.info(
                    f"Generated 'species' and 'ion_numbers' config for gen_opt module in config.yaml are respectively: {config['gen_opt']['species']} and {config['gen_opt']['ion_numbers']}"
                )
                    with open(os.path.join(combo_path, 'config.yaml'), 'w') as file:
                        yaml.dump(config, file)
                except Exception as e:
                    logging.error(f"Unexpected error: {e}")
