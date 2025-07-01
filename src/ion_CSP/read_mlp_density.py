import os
import csv
import shutil
import logging
import subprocess
from ase.io.vasp import read_vasp
from ion_CSP.identify_molecules import identify_molecules, molecules_information

class ReadMlpDensity:
    
    def __init__(self, work_dir:str, folder:str = '2_mlp_optimized'):
        """
        This class is designed to read and process MLP optimized files, specifically CONTCAR files, to calculate and sort their densities.
        The class also provides functionality to process these files using phonopy for symmetry analysis and primitive cell generation.

        :params
            work_dir: The working directory where the MLP optimized files are located.
        """
        # 获取脚本的当前目录
        self.base_dir = work_dir
        os.chdir(self.base_dir)
        # 寻找同一目录下的2_mlp_optimized文件夹
        self.folder_dir = os.path.join(self.base_dir, folder)
        self.max_density_dir = os.path.join(self.folder_dir, 'max_density')
        self.primitive_cell_dir = os.path.join(self.folder_dir, 'primitive_cell')
        print(f"Processing MLP CONTCARs in {self.folder_dir}")
        logging.info(f"Processing MLP CONTCARs in {self.folder_dir}")

    def _sequentially_read_files(self, directory: str, prefix_name: str = 'POSCAR_'):
        """
        Private method: 
        Extract numbers from file names, convert them to integers, sort them by sequence, and return a list containing both indexes and file names
        """
        # 获取dir文件夹中所有以prefix_name开头的文件，在此实例中为POSCAR_
        files = [f for f in os.listdir(directory) if f.startswith(prefix_name)]
        file_index_pairs = []
        for filename in files:
            index_part = filename[len(prefix_name):]  # 选取去除前缀'POSCAR_'的数字
            if index_part.isdigit():  # 确保剩余部分全是数字
                index = int(index_part)
                file_index_pairs.append((index, filename))
        file_index_pairs.sort(key=lambda pair: pair[0])
        return file_index_pairs
    
    def read_density_and_sort(self, n_screen: int = 10, molecules_screen: bool = True, detail_log: bool = False):
        """
        Obtain the atomic mass and unit cell volume from the optimized CONTCAR file, and obtain the ion crystal density. Finally, take n CONTCAR files with the highest density and save them separately for viewing.
        
        :params
            n_screen: The number of CONTCAR files with the highest density to be saved.
            molecules_screen: If True, only consider ionic crystals with original ions.
            detail_log: If True, print detailed information about the molecules identified in the CONTCAR files.
        """
        os.chdir(self.base_dir)
        # 获取所有以'CONTCAR_'开头的文件，并按数字顺序处理
        CONTCAR_file_index_pairs = self._sequentially_read_files(self.folder_dir, prefix_name='CONTCAR_')
        # 逐个处理文件
        density_index_list = []
        for _, CONTCAR_filename in CONTCAR_file_index_pairs:
            atoms = read_vasp(os.path.join(self.folder_dir, CONTCAR_filename))
            molecules, molecules_flag, initial_information = identify_molecules(atoms)
            if detail_log:
                molecules_information(molecules, molecules_flag, initial_information)
            if molecules_screen:
                if molecules_flag:
                    atoms_volume = atoms.get_volume()  # 体积单位为立方埃（Å³）
                    atoms_masses = sum(atoms.get_masses())  # 质量单位为原子质量单位(amu)
                    # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
                    density = 1.66054 * atoms_masses / atoms_volume       
                    density_index_list.append((density, CONTCAR_filename))
            else:
                atoms_volume = atoms.get_volume()  # 体积单位为立方埃（Å³）
                atoms_masses = sum(atoms.get_masses())  # 质量单位为原子质量单位(amu)
                # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
                density = 1.66054 * atoms_masses / atoms_volume       
                density_index_list.append((density, CONTCAR_filename))
        if molecules_screen:
            print(f'Total optimized ionic crystals: {len(CONTCAR_file_index_pairs)}')
            print(f'Screened ionic crystals with original ions: {len(density_index_list)}')
            logging.info(f'Total optimized ionic crystals: {len(CONTCAR_file_index_pairs)}')
            logging.info(f'Screened ionic crystals with original ions: {len(density_index_list)}')
        # 根据密度降序排序
        sorted_filename = sorted(density_index_list, key=lambda x: x[0], reverse=True)
        # 将前n个最大密度的CONTCAR文件进行重命名并保存到max_density文件夹
        if os.path.exists(self.max_density_dir):
            backup_dir = os.path.join(self.folder_dir, 'backup', 'max_density')
            os.makedirs(backup_dir, exist_ok=True)
            for item in os.listdir(self.max_density_dir):
                shutil.move(os.path.join(self.max_density_dir, item), os.path.join(backup_dir, item))
        numbers, mlp_densities, mlp_energies = [], [], []
        os.makedirs(self.max_density_dir, exist_ok=True)
        for density, CONTCAR_filename in sorted_filename[:n_screen]:
            # 生成新的包含密度值的文件名，并重命名文件
            # 密度转换为字符串，保留3位小数
            density_str = f'{density:.3f}'
            mlp_densities.append(density_str)
            # 保留 CONTCAR 的序数信息，方便回推检查
            number = CONTCAR_filename.split("_")[1]
            numbers.append(number)
            OUTCAR_filename = f'OUTCAR_{number}'
            try:
                with open(f"{self.folder_dir}/OUTCAR_{number}") as mlp_out:
                    lines = mlp_out.readlines()
                    for line in lines:
                        if "TOTEN" in line:
                            values = line.split()
                            mlp_energy = round(float(values[-2]), 2)
            except FileNotFoundError:
                logging.error(
                    f"  No avalible MLP OUTCAR_{number} found"
                )
                mlp_energy = False
            mlp_energies.append(mlp_energy)
            new_CONTCAR_filename = f'CONTCAR_{density_str}_{number}'
            new_OUTCAR_filename = f'OUTCAR_{density_str}_{number}'
            shutil.copy(f'{self.folder_dir}/{CONTCAR_filename}', f'{self.max_density_dir}/{new_CONTCAR_filename}')
            shutil.copy(f'{self.folder_dir}/{OUTCAR_filename}', f'{self.max_density_dir}/{new_OUTCAR_filename}')
            print(f'New CONTCAR and OUTCAR of {density_str}_{number} are renamed and saved')
            logging.info(f'New CONTCAR and OUTCAR of {density_str}_{number} are renamed and saved')

        with open(
            f"{self.max_density_dir}/mlp_density_energy.csv",
            "w",
            newline="",
            encoding="utf-8",
        ) as csv_file:
            writer = csv.writer(csv_file)
            header = [
                "Number",
                "MLP_E",
                "MLP_Density",
            ]
            datas = list(
                zip(
                    numbers,
                    mlp_energies,
                    mlp_densities,
                )
            )
            datas.sort(key=lambda x: -float(x[-1]))
            writer.writerow(header)
            for data in datas:
                writer.writerow(data)

    def phonopy_processing_max_density(self, specific_directory :str = None):
        """
        Use phonopy to check and generate symmetric primitive cells, reducing the complexity of subsequent optimization calculations, and preventing pyxtal.from_random from generating double proportioned supercells. 
        
        :params
            specific_directory: If specified, phonopy will process the files in this directory instead of the max_density directory.
            If not specified, it will process the files in the max_density directory.
        """
        if specific_directory:
            self.phonopy_dir = os.path.join(self.base_dir, specific_directory)
            self.primitive_cell_dir = os.path.join(os.path.dirname(self.phonopy_dir), 'primitive_cell')
        else:
            self.phonopy_dir = self.max_density_dir
        if os.path.exists(self.primitive_cell_dir):
            backup_dir = os.path.join(self.folder_dir, 'backup', 'primitive_cell')
            os.makedirs(backup_dir, exist_ok=True)
            for item in os.listdir(self.primitive_cell_dir):
                shutil.move(os.path.join(self.primitive_cell_dir, item), os.path.join(backup_dir, item))
        
        os.makedirs(self.primitive_cell_dir, exist_ok=True)
        CONTCAR_files = [f for f in os.listdir(self.phonopy_dir) if f.startswith('CONTCAR_')]
        # 改变工作目录，便于运行shell命令进行phonopy对称性检查和原胞与常规胞的生成
        os.chdir(self.phonopy_dir)
        logging.info('Start running phonopy processing ...')
        try:
            for new_CONTCAR_filename in CONTCAR_files:
                # 按顺序处理POSCAR文件，首先复制一份无数字后缀的POSCAR文件
                shutil.copy(f'{self.phonopy_dir}/{new_CONTCAR_filename}', f'{self.phonopy_dir}/POSCAR')
                with open(f'{self.primitive_cell_dir}/phonopy.log', 'a') as log:
                    # 使用phonopy模块处理POSCAR结构文件，获取对称化的原胞和常规胞。
                    # 应用晶体的对称操作优化后的原胞可以最好地符合晶体的对称性，减少后续优化计算的复杂性。
                    log.write(f'\nProcessing file: {new_CONTCAR_filename}\n')
                    result = subprocess.run(['nohup', 'phonopy', '--symmetry', 'POSCAR'], stderr=subprocess.STDOUT)
                    log.write(f'Finished processing file: {new_CONTCAR_filename} with return code: {result.returncode}\n')
                # 将phonopy生成的PPOSCAR（对称化原胞）和BPOSCAR（对称化常规胞）放到对应的文件夹中，并将文件名改回POSCAR_index
                shutil.move(f'{self.phonopy_dir}/PPOSCAR', f'{self.primitive_cell_dir}/{new_CONTCAR_filename}')
                # 复制对应的OUTCAR文件到primitive_cell目录下
                density_number = new_CONTCAR_filename.split("CONTCAR_")[1]
                new_OUTCAR_filename = f'OUTCAR_{density_number}'
                shutil.copy(f'{self.phonopy_dir}/{new_OUTCAR_filename}', f'{self.primitive_cell_dir}/{new_OUTCAR_filename}')
            shutil.copy(
                f"{self.phonopy_dir}/mlp_density_energy.csv",
                f"{self.primitive_cell_dir}/mlp_density_energy.csv",
            )
            for_vasp_opt_dir = os.path.join(self.base_dir, '3_for_vasp_opt')
            if os.path.exists(for_vasp_opt_dir):
                shutil.rmtree(for_vasp_opt_dir)
            shutil.copytree(self.primitive_cell_dir, for_vasp_opt_dir)
            logging.info('The phonopy processing has been completed!!\nThe symmetrized primitive cells have been saved in POSCAR format to the primitive_cell folder.\nThe output content of phonopy has been saved to the phonopy.log file in the same directory.')
            # 在 phonopy 成功进行对称化处理后，删除 2_mlp_optimized/max_density 文件夹以节省空间
            shutil.rmtree(self.phonopy_dir)
        except FileNotFoundError:
            logging.error(
                "There are no CONTCAR structure files after screening.\nPlease check if the ions correspond to the crystals and adjust the screening criteria"
            )
            raise FileNotFoundError(
                "There are no CONTCAR structure files after screening.\nPlease check if the ions correspond to the crystals and adjust the screening criteria"
            )
