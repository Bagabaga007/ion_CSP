"""Run the EE workflow with <work_dir>/config.yaml.

Usage:
    python docs/example_usage_EE.py /absolute/path/to/ee_work_dir
"""

from ion_CSP.log_and_time import get_work_dir_and_config, merge_config
from ion_CSP.run.main_EE import DEFAULT_CONFIG, main


if __name__ == "__main__":
    work_dir, config = get_work_dir_and_config()
    for module in ["convert_SMILES", "empirical_estimate"]:
        config[module] = merge_config(
            default_config=DEFAULT_CONFIG,
            user_config=config,
            key=module,
        )
    main(work_dir, config)
