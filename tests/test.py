import os
from paramiko import Transport
from dpdispatcher import Machine, Resources, Task, Submission

base_dir = os.path.dirname(__file__)
os.chdir(base_dir)
machine = Machine.load_from_json('chem_cpu_machine.json')
resources = Resources.load_from_json('chem_cpu_resources.json')

task = Task(
    command='pwd',
    task_work_path="./",
    forward_files=['example.txt']
    )

task_list = [task]
submission = Submission(
    work_base=base_dir,
    machine=machine,
    resources=resources,
    task_list=task_list,
)
submission.run_submission()
