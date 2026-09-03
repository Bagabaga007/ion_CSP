#!/bin/bash

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "${SCRIPT_DIR}/sub_ori.sh" || ! -f "${SCRIPT_DIR}/sub_supple.sh" ]]; then
    echo "sub_final.sh requires sibling sub_ori.sh and sub_supple.sh." >&2
    exit 1
fi

bash "${SCRIPT_DIR}/sub_ori.sh"
bash "${SCRIPT_DIR}/sub_supple.sh"
