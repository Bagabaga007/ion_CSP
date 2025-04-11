#!/bin/bash
WORK_DIR=$1

nohup python -m src.main_CSP $WORK_DIR > "${WORK_DIR}_CSP_console.log" 2>&1 &
