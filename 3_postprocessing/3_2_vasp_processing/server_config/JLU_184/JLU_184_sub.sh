#!/bin/bash
#BSUB -J yz_vasp
#BSUB -q normal
#BSUB -n 56
#BSUB -R 'span[ptile=56]'
#BSUB -o %J.out

source /data/env/inteloneapi2021
export PATH=/data/home/miwenhui/soft/vasp.6.3.0/bin:$PATH
mpirun -n 56 vasp_std > vasp.log 2>&1
