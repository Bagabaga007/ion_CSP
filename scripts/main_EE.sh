#!/bin/bash
WORK_DIR=$1

nohup python -m ion_CSP.run.main_EE $WORK_DIR > "${WORK_DIR}/main_EE_console.log" 2>&1 &
