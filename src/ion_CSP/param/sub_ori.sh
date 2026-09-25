#!/bin/bash

set -uo pipefail
shopt -s nullglob

BASE_DIR="."
ROOT_DIR="$(pwd)"
failures=0

# Rough and fine deliberately use ISIF=8 from INCAR_1/INCAR_2: keep the cell
# shape fixed while allowing the cell length/volume and ionic coordinates to
# relax. Rough uses damped dynamics (IBRION=3), fine uses RMM-DIIS
# (IBRION=1), avoiding the conjugate-gradient line search that triggers
# ZBRENT on the VASP 6.3.0 production build. These two stages are disposable
# preconditioners: retain any usable last frame and defer fatal/normal-output
# acceptance to final. Topology is checked later by Python.
require_ionic_convergence="${ION_CSP_REQUIRE_IONIC_CONVERGENCE:-0}"
vasp_max_attempts="${ION_CSP_VASP_MAX_ATTEMPTS:-3}"

if [[ ! -f INCAR_1 || ! -f INCAR_2 ]]; then
    echo "Required INCAR_1 or INCAR_2 is missing." >&2
    exit 1
fi
if [[ -z "${DPDISPATCHER_CPU_PER_NODE:-}" ]]; then
    echo "DPDISPATCHER_CPU_PER_NODE is not set." >&2
    exit 1
fi
if ! [[ "$vasp_max_attempts" =~ ^[1-9][0-9]*$ ]]; then
    echo "ION_CSP_VASP_MAX_ATTEMPTS must be a positive integer." >&2
    exit 1
fi
case "${require_ionic_convergence,,}" in
    0|1|false|true|no|yes) ;;
    *)
        echo "ION_CSP_REQUIRE_IONIC_CONVERGENCE must be 0/1 or false/true." >&2
        exit 1
        ;;
esac
case "${require_ionic_convergence,,}" in
    1|true|yes) require_ionic_convergence=1 ;;
    *) require_ionic_convergence=0 ;;
esac

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

archive_failed_vasp_attempt() {
    local stage_dir="$1"
    local attempt="$2"
    local exit_code="$3"
    local archive_dir="${stage_dir}/failed_attempts/attempt_${attempt}_exit_${exit_code}"
    local name

    mkdir -p "$archive_dir"
    for name in CONTCAR OUTCAR OSZICAR XDATCAR vasprun.xml vasp.log EIGENVAL DOSCAR IBZKPT PCDAT REPORT CHG CHGCAR WAVECAR ION_CSP_STAGE_STATUS; do
        if [[ -e "${stage_dir}/${name}" ]]; then
            mv "${stage_dir}/${name}" "${archive_dir}/${name}"
        fi
    done
}

run_vasp_command() {
    local stage_dir="$1"
    local stage="$2"
    local attempt
    local exit_code

    for ((attempt=1; attempt<=vasp_max_attempts; attempt++)); do
        exit_code=0
        (
            cd "$stage_dir" || exit 125
            mpirun -n "$DPDISPATCHER_CPU_PER_NODE" vasp_std > vasp.log 2>&1
        ) || exit_code=$?

        if (( exit_code != 255 || attempt == vasp_max_attempts )); then
            printf '%s\n' "$exit_code"
            return 0
        fi

        record_stage_status "$stage_dir" "$stage" "FAILURE" "transient_vasp_exit_255" "$exit_code"
        archive_failed_vasp_attempt "$stage_dir" "$attempt" "$exit_code"
        printf '%s transient MPI exit 255 in %s; retrying attempt %d/%d.\n' \
            "$stage" "$stage_dir" "$((attempt + 1))" "$vasp_max_attempts" >&2
    done
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

prepare_stage_incar() {
    local source_incar="$1"
    local output_incar="$2"
    local ibrion="$3"
    local smass="${4:-}"

    sed -E "s/^[[:space:]]*IBRION[[:space:]]*=.*/IBRION = ${ibrion}/" "$source_incar" > "$output_incar"
    grep -Eiq '^[[:space:]]*IBRION[[:space:]]*=' "$output_incar" ||
        printf 'IBRION = %s\n' "$ibrion" >> "$output_incar"
    if [[ -n "$smass" ]] && ! grep -Eiq '^[[:space:]]*SMASS[[:space:]]*=' "$output_incar"; then
        printf 'SMASS = %s\n' "$smass" >> "$output_incar"
    fi
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

write_relaxation_history() {
    local stage_dir="$1"
    local stage="$2"
    local status="$3"
    local outcar="${stage_dir}/OUTCAR"
    local pressure="NA"
    local ionic="NOT_REACHED"

    if [[ -s "$outcar" ]]; then
        pressure="$(read_outcar_pressure "$outcar" 2>/dev/null || printf 'NA')"
        grep -Fiq 'reached required accuracy - stopping structural energy minimisation' "$outcar" && ionic="REACHED"
    fi
    {
        printf 'stage\tstatus\tpressure_kB\tionic_convergence\n'
        printf '%s\t%s\t%s\t%s\n' "$stage" "$status" "$pressure" "$ionic"
    } > "${stage_dir}/CONSTRAINED_RELAXATION_HISTORY.tsv"
}

validate_vasp_stage() {
    local stage_dir="$1"
    local stage="$2"
    local exit_code="$3"
    local outcar="${stage_dir}/OUTCAR"
    local reason=""

    if [[ ! -s "$outcar" || ! -s "${stage_dir}/CONTCAR" ]]; then
        reason="missing_or_empty_OUTCAR_or_CONTCAR"
    elif [[ "$stage" == "rough" || "$stage" == "fine" ]]; then
        # These are intentionally disposable preconditioning stages. Keep any
        # usable CONTCAR/OUTCAR for the final restart, even when VASP reports a
        # fatal ZBRENT or exits non-zero after writing the last frame. The final
        # stage performs the real fatal/normal-termination acceptance check.
        if (( exit_code != 0 )); then
            reason="intermediate_output_accepted_vasp_exit_${exit_code}"
        elif grep -Eiq 'ZBRENT: fatal error|I REFUSE TO CONTINUE WITH THIS SICK JOB|VERY BAD NEWS|segmentation fault' "$outcar"; then
            reason="intermediate_output_accepted_fatal_VASP_marker"
        elif grep -Fiq 'reached required accuracy - stopping structural energy minimisation' "$outcar"; then
            reason="intermediate_output_accepted_ionic_converged"
        else
            reason="intermediate_output_accepted_relaxed_gate"
        fi
        record_stage_status "$stage_dir" "$stage" "SUCCESS" "$reason" "$exit_code"
        write_relaxation_history "$stage_dir" "$stage" "SUCCESS"
        return 0
    elif (( exit_code != 0 )); then
        reason="vasp_exit_${exit_code}"
    elif grep -Eiq 'ZBRENT: fatal error|I REFUSE TO CONTINUE WITH THIS SICK JOB|VERY BAD NEWS|segmentation fault' "$outcar"; then
        reason="fatal_VASP_marker"
    elif ! grep -Fiq 'General timing and accounting informations for this job' "$outcar"; then
        reason="incomplete_OUTCAR"
    elif (( require_ionic_convergence == 1 )) && ! grep -Fiq 'reached required accuracy - stopping structural energy minimisation' "$outcar"; then
        reason="ionic_relaxation_not_converged"
    else
        if grep -Fiq 'reached required accuracy - stopping structural energy minimisation' "$outcar"; then
            reason="normal_termination_ionic_converged"
        else
            reason="normal_termination_relaxed_gate"
        fi
        record_stage_status "$stage_dir" "$stage" "SUCCESS" "$reason" "$exit_code"
        write_relaxation_history "$stage_dir" "$stage" "SUCCESS"
        return 0
    fi

    record_stage_status "$stage_dir" "$stage" "FAILURE" "$reason" "$exit_code"
    write_relaxation_history "$stage_dir" "$stage" "FAILURE"
    printf '%s stage failed in %s: %s\n' "$stage" "$stage_dir" "$reason" >&2
    return 1
}

run_vasp_stage() {
    local stage_dir="$1"
    local stage="$2"
    local exit_code

    exit_code="$(run_vasp_command "$stage_dir" "$stage")"
    validate_vasp_stage "$stage_dir" "$stage" "$exit_code"
}

contcars=("$BASE_DIR"/CONTCAR_*)
if (( ${#contcars[@]} == 0 )); then
    echo "No CONTCAR_* input files were found." >&2
    exit 1
fi

# Rough: use INCAR_1 unchanged (ISIF=8).
for contcar in "${contcars[@]}"; do
    sample="${contcar##*/CONTCAR_}"
    sample_dir="${BASE_DIR}/${sample}"
    mkdir -p "$sample_dir"
    cp "$contcar" "${sample_dir}/POSCAR"
    # Rough: damped molecular dynamics avoids the IBRION=2 conjugate-gradient
    # line search that triggers ZBRENT on the VASP 6.3.0 production build.
    prepare_stage_incar INCAR_1 "${sample_dir}/INCAR" 3 0.4
    if ! create_potcar_from_poscar "${sample_dir}/POSCAR" "${sample_dir}/POTCAR"; then
        record_stage_status "$sample_dir" "rough" "FAILURE" "POTCAR_preparation_failed" "1"
        write_relaxation_history "$sample_dir" "rough" "FAILURE"
        failures=$((failures + 1))
        continue
    fi
    if ! run_vasp_stage "$sample_dir" "rough"; then
        failures=$((failures + 1))
    fi
done

# Fine: only start from a complete rough output, and use INCAR_2 unchanged
# (ISIF=8). Any rough last frame accepted here is only a restart input;
# scientific acceptance is deferred to final.
for sample_dir in "$BASE_DIR"/*; do
    [[ -d "$sample_dir" && -s "${sample_dir}/CONTCAR" && -s "${sample_dir}/POTCAR" ]] || continue
    mkdir -p "${sample_dir}/fine"
    rough_status="$(awk -F= '$1 == "status" {print toupper($2)}' "${sample_dir}/ION_CSP_STAGE_STATUS" 2>/dev/null)"
    if [[ "$rough_status" != "SUCCESS" ]]; then
        record_stage_status "${sample_dir}/fine" "fine" "FAILURE" "rough_stage_not_successful" "1"
        write_relaxation_history "${sample_dir}/fine" "fine" "FAILURE"
        failures=$((failures + 1))
        continue
    fi
    cp "${sample_dir}/CONTCAR" "${sample_dir}/fine/POSCAR"
    cp "${sample_dir}/POTCAR" "${sample_dir}/fine/POTCAR"
    # Fine: RMM-DIIS refines the damped rough structure without CG ZBRENT.
    prepare_stage_incar INCAR_2 "${sample_dir}/fine/INCAR" 1
    if ! run_vasp_stage "${sample_dir}/fine" "fine"; then
        failures=$((failures + 1))
    fi
done

if (( failures > 0 )); then
    printf 'VASP fixed-shape rough/fine stages completed with %d failed stage(s); inspect status and topology reports.\n' "$failures" >&2
else
    echo "All VASP fixed-shape rough and fine stages completed normally."
fi

# Candidate-level failures are returned as artifacts and filtered by the
# Python validation/topology gate. Keep the dispatcher task successful so
# other candidates in the same batch are not resubmitted.
exit 0
