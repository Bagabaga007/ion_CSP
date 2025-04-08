# CHANGELOG

## 1. Standardization
The entire Python project has been standardized, and a fully automated workflow from ion combination to high-precision crystal structure can be achieved through main_CSP.py, which only requires the initial input of the working path parameter.
## 2. StatusLogger Class
By using the StatusLogger class, the workflow's step execution status is recorded in .log and .yaml files. With the detection of content of .yaml files, the next work step can be automatically executed based on the progress of each step.
## 3. Default config paramters
Add default configuration parameters for each workflow step, and merge them with the user configuration parameters of the actual work path, and record them in the corresponding log file. Significantly reduced the number of parameters that users must provide, providing templates for both simple and complete config.yaml parameter files, as well as annotations for each parameter
## 4. Remote VASP calculation handling
In the VASP optimization step, the initial self-made server connection module was abandoned, and a method for performing VASP step-by-step calculation and processing on CPU servers was adapted to the dpdispatcher package. 
## 5. YAML Support for machine and resources
For machines and resources, support for YAML file types has been added, with sufficient annotations added to facilitate users to fill in according to their own situation.
## 6. Shell script logic optimization for VASP
The shell script logic for VASP step-by-step optimization has been adjusted as a whole, and the monitor mechanism has been eliminated. By adapting the tasks of the dpdispatcher package, more efficient operation has been achieved.
## 7. Automated POTCAR generation
Fixed a bug where the universal POTCAR is not applicable to certain systems composed only of a few elements of C/H/O/N. Now, the generation of POTCAR is achieved by automatically concatenating the corresponding element POTCAR based on the element information of POSCAR.
## 8. Bad Gaussian results handling
In the file processing section of Gaussian optimization, the ability to handle and summarize files that have failed optimization has been added.
