# Changelog

## Latest Changes

### 1754cc7 (2025-06-30)

Update version number to 2.2.1, fix file copying logic in main_EE workflow, update mlp_opt.py to use MatterSimCalculator to develop calculation efficiency.

### d6abfb5 (2025-06-28)

Optimize the environment variable settings in mlp_opt.py to disable numpy's multithreading and ensure the stability of multi-threaded computation.

## V2.2.0

### 18b9eba (2025-06-28)

Optimize the file copying logic in vasp_processing to ensure that copying operations are only performed when the file exists; Update sorting parameters to support 'NC_ratio' and modify the relevant CSV file names accordingly.

### 14ffac2 (2025-06-27)

Update version number to 2.2.0, optimize file replication logic in VASP processing, add error handling to ensure errors are recorded when files do not exist, modify README and CHANGELOG to reflect the latest changes.

## V2.1.9

### 8ad4b47 (2025-06-27)

Modify the hooks of post-commit to use self-written python script.

### 30a2f56 (2025-06-27)

Optimize the file copying logic in VASP processing to ensure that copying operations are only performed when files exist

### 0acedac (2025-06-27)

Update the version number to 2.1.9, adjust the version detection error information in the task manager, optimize the error handling of multiple modules, optimize the EE workflow, make its operation and calling more stable, and work logic more rigorous.

## V2.1.8

### b534c0e (2025-06-23)

Update version number to 2.1.8, optimize error handling in code, and update multiple modules to improve readability and functionality.

### b68c9ac (2025-06-19)

Update dependencies, improve documentation, and enhance error handling

### 162ddc4 (2025-06-16)

Use git hooks to automatcally update CHANGELOG.md

### 80f0d24 (2025-06-16)

Merge branch 'main' of https://github.com/Bagabaga007/ion_CSP

### bf651e6 (2025-06-16)

Update GitHub Actions workflow to trigger builds upon release, modify the steps for updating CHANGELOG to ensure proper installation of dependencies, update version number to 2.1.6, and optimize the generation format of CHANGELOG.

### 5c3c950 (2025-06-16)

Update update_changelog.yml

### 346d034 (2025-06-16)

Update update_changelog.yml

### 2ea0ac4 (2025-06-16)

Introduce importlib.resourses in multiple modules to improve file path management;  Fix the module import path in the task manager.

## V2.1.5

### 934f0fa (2025-06-16)

Add dependency descriptions for the Scipy version;  Fixed missing example 2 in the examples section.

### 31087de (2025-06-16)

Update version to 2.1.5.

### 3edd170 (2025-06-16)

Added the function of automatically updating CHANGELOG; Import configuration and data files through importlib.resosourced to fix file read errors in the pypi distribution package.

## V2.1.4

### ad5c1d7 (2025-06-13)

Optimize the packaging behavior when building pypi distribution packages to make them easier to use

## V2.1.3

### d503641 (2025-06-11)

Updata version to 2.1.3

## V2.1.2

### 1befcf7 (2025-06-09)

Implement centralized management of version numbers and automatic synchronization of multiple configuration files to ensure consistency and reliability in the construction process.

## V2.1.1

### bde5189 (2025-06-09)

Refactoring the project structure, configuring packaging parameters, and modifying sub process call logic to address the issue of module path failure in PyPI distribution packages;  To modify VASP optimization steps in the workflow: 1  Rough optimization of limiting cell shape 2. Fine optimization of limiting cell shape 3. Final optimization without cell constraints

### bb0d853 (2025-05-30)

Added usage and configuration examples in the project root directory. Added permission items in the CI Build workflow to enhance security. Added parameters to the example resources.yaml file to specify JOB_NAME to optimize the readability of jobs.

### 80a3e3d (2025-05-23)

Add template configuration files and corresponding usage document in the config folder under the root directory

### 7f383b0 (2025-05-21)

Adjust the installation method of the code, use git clone and pip install - e. commands to install the source code, and continue to optimize the use of code installed using distribution packages in the future.  Correspondingly updated the version number and the documentation content of README.md.

### 1d0d5fc (2025-05-16)

Modification of conda package build yml file.

### 83f25cb (2025-05-16)

Modification of conda package build yml file.

### 5540449 (2025-05-16)

Remove the flake8 installation in conda package construction.

### 01af54c (2025-05-16)

Update information in README.md

### cb7dcb0 (2025-05-15)

Ignore the big-size dist files

### 1ff9020 (2025-05-13)

Update README.md and modified the yml file for github action of conda package build

### 01f2095 (2025-05-13)

Modified yml file for github actions

### 90bfd07 (2025-05-13)

Modified yml file for Github actions.

### 6f29d21 (2025-05-13)

After machine learning potential optimization and structure filtering, CSV files were added to summarize the sequence number, density, and energy of the filtered structures, making it easier to visualize the interface.

### f2a8abc (2025-05-09)

Modified the yml file for github actions

### b5d1bcd (2025-05-09)

Merge branch 'main' of https://github.com/Bagabaga007/ion_CSP

### bfec475 (2025-05-09)

Significantly optimized the program logic of the interactive entrance and fixed a large number of potential bugs. Integrate pagination display function to reduce code duplication. Add unit testing modules for this interactive entry program.

### 30c455b (2025-04-25)

Create SECURITY.md

### 070c86f (2025-04-25)

Add unit testing module for task_manager.

### 72ccfad (2025-04-25)

Remove torchvision package dependencies to allow pip to attempt to solve the dependency conflict.

### 049d915 (2025-04-25)

Fix the bug in environment.yml file

### 3866521 (2025-04-25)

Adjust the depencies installation order

### ff92e05 (2025-04-25)

Change some certain packages installation way to pip installation.

### 24832ea (2025-04-25)

Remove invalid channel and adapt the mixed installation method.

### c2a01ee (2025-04-25)

Update the corresponding information yml to resolve the conda environment configuration actions in Github

### 0675303 (2025-04-25)

Add conda environment configuration

### b91ee5c (2025-04-25)

Merge branch 'main' of https://github.com/Bagabaga007/ion_CSP

### 15c4628 (2025-04-25)

Significantly optimized the logging system and task management of the interactive program entrance.  The specific functions implemented include: real-time process monitoring (PID tracking), automatic symbolic linking of log files, process security termination and resource cleaning, log pagination browsing (10 entries/page), module filtering (CSP/EE), and soft link parsing to display the actual path.  Greatly optimized the README.md file, providing bilingual support in both Chinese and English that is more in line with Python project specifications.

### 7b1752b (2025-04-23)

Create python-package-conda.yml

### 0cf8d07 (2025-04-23)

Merge branch 'main' of https://github.com/Bagabaga007/ion_CSP

### ebf54e3 (2025-04-23)

Unified the interactive interface between shell and Python versions, and uploaded distribution files to Pypi

### 1cbba7e (2025-04-23)

Create python-publish.yml

### 98f7382 (2025-04-22)

Added Python version of interactive main function entry and corresponding app files.

### f034fbe (2025-04-21)

1. Improve the dockerfile to ensure smooth operation inside the container; 2. Added a terminal interactive entrance for the program, supporting both Linux terminals and Docker environments, supporting runtime experience evaluation and ion crystal structure prediction modules, and able to capture the PID of Python programs and link logs for easy monitoring of task status and results, as well as task killing. 3. Optimize the task step logic of the EE module to avoid possible redundant Gaussian calculations, and provide batch update parameters for the config. yaml configuration file in the combined folder. 4. Generate CSV files for echo information during the structure generation stage, saving the structure generation information of each space group and the exception types when pyxtal generation fails. At the same time, based on the results of phonopy symmetry processing, remove the double atom structure generated by pyxtal and update the CSV file information.

### d079b70 (2025-04-11)

After successfully completing the task using the dpdispatcher module, delete the corresponding folder for submission to save space.

### 9ddb0c4 (2025-04-11)

Change the version number to 2.0.0.

### c367bc3 (2025-04-09)

Fully automated EE workflow

### f8e6c73 (2025-04-08)

Fully automated ion CSP workflow.

### c4400f0 (2025-04-02)

Delete 3_postprocessing directory

### 380eedb (2025-04-02)

Delete 2_generation directory

### 05931e7 (2025-04-02)

Delete 1_preparation directory

### a22b9e8 (2024-12-23)

ok

### 7f6d3b1 (2024-12-23)

ok

### c13cd0c (2024-12-20)

ok

### 1a2b2d5 (2024-12-19)

ok

### fd404eb (2024-12-19)

OK

### 2d8aafd (2024-12-17)

ok

### 7fbfd27 (2024-12-16)

ok

### a0b84fc (2024-12-16)

ok

### 9878e9d (2024-12-16)

ok

### b07065c (2024-12-16)

ok

### 68b6b0a (2024-12-16)

ok

### f6a30b0 (2024-12-14)

ok

### 0e434f6 (2024-12-14)

ok

### 2bef3e2 (2024-12-12)

committed

