#!/bin/bash
#SBATCH -J yz_vasp
#SBATCH -p v6_384
#SBATCH -N 1
#SBATCH -n 96

source /public1/soft/modules/module.sh
module load mpi/oneAPI/2021.2
export PATH=/public1/home/sch9797/software-sch9797/bin-544:$PATH
srun --mpi=pmi2 vasp_std > vasp.log 2>&1
# mpirun -np 96 vasp_std
