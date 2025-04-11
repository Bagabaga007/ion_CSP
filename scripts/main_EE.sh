#!/bin/bash
WORK_DIR=$1

nohup python -m src.main_EE $WORK_DIR > "${WORK_DIR}_EE_console.log" 2>&1 &
