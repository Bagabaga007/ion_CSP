# DPA4 MLP backend

The bundled fine-tuned DeePMD model supports only `C`, `N`, `O`, and `H`.
Use the optional DPA4 backend for structures containing unsupported elements such
as boron.

Recommended CSP configuration for initial screening:

```yaml
gen_opt:
  mlp_backend: 'dpa4'
  mlp_python: '/path/to/conda/envs/dpa4/bin/python'
  mlp_model: 'DPA4-Nano-OMat24-v20260805'
  mlp_device: 'cuda'
  mlp_workers: 1
```

DPA4 models are loaded through DeePMD-kit and may be specified either by an
official pretrained model name or by a local model path. DPA4 runs with one
worker per GPU so that the model is loaded once and reused across all structures.

The OMat24 pretrained models are universal inorganic-material starting points,
not validated force fields for nitrogen-rich ionic molecular crystals. Keep the
downstream VASP optimization and compare a representative structure subset
against DFT before relying on DPA4 rankings.

## Experimental ion-specific fine-tuned backend

An ion-specific DPA4 LoRA model can be selected with `dpa4_ion_ft`. It uses the
same DeePMD calculator as `dpa4`, but the distinct backend name makes production
logs and benchmark outputs unambiguous.

```yaml
gen_opt:
  mlp_backend: 'dpa4_ion_ft'
  mlp_python: '/path/to/conda/envs/dpa4/bin/python'
  mlp_model: 'dpa4_ion_ft_v1.pt'
  mlp_device: 'cuda'
  mlp_workers: 1
```

The current 4000-step checkpoint is experimental. It improves the independent
VASP energy, force, and stress benchmark over the base DPA4 model, but it has not
yet matched the bundled DeePMD model's structure-optimization convergence rate.
