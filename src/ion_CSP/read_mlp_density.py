import os
import csv
import shutil
import logging
import subprocess
from pathlib import Path
from ase.io.vasp import read_vasp
from ion_CSP.identify_molecules import identify_molecules, molecules_information


class ReadMlpDensity:
    def __init__(self, work_dir: Path, folder: str = "2_mlp_optimized"):
        """
        This class is designed to read and process MLP optimized files, specifically CONTCAR files, to calculate and sort their densities.
        The class also provides functionality to process these files using phonopy for symmetry analysis and primitive cell generation.

        :params
            work_dir: The working directory where the MLP optimized files are located.
        """
        # 获取脚本的当前目录
        self.base_dir = work_dir.resolve()
        # 寻找同一目录下的2_mlp_optimized文件夹
        self.folder_dir = self.base_dir / folder
        self.max_density_dir = self.folder_dir / "max_density"
        self.min_energy_dir = self.folder_dir / "min_energy"
        self.sort_value_dir = None
        self.primitive_cell_dir = self.folder_dir / "primitive_cell"
        print(f"Processing MLP CONTCARs in {self.folder_dir}")
        logging.info(f"Processing MLP CONTCARs in {self.folder_dir}")

    def _sequentially_read_files(self, directory: Path, prefix_name: str = "POSCAR_"):
        """
        Private method:
        Extract numbers from file names, convert them to integers, sort them by sequence, and return a list containing both indexes and file names
        """
        # 获取dir文件夹中所有以prefix_name开头的文件，在此实例中为POSCAR_
        files = [f for f in directory.iterdir() if f.name.startswith(prefix_name)]
        file_index_pairs = []
        for filename in files:
            index_part = filename.name[
                len(prefix_name) :
            ]  # 选取去除前缀'POSCAR_'的数字
            if index_part.isdigit():  # 确保剩余部分全是数字
                index = int(index_part)
                file_index_pairs.append((index, filename.name))
        file_index_pairs.sort(key=lambda pair: pair[0])
        return file_index_pairs

    def read_property_and_sort(
        self,
        n_screen: int = 10,
        sort_by: str = "density",
        molecules_screen: bool = True,
        detail_log: bool = False,
    ):
        """
        Obtain the atomic mass and unit cell volume from the optimized CONTCAR file, and obtain the ion crystal density. Finally, take n CONTCAR files with the highest density and save them separately for viewing.

        :params
            n_screen: The number of CONTCAR files with the highest density to be saved.
            sort_by: The property to sort by. Options: 'density' (default) or 'energy'.
            molecules_screen: If True, only consider ionic crystals with original ions.
            detail_log: If True, print detailed information about the molecules identified in the CONTCAR files.
        """
        if sort_by not in ["density", "energy"]:
            raise ValueError("sort_by parameter must be either 'density' or 'energy'.")
        # 获取所有以'CONTCAR_'开头的文件，并按数字顺序处理
        CONTCAR_file_index_pairs = self._sequentially_read_files(
            self.folder_dir, prefix_name="CONTCAR_"
        )
        # 存储密度、能量和文件名的列表
        property_index_list = []
        # 逐个处理文件
        for _, CONTCAR_filename in CONTCAR_file_index_pairs:
            atoms = read_vasp(self.folder_dir / CONTCAR_filename)
            molecules, molecules_flag, initial_information = identify_molecules(
                atoms, base_dir=self.base_dir
            )
            logging.info(molecules)
            logging.info(molecules_flag)
            logging.info(initial_information)
            if detail_log:
                molecules_information(molecules, molecules_flag, initial_information)
            # 跳过不符合条件的结构
            if molecules_screen and not molecules_flag:
                continue
            # 获取体积和质量，并计算密度
            atoms_volume = atoms.get_volume()  # 体积单位为立方埃（Å³）
            atoms_masses = sum(atoms.get_masses())  # 质量单位为原子质量单位(amu)
            # 1.66054这一转换因子用于将原子质量单位转换为克，以便在宏观尺度上计算密度g/cm³
            density = 1.66054 * atoms_masses / atoms_volume

            # 保留 CONTCAR 的序数信息，方便回推检查
            number = CONTCAR_filename.split("_")[1]
            OUTCAR_path = self.folder_dir / f"OUTCAR_{number}"
            energy = None
            try:
                with OUTCAR_path.open("r") as mlp_out:
                    lines = mlp_out.readlines()
                    for line in lines:
                        if "TOTEN" in line:
                            values = line.split()
                            energy = round(float(values[-2]), 2)
                            break
            except FileNotFoundError:
                logging.error(
                    f"  Failed to parse TOTEN from {OUTCAR_path}, file not found."
                )
                energy = False

            # 存储属性
            property_index_list.append(
                {
                    "number": number,
                    "density": density,
                    "energy": energy,
                }
            )
        logging.info(property_index_list)
        # 根据排序属性进行排序
        if sort_by == "density":
            sorted_list = sorted(
                property_index_list,
                key=lambda x: x["density"]
                if x["density"] is not None
                else float("-inf"),
                reverse=True,
            )
        elif sort_by == "energy":
            sorted_list = sorted(
                property_index_list,
                key=lambda x: x["energy"] if x["energy"] is not None else float("inf"),
            )
        else:
            raise ValueError("sort_by parameter must be either 'density' or 'energy'.")

        # 筛选出有效的结构（有对应的排序属性值）
        valid_sorted_list = [item for item in sorted_list if item[sort_by] is not None]
        # 输出筛选结果
        if molecules_screen:
            print(f"Total optimized ionic crystals: {len(CONTCAR_file_index_pairs)}")
            logging.info(
                f"Total optimized ionic crystals: {len(CONTCAR_file_index_pairs)}"
            )
            print(
                f"Screened ionic crystals with original ions: {len(valid_sorted_list)}"
            )
            logging.info(
                f"Screened ionic crystals with original ions: {len(valid_sorted_list)}"
            )
            if len(valid_sorted_list) < n_screen:
                logging.warning(
                    f"Only {len(valid_sorted_list)} ionic crystals with original ions found, which is less than the requested {n_screen} structures to save."
                )
                raise ValueError(
                    f"Only {len(valid_sorted_list)} ionic crystals with original ions found, which is less than the requested {n_screen} structures to save."
                )
        # 设置排序结果保存目录
        if sort_by == "density":
            self.sort_value_dir = self.max_density_dir
        elif sort_by == "energy":
            self.sort_value_dir = self.min_energy_dir
        else:
            raise ValueError("sort_by parameter must be either 'density' or 'energy'.")
        # 将前n个最大密度的CONTCAR文件进行重命名并保存到max_density文件夹
        if self.sort_value_dir.exists():
            backup_dir = self.folder_dir / "backup" / self.sort_value_dir.name
            backup_dir.mkdir(parents=True, exist_ok=True)
            for item in self.sort_value_dir.iterdir():
                shutil.move(str(item), str(backup_dir / item))
        else:
            os.makedirs(self.sort_value_dir, exist_ok=True)

        # 保存前n个结构
        numbers, mlp_densities, mlp_energies = [], [], []
        for item in valid_sorted_list[:n_screen]:
            number = item["number"]
            density = item["density"]
            energy = item["energy"]

            # 根据排序属性决定文件名中的值
            if sort_by == "density":
                sort_value = f"{density:.3f}"
            elif sort_by == "energy":
                sort_value = f"{energy:.2f}"

            # 保留 CONTCAR 的序数信息，方便回推检查
            numbers.append(number)
            mlp_densities.append(f"{density:.3f}" if density else "N/A")
            mlp_energies.append(f"{energy:.2f}" if energy else "N/A")
            # 源文件名及其路径
            src_CONTCAR_filename = f"CONTCAR_{number}"
            src_OUTCAR_filename = f"OUTCAR_{number}"
            src_CONTCAR_path = self.folder_dir / src_CONTCAR_filename
            src_OUTCAR_path = self.folder_dir / src_OUTCAR_filename
            # 生成新文件名及其路径
            new_CONTCAR_filename = f"CONTCAR_{sort_value}_{number}"
            new_OUTCAR_filename = f"OUTCAR_{sort_value}_{number}"
            new_CONTCAR_path = self.sort_value_dir / new_CONTCAR_filename
            new_OUTCAR_path = self.sort_value_dir / new_OUTCAR_filename
            if src_CONTCAR_path.exists():
                shutil.copy(str(src_CONTCAR_path), str(new_CONTCAR_path))
            else:
                logging.error(f"  {src_CONTCAR_path} does not exist.")
                raise FileNotFoundError(f"  {src_CONTCAR_path} does not exist.")
            if src_OUTCAR_path.exists():
                shutil.copy(str(src_OUTCAR_path), str(new_OUTCAR_path))
            else:
                logging.error(f"  {src_OUTCAR_path} does not exist.")
                raise FileNotFoundError(f"  {src_OUTCAR_path} does not exist.")
            print(
                f"New CONTCAR and OUTCAR of {sort_value}_{number} are renamed and saved"
            )
            logging.info(
                f"New CONTCAR and OUTCAR of {sort_value}_{number} are renamed and saved"
            )

        csv_path = self.sort_value_dir / "mlp_density_energy.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            header = ["Number", "MLP_E", "MLP_Density"]
            writer.writerow(header)
            for number, energy, density in zip(numbers, mlp_energies, mlp_densities):
                writer.writerow([number, energy, density])

    def phonopy_processing_max_density(self, specific_directory: str = None):
        """
        Use phonopy to check and generate symmetric primitive cells, reducing the complexity of subsequent optimization calculations, and preventing pyxtal.from_random from generating double proportioned supercells.

        :params
            specific_directory: If specified, phonopy will process the files in this directory instead of the max_density directory.
            If not specified, it will process the files in the max_density directory.
        """
        if specific_directory:
            self.phonopy_dir = Path(specific_directory).resolve()
            self.primitive_cell_dir = self.phonopy_dir.parent / "primitive_cell"
        else:
            if self.sort_value_dir is None:
                raise ValueError(
                    "Please run read_property_and_sort method first to set the sort_value_dir."
                )
            self.phonopy_dir = self.sort_value_dir

        if self.primitive_cell_dir.exists():
            backup_dir = self.folder_dir / "backup" / "primitive_cell"
            backup_dir.mkdir(parents=True, exist_ok=True)
            for item in self.primitive_cell_dir.iterdir():
                shutil.move(str(self.primitive_cell_dir / item), str(backup_dir / item))
        self.primitive_cell_dir.mkdir(exist_ok=True)
        CONTCAR_files = [
            f for f in self.phonopy_dir.iterdir() if f.name.startswith("CONTCAR_")
        ]
        if not CONTCAR_files:
            logging.error(
                "No CONTCAR files found for phonopy processing. Please check screening criteria."
            )
            raise FileNotFoundError(
                "No CONTCAR files found for phonopy processing. Please check screening criteria."
            )
        # 运行命令进行phonopy对称性检查和原胞与常规胞的生成
        phonopy_log = self.primitive_cell_dir / "phonopy.log"
        logging.info("Start running phonopy processing ...")
        for new_CONTCAR_file in CONTCAR_files:
            logging.info(f"  Processing file: {new_CONTCAR_file.name}")
            poscar_path = self.phonopy_dir / "POSCAR"
            shutil.copy(str(new_CONTCAR_file), str(poscar_path))
            with phonopy_log.open("a") as log:
                log.write(f"\nProcessing file: {new_CONTCAR_file.name}\n")
                try:
                    result = subprocess.run(
                        ["phonopy", "--symmetry", "POSCAR"],
                        cwd=self.phonopy_dir,
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.STDOUT,
                    )
                    log.write(
                        f"Finished processing file: {new_CONTCAR_file.name} with return code: {result.returncode}\n"
                    )
                except subprocess.CalledProcessError as e:
                    log.write(
                        f"Error processing file: {new_CONTCAR_file.name}. Error message: {e}\n"
                    )
                    logging.error(
                        f"Error processing file: {new_CONTCAR_file.name}. Check phonopy.log for details."
                    )
                    continue

            # 将phonopy生成的PPOSCAR（对称化原胞）放到对应的文件夹中，并将文件名改回CONTCAR_index
            pposcar = self.phonopy_dir / "PPOSCAR"
            if pposcar.exists():
                shutil.move(str(pposcar), str(self.primitive_cell_dir / new_CONTCAR_file.name))
            else:
                logging.warning(f"PPOSCAR not generated for {new_CONTCAR_file.name}.")
            # 复制对应的OUTCAR文件到primitive_cell目录下
            sort_value_and_number = new_CONTCAR_file.name.split("CONTCAR_")[1]
            new_OUTCAR_filename = f"OUTCAR_{sort_value_and_number}"
            if (self.phonopy_dir / new_OUTCAR_filename).exists():
                shutil.copy(
                    str(self.phonopy_dir / new_OUTCAR_filename),
                    str(self.primitive_cell_dir / new_OUTCAR_filename),
                )
            else:
                logging.warning(
                    f"{new_OUTCAR_filename} not found for {new_CONTCAR_file.name}."
                )
            # 复制csv文件到primitive_cell目录下
            if (self.phonopy_dir / "mlp_density_energy.csv").exists():
                shutil.copy(
                    str(self.phonopy_dir / "mlp_density_energy.csv"),
                    str(self.primitive_cell_dir / "mlp_density_energy.csv"),
                )
            else:
                logging.warning(
                    f"mlp_density_energy.csv not found in {self.phonopy_dir}."
                )
            # 删除临时文件
            (self.phonopy_dir / "POSCAR").unlink(missing_ok=True)
            (self.phonopy_dir / "BPOSCAR").unlink(missing_ok=True)
            for_vasp_opt_dir = self.base_dir / "3_for_vasp_opt"
            if for_vasp_opt_dir.exists():
                shutil.rmtree(for_vasp_opt_dir)
            shutil.copytree(self.primitive_cell_dir, for_vasp_opt_dir)
            logging.info(
                "The phonopy processing has been completed!!\nThe symmetrized primitive cells have been saved in POSCAR format to the primitive_cell folder.\nThe output content of phonopy has been saved to the phonopy.log file in the same directory."
            )
