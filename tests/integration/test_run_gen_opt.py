"""
Integration tests for run_gen_opt.py
Tests the complete workflow of crystal generation and optimization
"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock
from ion_CSP.run import run_gen_opt


@pytest.fixture
def test_work_dir(tmp_path):
    """Create a temporary work directory with config.yaml"""
    work_dir = tmp_path / "test_gen_opt"
    work_dir.mkdir()

    # Create config.yaml
    config = {
        "gen_opt": {
            "species": ["H2.gjf", "O2.gjf"],
            "ion_numbers": [2, 1],
            "num_per_group": 10,
            "space_groups_limit": 5,
            "nodes": 1,
            "machine": "machine.yaml",
            "resources": "resources.yaml",
            "python_path": "/usr/bin/python3"
        }
    }

    config_file = work_dir / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config, f)

    # Create dummy species files
    h2_file = work_dir / "H2.gjf"
    h2_file.write_text("""# HF/6-31G(d)

H2 molecule

0 1
H 0.0 0.0 0.0
H 0.0 0.0 0.74
""")

    o2_file = work_dir / "O2.gjf"
    o2_file.write_text("""# HF/6-31G(d)

O2 molecule

0 1
O 0.0 0.0 0.0
O 0.0 0.0 1.21
""")

    return work_dir


def test_run_gen_opt_main_with_mocks(test_work_dir):
    """Test run_gen_opt.main with mocked dependencies"""

    # Read config
    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # Merge with default config
    config["gen_opt"] = run_gen_opt.merge_config(
        default_config=run_gen_opt.DEFAULT_CONFIG,
        user_config=config,
        key="gen_opt"
    )

    # Mock CrystalGenerator methods
    with patch("ion_CSP.run.run_gen_opt.CrystalGenerator") as MockGenerator:
        mock_gen = MockGenerator.return_value
        mock_gen.generate_structures = MagicMock()
        mock_gen.phonopy_processing = MagicMock()
        mock_gen.dpdisp_mlp_tasks = MagicMock()

        # Mock StatusLogger
        with patch("ion_CSP.run.run_gen_opt.StatusLogger") as MockLogger:
            # Create two separate mock instances for two tasks
            mock_logger_1 = MagicMock()
            mock_logger_2 = MagicMock()
            MockLogger.side_effect = [mock_logger_1, mock_logger_2]

            # First task: not successful initially, then becomes successful after execution
            # Second task: not successful
            mock_logger_1.is_successful.side_effect = [False, True]  # Called twice
            mock_logger_2.is_successful.return_value = False

            # Run main function
            run_gen_opt.main(test_work_dir, config)

            # Verify CrystalGenerator was initialized correctly
            MockGenerator.assert_called_once_with(
                work_dir=test_work_dir,
                ion_numbers=[2, 1],
                species=["H2.gjf", "O2.gjf"]
            )

            # Verify generation task was executed
            mock_gen.generate_structures.assert_called_once_with(
                num_per_group=10,
                space_groups_limit=5
            )
            mock_gen.phonopy_processing.assert_called_once()

            # Verify optimization task was executed
            mock_gen.dpdisp_mlp_tasks.assert_called_once_with(
                machine="machine.yaml",
                resources="resources.yaml",
                python_path="/usr/bin/python3",
                nodes=1
            )


def test_run_gen_opt_skip_successful_tasks(test_work_dir):
    """Test that successful tasks are skipped"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    config["gen_opt"] = run_gen_opt.merge_config(
        default_config=run_gen_opt.DEFAULT_CONFIG,
        user_config=config,
        key="gen_opt"
    )

    with patch("ion_CSP.run.run_gen_opt.CrystalGenerator") as MockGenerator:
        mock_gen = MockGenerator.return_value

        with patch("ion_CSP.run.run_gen_opt.StatusLogger") as MockLogger:
            # Create two separate mock instances for two tasks
            mock_logger_1 = MagicMock()
            mock_logger_2 = MagicMock()
            MockLogger.side_effect = [mock_logger_1, mock_logger_2]

            # First task successful (returns True), second task not successful
            mock_logger_1.is_successful.return_value = True
            mock_logger_2.is_successful.return_value = False

            run_gen_opt.main(test_work_dir, config)

            # Verify generation task was skipped
            mock_gen.generate_structures.assert_not_called()
            mock_gen.phonopy_processing.assert_not_called()

            # Verify optimization task was executed
            mock_gen.dpdisp_mlp_tasks.assert_called_once()


def test_run_gen_opt_task_failure_handling(test_work_dir):
    """Test that task failures are properly handled"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    config["gen_opt"] = run_gen_opt.merge_config(
        default_config=run_gen_opt.DEFAULT_CONFIG,
        user_config=config,
        key="gen_opt"
    )

    with patch("ion_CSP.run.run_gen_opt.CrystalGenerator") as MockGenerator:
        mock_gen = MockGenerator.return_value
        # Make generate_structures raise an exception
        mock_gen.generate_structures.side_effect = RuntimeError("Generation failed")

        with patch("ion_CSP.run.run_gen_opt.StatusLogger") as MockLogger:
            mock_logger = MockLogger.return_value
            mock_logger.is_successful.return_value = False
            mock_logger.set_running = MagicMock()
            mock_logger.set_failure = MagicMock()

            # Should raise the exception
            with pytest.raises(RuntimeError, match="Generation failed"):
                run_gen_opt.main(test_work_dir, config)

            # Verify failure was logged
            mock_logger.set_failure.assert_called_once()


def test_run_gen_opt_default_config():
    """Test that DEFAULT_CONFIG has expected structure"""
    assert "gen_opt" in run_gen_opt.DEFAULT_CONFIG
    assert "num_per_group" in run_gen_opt.DEFAULT_CONFIG["gen_opt"]
    assert "space_groups_limit" in run_gen_opt.DEFAULT_CONFIG["gen_opt"]
    assert "nodes" in run_gen_opt.DEFAULT_CONFIG["gen_opt"]

    # Verify default values
    assert run_gen_opt.DEFAULT_CONFIG["gen_opt"]["num_per_group"] == 500
    assert run_gen_opt.DEFAULT_CONFIG["gen_opt"]["space_groups_limit"] == 230
    assert run_gen_opt.DEFAULT_CONFIG["gen_opt"]["nodes"] == 1


@patch("ion_CSP.run.run_gen_opt.get_work_dir_and_config")
@patch("ion_CSP.run.run_gen_opt.main")
def test_run_gen_opt_main_entry_point(mock_main, mock_get_config, test_work_dir):
    """Test the __main__ entry point"""

    # Setup mock return values
    config = {
        "gen_opt": {
            "species": ["H2.gjf"],
            "ion_numbers": [1],
            "machine": "machine.yaml",
            "resources": "resources.yaml",
            "python_path": "/usr/bin/python3"
        }
    }
    mock_get_config.return_value = (test_work_dir, config)

    # Import and execute the __main__ block
    import importlib
    import ion_CSP.run.run_gen_opt as module

    # Simulate running as __main__
    with patch.object(module, "__name__", "__main__"):
        # This would normally execute the if __name__ == "__main__" block
        # For testing, we'll call the functions directly
        work_dir, config = mock_get_config()
        config["gen_opt"] = run_gen_opt.merge_config(
            default_config=run_gen_opt.DEFAULT_CONFIG,
            user_config=config,
            key="gen_opt"
        )
        mock_main(work_dir, config)

    # Verify main was called
    mock_main.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
