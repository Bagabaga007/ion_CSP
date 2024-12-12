#!/bin/bash
#SBATCH -p v6_384
#SBATCH -N 1
#SBATCH -n 96
source /public1/soft/modules/module.sh
module load mpi/oneAPI/2021.2
export PATH=/public1/home/sch9797/software-sch9797/bin-544:$PATH
mpirun -np 96 vasp_std
