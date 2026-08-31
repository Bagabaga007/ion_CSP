# MatterSim MLP backend

The bundled fine-tuned DeePMD model supports only `C`, `N`, `O`, and `H`.
Structures containing other elements, such as boron, must not use that model.

For universal-element optimization, configure the CSP `gen_opt` module with the
MatterSim backend and a separate MatterSim Python environment:

```yaml
gen_opt:
  mlp_backend: 'mattersim'
  mlp_python: '/path/to/conda/envs/mattersim/bin/python'
  mlp_model: 'MatterSim-v1.0.0-5M.pth'
  mlp_device: 'cuda'
  mlp_workers: 1
```

The remaining `machine`, `resources`, `nodes`, `num_per_group`, and
`ion_numbers` settings are unchanged. MatterSim defaults to one worker because
each worker loads a complete model onto the selected GPU. DeePMD remains the
default backend for existing configurations.

The MatterSim environment is intentionally separate from the main `ion_CSP`
environment because current MatterSim releases require Python 3.12, while the
main project currently runs on Python 3.11.
