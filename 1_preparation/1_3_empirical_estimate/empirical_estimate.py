import os
import re
import csv
import time
import json
import shutil
import logging
import itertools
import subprocess

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

class empirical_estimation:
    def __init__(self, folders:list, ratios:list):
        """
        Retrieve the directory where the current script is located and use it as the working directory.
        """
        self.base_dir = os.path.dirname(__file__)
        os.chdir(self.base_dir)
        # 确保所取的文件夹数与配比数是对应的
        if len(folders) != len(ratios):
            raise ValueError('The number of folders must match the number of ratios.')
        self.folders = folders
        self.ratios = ratios

    def multiwfn_process_fchk_to_json(self, specific_directory=None):
        '''
        If a specific directory is given, this method can be used separately to implement batch processing of FCHK files with Multiwfn and save the desired electrostatic potential analysis results to the corresponding JSON file. Otherwise, the folder list provided during initialization will be processed sequentially.
        '''
        if specific_directory is None:
            for folder in self.folders:
                self._multiwfn_process_fchk_to_json(folder)
        else:
            folder = specific_directory
            self._multiwfn_process_fchk_to_json(folder)

    def _multiwfn_process_fchk_to_json(self, folder):
        '''
        Perform electrostatic potential analysis on FCHK files using Multiwfn and save the analysis results to a JSON file.
        '''
        # 在每个文件夹中获取 FCHK 文件并根据文件名排序, 再用 Multiwfn 进行静电势分析, 最后将分析结果保存到同名 JSON 文件中
        fchk_files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith('.fchk')]
        fchk_files.sort()
        for fchk_file in fchk_files:
            self._single_multiwfn_process(fchk_file)
            print(f'')
        logging.info(f'\nElectrostatic potential analysis by Multiwfn for {folder} folder has completed, and the results have been stored in the corresponding json files.\n')

    def _single_multiwfn_process(self, fchk_filename):
        '''
        Private method: Use multiwfn to perform electrostatic potential analysis on each FCHK file separately, and save the required results to a corresponding JSON file.
        '''
        print(f'Multiwfn processing {fchk_filename}')
        logging.info(f'Multiwfn processing {fchk_filename}')
        # 创建 input.txt 用于存储 Multiwfn 命令内容
        with open(f'input.txt', 'w') as input_file:
            input_file.write(f"{fchk_filename}\n12\n0\nq\n")
        # 通过 input.txt 执行 Multiwfn 命令, 并将输出结果重定向到output.txt中
        subprocess.run(f'Multiwfn_noGUI < input.txt > output.txt', shell=True, capture_output=True)
        # 获取目录以及 .fchk 文件的无后缀文件名, 即 refcode
        folder, filename = os.path.split(fchk_filename)
        refcode, _ = os.path.splitext(filename)
        with open('output.txt', 'r') as output_file:
            output_content = output_file.read()
            # 提取所需数据
            volume_match = re.search(r'Volume:\s*([\d.]+)\s*Bohr\^3\s+\(\s*([\d.]+)\s*Angstrom\^3\)', output_content)
            density_match = re.search(r'Estimated density according to mass and volume \(M/V\):\s*([\d.]+)\s*g/cm\^3', output_content)
            volume = volume_match.group(2) if volume_match else None  # Angstrom^3
            density = density_match.group(1) if density_match else None  # g/cm^3
            try:
                # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度 g/cm³
                molecular_mass =  round(float(volume) * float(density) / 1.66054, 5)
            except TypeError as e:
                print(f'Bad FCHK file: {fchk_filename}: {e}')
                logging.error(f'Bad FCHK file: {fchk_filename}: {e}')
                return
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

        result = {'refcode':refcode, 'ion_type':ion_type, 'molecular_mass':molecular_mass, 'volume':volume, 'density':density, 'positive_surface_area':positive_surface_area, 'positive_average_value':positive_average_value, 'negative_surface_area':negative_surface_area, 'negative_average_value':negative_average_value}
        with open (f'{folder}/{refcode}.json', 'w') as json_file:
            json.dump(result, json_file, indent=4)
        os.remove('input.txt')
        os.remove('output.txt')
        print(f'Finished processing {fchk_filename}')
        logging.info(f'Finished processing {fchk_filename}')

    def gaussian_log_to_optimized_gjf(self, specific_directory=None):
        """
        If a specific directory is given, this method can be used separately to batch process the last frame of Gaussian optimized LOG files into GJF files using Multiwfn.
        Otherwise, the folder list provided during initialization will be processed in order.
        """
        if specific_directory is None:
            for folder in self.folders:
                os.makedirs(f'Optimized/{folder}', exist_ok=True)
                self._gaussian_log_to_optimized_gjf(folder)
        else:
            folder = specific_directory
            self._gaussian_log_to_optimized_gjf(folder)
            
    def _gaussian_log_to_optimized_gjf(self, folder):
        '''
        Due to the lack of support of Pyxtal module for LOG files in subsequent crystal generation, it is necessary to convert the last frame of the Gaussian optimized LOG file to a GJF file with Multiwfn processing.
        '''
        # 在每个文件夹中获取 .log 文件并根据文件名排序, 再用Multiwfn载入优化最后一帧转换为 gjf 文件
        log_files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith('.log')]
        log_files.sort()
        os.chdir(self.base_dir)
        for log_file in log_files:
            print(f'Multiwfn processing {log_file}')
            logging.info(f'Multiwfn processing {log_file}')                
            # 获取目录以及 .fchk 文件的无后缀文件名, 即 refcode
            _, filename = os.path.split(log_file)
            refcode, _ = os.path.splitext(filename)
            try:
                # 创建 input.txt 用于存储 Multiwfn 命令内容
                with open(f'input.txt', 'w') as input_file:
                    input_file.write(f"{log_file}\ngi\nOptimized/{folder}/{refcode}.gjf\nq\n")
                # Multiwfn首先载入优化任务的out/log文件, 然后输入gi, 再输入要保存的gjf文件名, 此时里面的结构就是优化最后一帧的, 还避免了使用完全图形界面           
                subprocess.run(f'Multiwfn_noGUI < input.txt', shell=True, capture_output=True)
                print(f'Finished converting {refcode} .log to .gjf')
                logging.info(f'Finished converting {refcode} .log to .gjf')
            except Exception as e:
                print(f'Error with processing {log_file}: {e}')
                logging.error(f'Error with processing {log_file}: {e}')
        os.remove('input.txt')
        logging.info(f'\nThe .log to .gjf conversion by Multiwfn for {folder} folder has completed, and the optimized .gjf structures have been stored in the optimized directory.\n')

    def empirical_estimate(self):
        """
        Based on the electrostatic analysis obtained from the JSON file, calculate the initial screening density of the ion crystal using empirical formulas, and generate the CSV file according to the sorted density.
        """
        # 获取所有 JSON 文件
        all_files = []
        for folder in self.folders:
            json_files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith('.json')]
            json_files.sort()
            print(f'Valid JSON file number in {folder}: {len(json_files)}')
            logging.info(f'Valid JSON file number in {folder}: {len(json_files)}')
            if not json_files:
                raise FileNotFoundError(f'No available JSON files in {folder} folder')
            all_files.append(json_files)

        # 对所有 JSON 文件根据其文件夹与配比进行组合
        combinations = []
        for folder_files in itertools.product(*all_files):
            # 根据给定的配比生成组合
            ratio_combination = []
            for folder_index, count in enumerate(self.ratios):
                ratio_combination.extend([folder_files[folder_index]] * count)
            combinations.append(ratio_combination)
        print(f'Valid combination number: {len(combinations)}')
        logging.info(f'Valid combination number: {len(combinations)}')
        predicted_crystal_densities = []
        for combo in combinations:
            # 每个组合包含数个离子，分别获取他们的各项性质，包括质量、体积、密度、正/负电势与面积
            refcodes, ion_types, masses, volumes = [], [], [], []
            positive_surface_areas, positive_average_values, positive_electrostatics, negative_surface_areas, negative_average_values, negative_electrostatics = [], [], [], [], [], []
            for json_file in combo:
                # 根据每一个组合中的组分找到对应的 JSON 文件并读取其中的性质内容
                with open(json_file, 'r') as json_file:
                    property = json.load(json_file)
                refcodes.append(property['refcode'])
                ion_types.append(property['ion_type'])
                # 1.66054 这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度 g/cm³
                mass = property['molecular_mass'] * 1.66054
                masses.append(mass)
                molecular_volume = float(property['volume'])
                volumes.append(molecular_volume)
                positive_surface_area = property['positive_surface_area']
                negative_surface_area = property['negative_surface_area']
                positive_average_value = property['positive_average_value']
                negative_average_value = property['negative_average_value']
                if (positive_surface_area != '0.00000' and positive_average_value != 'NaN'):
                    positive_surface_areas.append(float(positive_surface_area))
                    positive_average_values.append(float(positive_average_value))
                    positive_electrostatic = float(positive_average_value) / float(positive_surface_area)
                    positive_electrostatics.append(positive_electrostatic)
                if (negative_surface_area != '0.00000' and negative_average_value != 'NaN'):
                    negative_surface_areas.append(float(negative_surface_area))
                    negative_average_values.append(float(negative_average_value))
                    negative_electrostatic = float(negative_average_value) / float(negative_surface_area)
                    negative_electrostatics.append(negative_electrostatic)

            # 1. 拟合经验公式参数来源：Molecular Physics 2010, 108:10, 1391-1396. 
            # http://dx.doi.org/10.1080/00268971003702221
            # alpha, beta, gamma, delta = 1.0260, 0.0514, 0.0419, 0.0227 
            # 2. 拟合经验公式参数来源：Journal of Computational Chemistry 2013, 34, 2146–2151. 
            # https://doi.org/10.1002/jcc.23369
            alpha, beta, gamma, delta = 1.1145, 0.02056, -0.0392, -0.1683  

            M_d_Vm = sum(masses) / sum(volumes)
            predicted_crystal_density = (alpha * M_d_Vm) + (beta * sum(positive_electrostatics)) + (gamma * sum(negative_electrostatics)) + (delta)
            predicted_crystal_densities.append(predicted_crystal_density)

        # 将组合和对应的密度合并并排序
        data = []
        for combo, density in zip(combinations, predicted_crystal_densities):
            # 去掉 .json 后缀
            cleaned_combo = [name.replace('.json', '') for name in combo]
            # 将组合和密度合并成一行
            data.append(cleaned_combo + [density])
        # 根据密度列进行排序（假定密度在最后一列）
        data.sort(key=lambda x: float(x[-1]), reverse=True)

        # 写入排序后的 .csv 文件
        with open('sorted_density.csv', 'w', newline='', encoding='utf-8') as csv_file:
            writer = csv.writer(csv_file)
            # 动态生成表头
            num_components = len(combinations[0]) if combinations else 0
            header = [f'Component {i+1}' for i in range(num_components)] + ['Density']
            writer.writerow(header)  # 写入表头
            writer.writerows(data)  # 写入排序后的数

    def make_combo_dir(self, csv_file, working_directory, target_directory, num_folders):
        """
        Create a combo_n folder based on the .csv file and copy the corresponding .gjf structure file.

        :param csv_file: path of .csv file
        :param working_directory: Working directory containing optimized .gjf structure files
        :param target_directory: The target directory of the combo folder to be created
        :param num_folders: The number of combo folders to be created
        """
        with open(csv_file, mode='r', newline='') as file:
            reader = csv.DictReader(file)
            
            # 初始化已处理的文件夹计数
            folder_count = 0
            for index, row in enumerate(reader):
                if folder_count >= num_folders:
                    break  # 达到指定文件夹数量，停止处理
                # 创建 combo_n 文件夹名称
                combo_folder = f"combo_{index + 1}"
                combo_path = os.path.join(target_directory, combo_folder)
                # 检查文件夹是否已存在
                if os.path.exists(combo_path):
                    print(f"Folder {combo_path} already exists. Skipping...")
                    continue
                # 创建文件夹
                os.makedirs(combo_path)
                folder_count += 1
                
                # 遍历每一列（组件）并复制对应的 .gjf 文件
                for component in row.keys():
                    if component == 'Density':
                        continue
                    gjf_filename = f"{row[component]}.gjf"  # 使用当前列的值
                    gjf_source_path = os.path.join(working_directory, gjf_filename)
                    
                    # 复制文件到对应的 combo_n 文件夹
                    if os.path.exists(gjf_source_path):
                        shutil.copy(gjf_source_path, combo_path)
                        print(f"Copied {gjf_filename} to {combo_path}")
                    else:
                        print(f"File {gjf_filename} does not exist in {working_directory}")


def log_and_time(func):
    '''Decorator for recording log information and script runtime'''
    # 获取脚本所在目录, 在该目录下生成日志
    base_dir = os.path.dirname(__file__)  
    log_file_path = os.path.join(base_dir, 'output.log')
    # 配置日志记录
    logging.basicConfig(
        filename = log_file_path,  # 日志文件名
        level = logging.INFO,  # 指定日志级别
        format='%(asctime)s - %(levelname)s - %(message)s'  # 日志格式
    )    
    def wrapper(*args, **kwargs):
        # 获取程序开始执行时的CPU时间和Wall Clock时间
        start_cpu, start_clock = time.process_time(), time.perf_counter()
        # 记录程序开始信息
        script_name = os.path.basename(__file__) 
        logging.info(f'Start running: {script_name}')
        # 调用实际的函数, 如果出现错误, 报错的同时也将错误信息记录到日志中
        result = None
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            logging.error(f'Error occurred: {e}', exc_info=True)
            raise
        print('The script has run successfully, and the output content has been recorded in the output.log file in the same directory.')
        # 获取程序结束时的CPU时间和Wall Clock时间
        end_cpu, end_clock = time.process_time(), time.perf_counter()
        # 计算CPU时间和Wall Clock时间的差值
        cpu_time, wall_time = end_cpu-start_cpu, end_clock-start_clock
        # 记录程序结束信息
        logging.info(f'End running: {script_name}\nWall time: {wall_time:.4f} sec, CPU time: {cpu_time:.4f} sec\n')
        return result
    return wrapper

@ log_and_time
def main():
    # 在脚本同目录下准备 Gaussian 优化处理后具有 .fchk 和 .log 文件的文件夹, 并提供对应的配比
    result = empirical_estimation(folders=['N5', 'nitro_-1', 'charge_2'], ratios=[1, 1, 1])
    # 对 FCHK 文件用 Multiwfn 进行静电势分析, 并将经验公式所需的分析结果保存到同名 JSON 文件中
    # result.multiwfn_process_fchk_to_json()
    # 由于后续晶体生成不支持 .log 文件，需要将 Gaussian 优化得到的 .log 文件最后一帧转为 .gjf 文件
    # result.gaussian_log_to_optimized_gjf()
    # 根据配比生成离子晶体组合，读取 .json 文件并将各离子性质代入经验公式，最终将预测的离子晶体密度以及对应的组分输出到 .csv 文件并根据密度从大到小排序
    # result.empirical_estimate()
    # 基于.csv文件创建一个combo_n文件夹，并复制相应的.gjf结构文件。
    result.make_combo_dir(csv_file='sorted_density.csv', working_directory='Optimized_gjf', target_directory='test/test_combo', num_folders=5)

if __name__ == '__main__':
    main()
