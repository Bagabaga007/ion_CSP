# Offline customer compute profiles

`ion-csp-profile` validates customer-owned dpdispatcher machine/resources files
and can render a private, per-run resource set for remote Slurm or a local
workstation. It does not connect to SSH, run `nvidia-smi`, or submit jobs.
It checks that `remote_profile` has a hostname and username but never emits
or copies those values. Local GPU validation only checks
that `nvidia-smi` is discoverable on `PATH`.

The profile itself contains paths and non-secret identifiers. Keep `owner_id`
and `run_id` short, stable, and non-secret; they become part of Slurm job tags.
The validator requires absolute paths to operator-owned `.yaml` or `.json`
machine and resources files. A profile must declare exactly one scheduler mode:

- `remote_slurm` with `scheduler: slurm`, `SSHContext` machine files, and
  `Slurm` batch files;
- `local` with `scheduler: none`, `LocalContext` machine files, and `Shell`
  batch files.

Remote `SSHContext` plus `Shell` is rejected. Each stage pairs one machine and
one resources file and declares `resource_class: cpu` or `gpu`. `convert_SMILES`
and `vasp_processing` use CPU resources; `gen_opt` may use either. The resource
file must request zero GPUs for CPU stages and at least one GPU for GPU stages.
Remote Slurm machine files must also provide an absolute `remote_root` made of
plain path characters, plus a `remote_profile` mapping with non-empty
`hostname` and `username` fields. Validation never prints those identity values
or copies them into rendered files.

## Remote Slurm example

```yaml
schema_version: 1
owner_id: lab-blue
run_id: csp-2026-10-19-01
mode: remote_slurm
scheduler: slurm
stages:
  convert_SMILES:
    resource_class: cpu
    machine: /srv/ion-csp/operator/cpu-machine.yaml
    resources: /srv/ion-csp/operator/cpu-resources.yaml
  gen_opt:
    resource_class: gpu
    machine: /srv/ion-csp/operator/gpu-machine.yaml
    resources: /srv/ion-csp/operator/gpu-resources.yaml
  vasp_processing:
    resource_class: cpu
    machine: /srv/ion-csp/operator/cpu-machine.yaml
    resources: /srv/ion-csp/operator/cpu-resources.yaml
```

The CPU and GPU machine files can point to different nodes while using the
customer's shared Slurm account. The tool never guesses an account from a
partition/`queue_name`: an explicitly configured `--account` custom flag is
preserved as written. Safe, non-conflicting Slurm custom flags are preserved;
the renderer rejects custom `job-name`/`comment` directives and resource
allocation flags that conflict with the structured resource counts. It appends
run, role, and stage tags as `--job-name` and `--comment` for every remote
stage.

## Local workstation example

```yaml
schema_version: 1
owner_id: workstation-a
run_id: local-check-01
mode: local
scheduler: none
stages:
  gen_opt:
    resource_class: gpu  # use cpu with gpu_per_node: 0 for CPU-only MLP
    machine: /home/alice/ion-csp/machine.yaml
    resources: /home/alice/ion-csp/resources-gpu.yaml
  vasp_processing:
    resource_class: cpu
    machine: /home/alice/ion-csp/machine.yaml
    resources: /home/alice/ion-csp/resources-cpu.yaml
```

For a local profile, use a `LocalContext`/`Shell` machine file. CPU-only work
does not require `nvidia-smi`; a local GPU stage requires it to be available on
`PATH`. For a full CSP run, the local machine must also provide the required
VASP and MPI executables for the CPU `vasp_processing` stage. Local outputs have
no Slurm flags and no scheduler/account charge.

## Validate and render

```bash
ion-csp-profile validate /absolute/path/site-profile.yaml
ion-csp-profile render /absolute/path/site-profile.yaml \
  --output-dir /absolute/path/ion-csp-profile-runs
```

Rendering creates a new `<owner_id>__<run_id>` directory and refuses to reuse
an existing one. The directory contains `resources/<stage>.resources.yaml`, a
`config.compute-profile.yaml` fragment, and a redacted `receipt.json`; files
are created with owner-only permissions. The fragment has the existing
`convert_SMILES`, `gen_opt`, and `vasp_processing` stage names and absolute
`machine`/`resources` paths. Copy those path pairs into the corresponding
sections of the customer's `<work_dir>/config.yaml`; the fragment intentionally
does not replace workflow-specific inputs such as species, CSV names, or
ion counts.

Generated resources retain the standard node/CPU/GPU/group counts, safe Slurm
custom flags, queue name, approved module/source settings, and a small allowlist
of non-secret environment variables. Arbitrary shell snippets, generic
`kwargs`, custom scheduler templates, and unapproved environment keys are
rejected rather than copied. `source_list` paths and approved `envs` values use
a conservative character set that rejects command substitution, backticks,
semicolons, and other shell separators; a simple `PATH` value such as
`/opt/vasp/bin:$PATH` remains valid. The receipt includes only owner/run
identifiers, stage roles, and output paths; it never contains machine-source
paths or machine credentials.
