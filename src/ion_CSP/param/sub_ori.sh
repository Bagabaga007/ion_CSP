#!/bin/bash

set -uo pipefail
shopt -s nullglob

BASE_DIR="."
ROOT_DIR="$(pwd)"
failures=0

rough_max_cycles="${ION_CSP_ROUGH_MAX_CYCLES:-2}"
fine_max_cycles="${ION_CSP_FINE_MAX_CYCLES:-3}"
rough_pressure_tolerance="${ION_CSP_ROUGH_PRESSURE_TOLERANCE_KB:-5.0}"
fine_pressure_tolerance="${ION_CSP_FINE_PRESSURE_TOLERANCE_KB:-1.0}"
volume_nsw="${ION_CSP_VOLUME_NSW:-3}"

if [[ ! -f INCAR_1 || ! -f INCAR_2 ]]; then
    echo "Required INCAR_1 or INCAR_2 is missing." >&2
    exit 1
fi
if [[ -z "${DPDISPATCHER_CPU_PER_NODE:-}" ]]; then
    echo "DPDISPATCHER_CPU_PER_NODE is not set." >&2
    exit 1
fi
if ! [[ "$rough_max_cycles" =~ ^[1-9][0-9]*$ && "$fine_max_cycles" =~ ^[1-9][0-9]*$ && "$volume_nsw" =~ ^[1-9][0-9]*$ ]]; then
    echo "Macro-cycle counts and ION_CSP_VOLUME_NSW must be positive integers." >&2
    exit 1
fi
if ! [[ "$rough_pressure_tolerance" =~ ^[0-9]+([.][0-9]+)?$ && "$fine_pressure_tolerance" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "Pressure tolerances must be non-negative numbers in kB." >&2
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

prepare_incar() {
    local source_incar="$1"
    local output_incar="$2"
    local isif="$3"
    local nsw="${4:-}"

    if [[ -n "$nsw" ]]; then
        sed -E             -e "s/^[[:space:]]*ISIF[[:space:]]*=.*/ISIF = ${isif}/"             -e "s/^[[:space:]]*NSW[[:space:]]*=.*/NSW = ${nsw}/"             "$source_incar" > "$output_incar"
    else
        sed -E             -e "s/^[[:space:]]*ISIF[[:space:]]*=.*/ISIF = ${isif}/"             "$source_incar" > "$output_incar"
    fi

    grep -Eiq '^[[:space:]]*ISIF[[:space:]]*=' "$output_incar" ||
        printf 'ISIF = %s\n' "$isif" >> "$output_incar"
    if [[ -n "$nsw" ]]; then
        grep -Eiq '^[[:space:]]*NSW[[:space:]]*=' "$output_incar" ||
            printf 'NSW = %s\n' "$nsw" >> "$output_incar"
    fi
}

read_incar_pstress() {
    awk '
        BEGIN { IGNORECASE=1; value=0 }
        /^[[:space:]]*PSTRESS[[:space:]]*=/ {
            line=$0
            sub(/[#!].*$/, "", line)
            split(line, fields, "=")
            gsub(/[[:space:]]/, "", fields[2])
            if (fields[2] != "") value=fields[2]
        }
        END { print value }
    ' "$1"
}

read_outcar_pressure() {
    awk '
        /external pressure[[:space:]]*=/ { value=$4 }
        END {
            if (value == "") exit 1
            print value
        }
    ' "$1"
}

pressure_is_within_tolerance() {
    awk -v pressure="$1" -v target="$2" -v tolerance="$3" '
        BEGIN {
            delta=pressure-target
            if (delta < 0) delta=-delta
            exit !(delta <= tolerance)
        }
    '
}

promote_stage_result() {
    local source_dir="$1"
    local stage_dir="$2"
    local name

    for name in CONTCAR OUTCAR OSZICAR XDATCAR vasprun.xml vasp.log INCAR; do
        if [[ -f "${source_dir}/${name}" ]]; then
            cp "${source_dir}/${name}" "${stage_dir}/${name}"
        fi
    done
}

run_vasp_volume_step() {
    local stage_dir="$1"
    local stage="$2"
    local exit_code=0
    local reason=""

    (
        cd "$stage_dir" || exit 125
        mpirun -n "$DPDISPATCHER_CPU_PER_NODE" vasp_std > vasp.log 2>&1
    ) || exit_code=$?

    if (( exit_code != 0 )); then
        reason="vasp_exit_${exit_code}"
    elif [[ ! -s "${stage_dir}/OUTCAR" || ! -s "${stage_dir}/CONTCAR" ]]; then
        reason="missing_or_empty_OUTCAR_or_CONTCAR"
    elif grep -Eiq 'ZBRENT: fatal error|I REFUSE TO CONTINUE WITH THIS SICK JOB|VERY BAD NEWS|segmentation fault' "${stage_dir}/OUTCAR"; then
        reason="fatal_VASP_marker"
    elif ! grep -Fiq 'General timing and accounting informations for this job' "${stage_dir}/OUTCAR"; then
        reason="incomplete_OUTCAR"
    else
        record_stage_status "$stage_dir" "$stage" "STEP_COMPLETE" "bounded_volume_step" "$exit_code"
        return 0
    fi

    record_stage_status "$stage_dir" "$stage" "FAILURE" "$reason" "$exit_code"
    return 1
}

run_constrained_stage() {
    local stage_dir="$1"
    local base_incar="$2"
    local stage="$3"
    local max_cycles="$4"
    local pressure_tolerance="$5"
    local history="${stage_dir}/CONSTRAINED_RELAXATION_HISTORY.tsv"
    local cycles_dir="${stage_dir}/constrained_cycles"
    local current_poscar="${stage_dir}/POSCAR"
    local target_pressure
    local cycle
    local ion_dir
    local volume_dir
    local pressure

    target_pressure="$(read_incar_pstress "$base_incar")"
    mkdir -p "$cycles_dir"
    printf 'cycle\tion_status\tpressure_kB\ttarget_kB\tvolume_status\n' > "$history"

    for ((cycle=1; cycle<=max_cycles; cycle++)); do
        ion_dir="${cycles_dir}/cycle_${cycle}/ions"
        mkdir -p "$ion_dir"
        cp "$current_poscar" "${ion_dir}/POSCAR"
        cp "${stage_dir}/POTCAR" "${ion_dir}/POTCAR"
        prepare_incar "$base_incar" "${ion_dir}/INCAR" 2

        if ! run_vasp_stage "$ion_dir" "${stage}_ions_cycle_${cycle}"; then
            promote_stage_result "$ion_dir" "$stage_dir"
            printf '%s\tFAILURE\tNA\t%s\tNOT_RUN\n' "$cycle" "$target_pressure" >> "$history"
            record_stage_status "$stage_dir" "$stage" "FAILURE" "ion_relaxation_failed_cycle_${cycle}" "1"
            return 1
        fi

        if ! pressure="$(read_outcar_pressure "${ion_dir}/OUTCAR")"; then
            promote_stage_result "$ion_dir" "$stage_dir"
            printf '%s\tSUCCESS\tNA\t%s\tNOT_RUN\n' "$cycle" "$target_pressure" >> "$history"
            record_stage_status "$stage_dir" "$stage" "FAILURE" "pressure_missing_cycle_${cycle}" "1"
            return 1
        fi

        if pressure_is_within_tolerance "$pressure" "$target_pressure" "$pressure_tolerance"; then
            promote_stage_result "$ion_dir" "$stage_dir"
            printf '%s\tSUCCESS\t%s\t%s\tNOT_NEEDED\n' "$cycle" "$pressure" "$target_pressure" >> "$history"
            record_stage_status "$stage_dir" "$stage" "SUCCESS" "force_and_pressure_converged_cycle_${cycle}" "0"
            return 0
        fi

        if (( cycle == max_cycles )); then
            promote_stage_result "$ion_dir" "$stage_dir"
            printf '%s\tSUCCESS\t%s\t%s\tMAX_CYCLES\n' "$cycle" "$pressure" "$target_pressure" >> "$history"
            record_stage_status "$stage_dir" "$stage" "FAILURE" "pressure_not_converged_after_${max_cycles}_cycles" "1"
            return 1
        fi

        volume_dir="${cycles_dir}/cycle_${cycle}/volume"
        mkdir -p "$volume_dir"
        cp "${ion_dir}/CONTCAR" "${volume_dir}/POSCAR"
        cp "${stage_dir}/POTCAR" "${volume_dir}/POTCAR"
        prepare_incar "$base_incar" "${volume_dir}/INCAR" 7 "$volume_nsw"
        if ! run_vasp_volume_step "$volume_dir" "${stage}_volume_cycle_${cycle}"; then
            promote_stage_result "$ion_dir" "$stage_dir"
            printf '%s\tSUCCESS\t%s\t%s\tFAILURE\n' "$cycle" "$pressure" "$target_pressure" >> "$history"
            record_stage_status "$stage_dir" "$stage" "FAILURE" "volume_step_failed_cycle_${cycle}" "1"
            return 1
        fi

        printf '%s\tSUCCESS\t%s\t%s\tSTEP_COMPLETE\n' "$cycle" "$pressure" "$target_pressure" >> "$history"
        current_poscar="${volume_dir}/CONTCAR"
    done
    return 1
}

contcars=("$BASE_DIR"/CONTCAR_*)
if (( ${#contcars[@]} == 0 )); then
    echo "No CONTCAR_* input files were found." >&2
    exit 1
fi

for contcar in "${contcars[@]}"; do
    sample="${contcar##*/CONTCAR_}"
    sample_dir="${BASE_DIR}/${sample}"
    mkdir -p "$sample_dir"
    cp "$contcar" "${sample_dir}/POSCAR"
    if ! create_potcar_from_poscar "${sample_dir}/POSCAR" "${sample_dir}/POTCAR"; then
        record_stage_status "$sample_dir" "rough" "FAILURE" "POTCAR_preparation_failed" "1"
        failures=$((failures + 1))
        continue
    fi
    if ! run_constrained_stage "$sample_dir" "${ROOT_DIR}/INCAR_1" "rough" "$rough_max_cycles" "$rough_pressure_tolerance"; then
        failures=$((failures + 1))
    fi
done

for sample_dir in "$BASE_DIR"/*; do
    [[ -d "$sample_dir" && -s "${sample_dir}/CONTCAR" && -s "${sample_dir}/POTCAR" ]] || continue
    mkdir -p "${sample_dir}/fine"
    cp "${sample_dir}/CONTCAR" "${sample_dir}/fine/POSCAR"
    cp "${sample_dir}/POTCAR" "${sample_dir}/fine/POTCAR"
    if ! run_constrained_stage "${sample_dir}/fine" "${ROOT_DIR}/INCAR_2" "fine" "$fine_max_cycles" "$fine_pressure_tolerance"; then
        failures=$((failures + 1))
    fi
done

if (( failures > 0 )); then
    printf 'VASP constrained stages completed with %d failed stage(s); inspect status and history files.\n' "$failures" >&2
else
    echo "All VASP constrained rough and fine stages converged in force and pressure."
fi

# Candidate-level numerical failures are returned as artifacts and filtered by
# the Python validation gate. Keep the dispatcher task successful so other
# candidates in the same batch are not resubmitted.
exit 0
