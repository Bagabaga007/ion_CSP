#!/bin/bash
#SBATCH -p v6_384
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -c 8
#export PGI_FASTMATH_CPU=sandybridge
export COM_DIR=~/software-sch9797/g16
export g16root=$COM_DIR
export PATH=$g16root:$PATH
source $g16root/bsd/g16.profile
export GAUSS_SCRDIR=/public1/home/sch9797/software-sch9797/gaussian16-test/tmp
export GAUSS_EXEDIR=$g16root

# 读取输入参数
gjf_file=$1
base_name=$2

# 运行Gaussian
srun  g16 "$gjf_file"
formchk "${base_name}.chk"

