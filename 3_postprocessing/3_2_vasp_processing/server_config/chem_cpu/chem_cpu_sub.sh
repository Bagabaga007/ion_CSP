#!/bin/bash
#SBATCH -p phys
#SBATCH -N 1
#SBATCH -n 56

source /public/env/intel2021
export PATH=/public/software/vasp.6.3.0/bin:$PATH
srun --mpi=pmi2 vasp_std > vasp.log 2>&1
