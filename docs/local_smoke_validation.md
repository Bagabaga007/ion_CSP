# Local CSP smoke result verification

`scripts/verify_local_csp_smoke.py` checks an already completed CSP smoke. It
does not start `ion_CSP`, MLP, MPI, VASP, or any other scientific process. It
reads the workdir configuration, recorded workflow stages, generated and
optimized structures, VASP stage markers, CSV outputs, and final POSCAR.

Run it with the same Python environment as the core package:

```bash
python scripts/verify_local_csp_smoke.py /absolute/path/to/completed-smoke
```

The verifier has a 30-second default read budget (maximum 120 seconds) and
rejects oversized metadata, CSV, structure, or OUTCAR files. It derives the
expected element counts and site count from `gen_opt.species` and
`gen_opt.ion_numbers` in `config.yaml`, then checks the final POSCAR against
that composition and requires a finite, positive periodic cell.

The smoke contract requires explicit small limits: no more than two generation
attempts, one MLP worker/node, `n_screen: 1`, and one VASP node. It checks all
five `workflow_status.yaml` stages, at least one generated primitive cell,
matching nonempty MLP CONTCAR/OUTCAR files, exactly one screened density row,
one eligible final-relaxation candidate, one finite final CSV row with
`Final_Ions_Check: true`, and successful VASP rough, fine, and final stage
markers. The final OUTCAR must have a normal termination marker and ionic
convergence. A nonzero stage exit is reported as `process_exit`; a completed
VASP run without ionic convergence is reported as `not_converged`; missing or
in-progress stages are reported separately.

By default, no evidence file or CIF is written. Use `--json-output` to create a
new JSON evidence file; existing files are never overwritten. Use `--write-cif`
to create a new `Final.cif` from the verified final POSCAR. CIF creation is
explicit, fails if `Final.cif` already exists, and verifies formula, site
count, and cell-volume roundtrip. The verifier does not delete or modify
existing smoke outputs.

```bash
python scripts/verify_local_csp_smoke.py /absolute/path/to/completed-smoke \
  --write-cif \
  --json-output /absolute/path/to/new-evidence.json
```

This check validates recorded artifacts; it does not reproduce calculations
or certify the scientific accuracy of the model or VASP results.
