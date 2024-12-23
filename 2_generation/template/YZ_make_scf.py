import os
import shutil
import time
import logging
import subprocess
from warnings import filterwarnings
from ase.io import read
from pyxtal import pyxtal
from pyxtal.msg import Comp_CompatibilityError, Symm_CompatibilityError
from dpdispatcher import Machine, Resources, Task, Submission

filterwarnings('ignore')

class CrystalGenerator:
    def __init__(self, base_dir:str, species:list, ion_numbers:list, conventional=False):
        """
        Initialize the class based on the provided ionic crystal composition structure files and corresponding composition numbers。
        """
        # 获取当前脚本的路径以及同路径下离子晶体组分的结构文件, 并将这一路径作为工作路径来避免可能的错误
        self.base_dir = base_dir
        os.chdir(self.base_dir)
        self.species = species
        self.ion_numbers = ion_numbers
        self.species_dirs = []
        ion_atomss, species_atomss = [], []
        # 读取离子晶体各组分的原子数，并在日志文件中记录
        for ion, number in zip(species, ion_numbers):
            species_dir = os.path.join(base_dir, ion)
            self.species_dirs.append(species_dir)
            species_atom = len(read(species_dir))
            species_atomss.append(species_atom)
            species_atoms = species_atom * number
            ion_atomss.append(species_atoms)
        self.cell_atoms = sum(ion_atomss)
        logging.info(f'The components of ions {species} in the ionic crystal are {ion_numbers}')
        logging.info(f'The number of atoms for each ion is: {species_atomss}, and the total number of atoms is {self.cell_atoms}')
        # 创建脚本同路径下用于存放原胞和常规胞的文件夹
        self.POSCAR_dir = os.path.join(base_dir, 'POSCAR_Files')
        os.makedirs(self.POSCAR_dir, exist_ok=True)  # 如果目录不存在，则创建POSCAR_Files文件夹
        self.primitive_cell_dir = os.path.join(base_dir, 'primitive_cell')
        os.makedirs(self.primitive_cell_dir, exist_ok=True)
        # 根据conventional参数确定是否要保留conventional_cell结构文件
        self.conventional = conventional
        if self.conventional:
            self.conventional_cell_dir = os.path.join(base_dir, 'conventional_cell')
            os.makedirs(self.conventional_cell_dir, exist_ok=True)

        # 定义要检查的文件列表
        self.required_files = ['YZ_run_opt.py', 'model.pt']
        # 遍历文件列表，检查所需的每个文件是否存在
        for file in self.required_files:
            file_path = os.path.join(base_dir, file)
            if not os.path.exists(file_path):
                error_message = (f"\nWrong: The required file {file} does not exist in the current directory\nRequired files include: {self.required_files}.")
                logging.error(error_message)
                raise FileNotFoundError(error_message)
            else:
                # 准备dpdispatcher运行所需的文件，将其复制到primitive_cell文件夹中
                if file != 'model.pt':  # 由于model.pt势函数文件过大，不进行反复复制，防止复制过程出错
                    shutil.copy(file_path, self.primitive_cell_dir)
        logging.info('The necessary files are fully prepared.')

    def _sequentially_read_files(self, dir, prefix_name='POSCAR_'):
        """
        Private method: 
        Extract numbers from file names, convert them to integers, sort them by sequence, and return a list containing both indexes and file names
        """
        # 获取dir文件夹中所有以prefix_name开头的文件，在此实例中为POSCAR_
        files = [f for f in os.listdir(dir) if f.startswith(prefix_name)]
        file_index_pairs = []
        for filename in files:
            index_part = filename[len(prefix_name):]  # 选取去除前缀'POSCAR_'的数字
            if index_part.isdigit():  # 确保剩余部分全是数字
                index = int(index_part)
                file_index_pairs.append((index, filename))
        file_index_pairs.sort(key=lambda pair: pair[0])
        return file_index_pairs

    def generate_structures(self, N=100, is_test=False):
        """
        Based on the provided ion species and corresponding numbers, use pyxtal to randomly generate ion crystal structures based on crystal space groups.
        """
        count_POSCAR = 0  # 用于给生成的POSCAR文件计数
        if is_test:  # 如果参数is_test为True，则只测试1到15的空间群，以节约测试时间
            space_groups = 16
        else:  # 否则搜索所有的230个空间群
            space_groups = 231
        for space_group in range(1, space_groups):  # 按顺序搜索晶体空间群
            logging.info(f'Space group: {space_group}')
            count_group = 0
            try:
                for i in range(N):  # 参数N确定对每个空间群所要生成的POSCAR结构文件个数
                    # 调用pyxtal类
                    pyxtal_structure = pyxtal(molecular=True)
                    # 根据阴阳离子结构文件与对应的配比以及空间群信息随机生成离子晶体，N取100以上
                    pyxtal_structure.from_random(dim=3, group=space_group, species=self.species_dirs, numIons=self.ion_numbers, conventional=False)
                    # 生成POSCAR_n文件
                    POSCAR_path = os.path.join(self.POSCAR_dir, f'POSCAR_{count_POSCAR}')
                    pyxtal_structure.to_file(POSCAR_path, fmt='poscar')
                    count_POSCAR += 1
                    count_group += 1
                logging.info(f' {count_group} POSCAR generated.')
            except (RuntimeError, Comp_CompatibilityError, Symm_CompatibilityError) as e:
                # 捕获对于某一空间群生成结构的运行时间过长、组成兼容性错误、对称性兼容性错误等异常，使结构生成能够完全进行而不中断
                logging.error(f'Generating structure error: {e}')
        logging.info(f'Using pyxtal.from_random, {count_POSCAR} ion crystal structures were randomly generated based on crystal space groups.')

    def phonopy_processing(self):
        """
        Use phonopy to check and generate symmetric primitive cells, reducing the complexity of subsequent optimization calculations, and preventing pyxtal.from_random from generating double proportioned supercells. 
        """
        POSCAR_file_index_pairs = self._sequentially_read_files(self.POSCAR_dir, prefix_name='POSCAR_')
        # 改变工作目录为POSCAR_Files，便于运行shell命令进行phonopy对称性检查和原胞与常规胞的生成
        os.chdir(self.POSCAR_dir)
        for _, filename in POSCAR_file_index_pairs:
            # 按顺序处理POSCAR文件，首先复制一份无数字后缀的POSCAR文件
            shutil.copy(f'{self.POSCAR_dir}/{filename}', f'{self.POSCAR_dir}/POSCAR')
            with open(f'{self.primitive_cell_dir}/phonopy.log', 'a') as log:
                # 使用phonopy模块处理POSCAR结构文件，获取对称化的原胞和常规胞。
                # 应用晶体的对称操作优化后的原胞可以最好地符合晶体的对称性，减少后续优化计算的复杂性。
                log.write(f'\nProcessing file: {filename}\n')
                result = subprocess.run(['phonopy', '--symmetry', 'POSCAR'], stderr=subprocess.STDOUT)
                log.write(f'Finished processing file: {filename} with return code: {result.returncode}\n')
            # 将phonopy生成的PPOSCAR（对称化原胞）和BPOSCAR（对称化常规胞）放到对应的文件夹中，并将文件名改回POSCAR_index
            shutil.move(f'{self.POSCAR_dir}/PPOSCAR', f'{self.primitive_cell_dir}/{filename}')
            cell_atoms = len(read(f'{self.primitive_cell_dir}/{filename}'))
            # 检查生成的POSCAR中的原子数，如果不匹配则删除该POSCAR并在日志中记录
            if cell_atoms != self.cell_atoms:
                os.remove(f'{self.primitive_cell_dir}/{filename}')
                logging.error(f'The number of atoms in {filename} does not match!! Original: {self.cell_atoms} vs Generated {cell_atoms}')
            if self.conventional:
                shutil.move(f'{self.POSCAR_dir}/BPOSCAR', f'{self.conventional_cell_dir}/{filename}')
        # 移除最后复制多出来的POSCAR文件和phonopy_symcells.yaml
        os.remove(f'{self.POSCAR_dir}/phonopy_symcells.yaml')
        os.remove(f'{self.POSCAR_dir}/POSCAR')
        if not self.conventional:
            os.remove(f'{self.POSCAR_dir}/BPOSCAR')
        logging.info(f'The phonopy processing has been completed!!\nThe symmetrized primitive cells have been saved in POSCAR format to the primitive_cell folder.\nThe output content of phonopy has been saved to the phonopy.log file in the same directory.')
        
    def prepare_and_submit(self, machine_file, resources_file, task_alloc=4):
        """
        Based on the dpdispatcher module, prepare and submit files for optimization on remote server.
        """
        # 调整工作目录，减少错误发生
        os.chdir(self.primitive_cell_dir)
        # 设置远程服务器上的python路径，读取machine.json和resources.json的参数
        python = '/HOME/scw7187/run/soft/miniforge3/envs/yz/bin/python'
        machine = Machine.load_from_json(f'{self.base_dir}/{machine_file}')
        resources = Resources.load_from_json(f'{self.base_dir}/{resources_file}')
        # 依次读取primitive_cell文件夹中的所有POSCAR文件和对应的序号
        primitive_cell_file_index_pairs = self._sequentially_read_files(self.primitive_cell_dir, prefix_name='POSCAR_')
        # 创建一个嵌套列表来存储每个GPU的任务并将文件平均依次分配给每个GPU
        # 例如：对于10个结构文件任务分发给4个GPU的情况，则4个GPU领到的任务分别[0, 4, 8], [1, 5, 9], [2, 6], [3, 7], 便于快速分辨GPU与作业的分配关系
        total_files = len(primitive_cell_file_index_pairs)
        gpu_jobs = [[] for _ in range(task_alloc)]
        for index, _ in primitive_cell_file_index_pairs:
            gpu_index = index % task_alloc
            gpu_jobs[gpu_index].append(index)
        task_list = []
        for pop in range(task_alloc):
            remote_task_dir = f'data/pop{pop}'
            command = f'{python} YZ_run_opt.py > output_dp.log 2>&1'
            forward_files = ['YZ_run_opt.py']
            backward_files = ['output_dp.log'] 
            # 将YZ_run_opt.py和input.dat复制一份到task_dir下
            task_dir = os.path.join(self.primitive_cell_dir, f'data/pop{pop}')
            os.makedirs(task_dir, exist_ok=True)
            for file in forward_files:
                shutil.copyfile(f'{self.primitive_cell_dir}/{file}', f'{task_dir}/{file}')
            for job_i in gpu_jobs[pop]:
                # 将分配好的POSCAR文件添加到对应的上传文件中
                forward_files.append(f'POSCAR_{job_i}')
                # 每个POSCAR文件在优化后都取回对应的CONTCAR和OUTCAR输出文件
                backward_files.append(f'CONTCAR_{job_i}')
                backward_files.append(f'OUTCAR_{job_i}')
                shutil.copyfile(f'{self.primitive_cell_dir}/POSCAR_{job_i}', f'{task_dir}/POSCAR_{job_i}')
                shutil.copyfile(f'{self.primitive_cell_dir}/POSCAR_{job_i}', f'{task_dir}/ori_POSCAR_{job_i}')

            task = Task(
                command=command,
                task_work_path=remote_task_dir,
                forward_files=forward_files,
                backward_files=backward_files
            )
            task_list.append(task)

        submission = Submission(
            work_base=os.getcwd(),
            machine=machine,
            resources=resources,
            task_list=task_list,
            forward_common_files=[f'{self.base_dir}/model.pt']
        )
        submission.run_submission()

        # 创建用于存放优化后文件的 optimized 目录   
        optimized_dir = os.path.join(self.base_dir, 'optimized')
        os.makedirs(optimized_dir, exist_ok=True)
        for pop in range(task_alloc):
            # 从传回 primitive_cell 目录下的 data/pop 文件夹中将结果文件取到 optimized 目录
            task_dir = os.path.join(self.primitive_cell_dir, f'data/pop{pop}')
             # 按照给定的 POSCAR 结构文件按顺序读取 CONTCAR 和 OUTCAR 文件并复制
            task_file_index_pairs = self._sequentially_read_files(task_dir, prefix_name='POSCAR_')
            for index, _ in task_file_index_pairs:
                shutil.copyfile(f'{task_dir}/CONTCAR_{index}', f'{optimized_dir}/CONTCAR_{index}')
                shutil.copyfile(f'{task_dir}/OUTCAR_{index}', f'{optimized_dir}/OUTCAR_{index}')
        # 完成后删除不必要的运行文件并记录优化完成的信息
        os.remove(f'{self.primitive_cell_dir}/YZ_run_opt.py')
        logging.info('Batch optimization completed!!!')

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
    base_dir = os.path.dirname(__file__)  
    species = ['N5.gjf', 'CEDPIO.gjf', 'TUQKUO.gjf']
    ion_numbers = [2, 2, 2]
    generator = CrystalGenerator(base_dir, species=species, ion_numbers=ion_numbers)
    generator.generate_structures(N=500, is_test=False)
    generator.phonopy_processing()
    generator.prepare_and_submit(machine_file='machine_scw7187.json', resources_file='resources_scw7187.json', task_alloc=4)
    

if __name__ == '__main__':
    main()
