# ion_CSP delivery bundle

This directory is the machine-readable release boundary for the three ion_CSP
execution endpoints:

- `core`: EE/CSP controller, parsers, PyXtal, Multiwfn and Phonopy;
- `mlp`: isolated DeepMD/Torch worker and the selected model asset;
- `external`: Gaussian/formchk and VASP/MPI workers.

Authoritative files:

- `locks/core.lock.json` indexes the hash-bearing root `uv.lock` and fixed Conda
  channels;
- `locks/mlp-deepmd-torch.lock.toml` and `locks/mlp-requirements.txt` define the
  default DeepMD/Torch ABI pair;
- `config-contract.schema.json` documents the fixed
  `<work_dir>/config.yaml` EE/CSP contract;
- `external-software.json` declares software location and licensing boundaries;
- `release-manifest.json` binds source parsing, locks, model, build artifacts and
  the external inventory by SHA-256.

See [the deployment guide](../docs/delivery.md) for commands and acceptance
criteria.  A release is not production-accepted until JSON preflight receipts
from all endpoints have been captured on the machines that will execute work.
