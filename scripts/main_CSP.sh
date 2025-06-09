#!/bin/bash
WORK_DIR=$1

nohup python -m run.main_CSP $WORK_DIR > "${WORK_DIR}/main_CSP_console.log" 2>&1 &
