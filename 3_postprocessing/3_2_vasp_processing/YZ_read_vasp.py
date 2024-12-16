import os
import csv
import time
import logging
from ase.io.vasp import read_vasp_out

class vasp_processing:

    def __init__(self, target_folder):
        # 获取脚本的当前目录
        self.base_dir = os.path.dirname(__file__)
        # 寻找同一目录下的optimized文件夹
        self.folder_dir = os.path.join(self.base_dir, target_folder)

    def read_vaspout_save_csv(self, fine_dir=False):
        """
        Batch read VASP output files and save energy and density to corresponding CSV files in the directory
        
        :param if_fine: If there is step-by-step optimization, further read the refined optimization folder, which defaults to the fine folder in the vasp result directory
        """
        vasp_opt_dir = self.folder_dir
        numbers, pred_densities = [], []
        rough_densities, rough_energies = [], []
        fine_densities, fine_energies = [], []
        for folder in os.listdir(vasp_opt_dir):
            vasp_opt_path = os.path.join(vasp_opt_dir, folder)
            if os.path.isdir(vasp_opt_path):
                pred_density, number = folder.split('_')[0], folder.split('_')[1]
                numbers.append(number)
                pred_densities.append(pred_density)
                # 读取一级目录下的OUTCAR文件
                OUTCAR_file_path = os.path.join(vasp_opt_path, 'OUTCAR')               
                structure = read_vasp_out(OUTCAR_file_path)
                atoms_volume = structure.get_volume()  # 体积单位为立方埃（Å³）
                atoms_masses = sum(structure.get_masses())  # 质量单位为原子质量单位(amu)
                # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
                density = round(1.66054 * atoms_masses / atoms_volume, 4)
                energy = round(structure.get_total_energy(), 4)
                print(f'{folder}: density: {density}, energy: {energy}')
                if fine_dir:
                    # 读取二级目录下的OUTCAR文件
                    fine_OUTCAR_file_path = os.path.join(vasp_opt_path, fine_dir, 'OUTCAR')               
                    fine_structure = read_vasp_out(fine_OUTCAR_file_path)
                    fine_atoms_volume = fine_structure.get_volume()  # 体积单位为立方埃（Å³）
                    fine_atoms_masses = sum(fine_structure.get_masses())  # 质量单位为原子质量单位(amu)
                    # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
                    fine_density = round(1.66054 * fine_atoms_masses / fine_atoms_volume, 4)
                    fine_energy = round(fine_structure.get_total_energy(), 4)
                    print(f'{folder}: fine_density: {fine_density}, fine_energy: {fine_energy}')
                else:
                    fine_density, fine_energy = 0, 0
            rough_densities.append(density)
            rough_energies.append(energy)
            fine_densities.append(fine_density)
            fine_energies.append(fine_energy)

        with open(f'{vasp_opt_dir}/vasp_density_energy.csv', 'w', newline='', encoding='utf-8') as csv_file:
            writer = csv.writer(csv_file)
            header = ['Number', 'Pred_density', 'Rough_density', 'Rough_energy', 'Fine_density', 'Fine_energy']
            writer.writerow(header)
            datas = list(zip(numbers, pred_densities, rough_densities, rough_energies, fine_densities, fine_energies))
            datas.sort(key=lambda x: float(x[1]), reverse=True)
            for data in datas:
                writer.writerow(data)


def log_and_time(func):
    """Decorator for recording log information and script runtime"""
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
    result = vasp_processing('vasp_results/vasp_combo_3/vasp_opt')
    result.read_vaspout_save_csv(fine_dir='fine')


if __name__ == "__main__":
    main()