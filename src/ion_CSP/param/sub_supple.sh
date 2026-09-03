#!/bin/bash

set -uo pipefail
shopt -s nullglob

BASE_DIR="."
ROOT_DIR="$(pwd)"
failures=0

if [[ ! -f INCAR_3 ]]; then
    echo "Required INCAR_3 is missing." >&2
    exit 1
fi
if [[ -z "${DPDISPATCHER_CPU_PER_NODE:-}" ]]; then
    echo "DPDISPATCHER_CPU_PER_NODE is not set." >&2
    exit 1
fi

record_stage_status() {
    local stage_dir="$1"
    local stage="$2"
    local status="$3"
    local reason="$4"
    local exit_code="$5"
    {
        printf 'stage=%s\n' "$stage"
        printf 'status=%s\n' "$status"
        printf 'reason=%s\n' "$reason"
        printf 'exit_code=%s\n' "$exit_code"
    } > "${stage_dir}/ION_CSP_STAGE_STATUS"
}

create_potcar_from_poscar() {
    local poscar_file="$1"
    local output_file="$2"
    local element_line
    local element
    local potential
    local tmp_file="${output_file}.tmp"

    if ! read -r element_line < <(sed -n '6p' "$poscar_file"); then
        return 1
    fi
    if [[ -z "${element_line//[[:space:]]/}" ]]; then
        return 1
    fi

    : > "$tmp_file"
    read -r -a elements <<< "$element_line"
    for element in "${elements[@]}"; do
        potential="${ROOT_DIR}/POTCAR_${element}"
        if [[ ! -s "$potential" ]]; then
            printf 'Missing potential for element %s: %s\n' "$element" "$potential" >&2
            rm -f "$tmp_file"
            return 1
        fi
        cat "$potential" >> "$tmp_file"
    done
    mv "$tmp_file" "$output_file"
}

validate_vasp_stage() {
    local stage_dir="$1"
    local stage="$2"
    local exit_code="$3"
    local outcar="${stage_dir}/OUTCAR"
    local reason=""

    if (( exit_code != 0 )); then
        reason="vasp_exit_${exit_code}"
    elif [[ ! -s "$outcar" || ! -s "${stage_dir}/CONTCAR" ]]; then
        reason="missing_or_empty_OUTCAR_or_CONTCAR"
    elif grep -Eiq 'ZBRENT: fatal error|I REFUSE TO CONTINUE WITH THIS SICK JOB|VERY BAD NEWS|segmentation fault' "$outcar"; then
        reason="fatal_VASP_marker"
    elif ! grep -Fiq 'General timing and accounting informations for this job' "$outcar"; then
        reason="incomplete_OUTCAR"
    elif ! grep -Fiq 'reached required accuracy - stopping structural energy minimisation' "$outcar"; then
        reason="ionic_relaxation_not_converged"
    else
        record_stage_status "$stage_dir" "$stage" "SUCCESS" "converged" "$exit_code"
        return 0
    fi

    record_stage_status "$stage_dir" "$stage" "FAILURE" "$reason" "$exit_code"
    printf '%s stage failed in %s: %s\n' "$stage" "$stage_dir" "$reason" >&2
    return 1
}

run_vasp_stage() {
    local stage_dir="$1"
    local stage="$2"
    local exit_code=0

    (
        cd "$stage_dir" || exit 125
        mpirun -n "$DPDISPATCHER_CPU_PER_NODE" vasp_std > vasp.log 2>&1
    ) || exit_code=$?

    validate_vasp_stage "$stage_dir" "$stage" "$exit_code"
}

found=0
for sample_dir in "$BASE_DIR"/*; do
    [[ -d "$sample_dir" && -s "${sample_dir}/fine/CONTCAR" ]] || continue
    found=1
    mkdir -p "${sample_dir}/fine/final"
    fine_status_file="${sample_dir}/fine/ION_CSP_STAGE_STATUS"
    if [[ -f "$fine_status_file" ]]; then
        fine_status="$(awk -F= '$1 == "status" {print toupper($2)}' "$fine_status_file")"
        if [[ "$fine_status" != "SUCCESS" ]]; then
            record_stage_status "${sample_dir}/fine/final" "final" "FAILURE" "fine_stage_not_successful" "1"
            failures=$((failures + 1))
            continue
        fi
    fi
    cp "${sample_dir}/fine/CONTCAR" "${sample_dir}/fine/final/POSCAR"
    cp INCAR_3 "${sample_dir}/fine/final/INCAR"
    if ! create_potcar_from_poscar "${sample_dir}/fine/final/POSCAR" "${sample_dir}/fine/final/POTCAR"; then
        record_stage_status "${sample_dir}/fine/final" "final" "FAILURE" "POTCAR_preparation_failed" "1"
        failures=$((failures + 1))
        continue
    fi
    if ! run_vasp_stage "${sample_dir}/fine/final" "final"; then
        failures=$((failures + 1))
    fi
done

if (( found == 0 )); then
    echo "No fine/CONTCAR inputs were found for final relaxation." >&2
    exit 1
fi
if (( failures > 0 )); then
    printf 'Final VASP stages completed with %d failure(s); inspect ION_CSP_STAGE_STATUS files.\n' "$failures" >&2
else
    echo "All final VASP stages converged."
fi
exit 0
