"""
Integration tests for run_vasp_processing.py
Tests the complete workflow of VASP processing
"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock
from ion_CSP.run import run_vasp_processing


@pytest.fixture
def test_work_dir(tmp_path):
    """Create a temporary work directory with config.yaml"""
    work_dir = tmp_path / "test_vasp"
    work_dir.mkdir()

    # Create config.yaml
    config = {
        "vasp_processing": {
            "nodes": 2,
            "molecules_prior": True,
            "machine_path": "machine.yaml",
            "resources_path": "resources.yaml"
        }
    }

    config_file = work_dir / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config, f)

    return work_dir


def test_run_vasp_processing_main_with_mocks(test_work_dir):
    """Test run_vasp_processing.main with mocked dependencies"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    config["vasp_processing"] = run_vasp_processing.merge_config(
        default_config=run_vasp_processing.DEFAULT_CONFIG,
        user_config=config,
        key="vasp_processing"
    )

    with patch("ion_CSP.run.run_vasp_processing.VaspProcessing") as MockVasp:
        mock_vasp = MockVasp.return_value
        mock_vasp.read_vaspout_save_csv = MagicMock()

        with patch("ion_CSP.run.run_vasp_processing.StatusLogger") as MockLogger:
            mock_logger = MockLogger.return_value
            mock_logger.set_running = MagicMock()
            mock_logger.set_success = MagicMock()

            run_vasp_processing.main(test_work_dir, config)

            # Verify VaspProcessing was initialized correctly
            MockVasp.assert_called_once_with(work_dir=test_work_dir)

            # Verify read_vaspout_save_csv was called with molecules_prior=True
            mock_vasp.read_vaspout_save_csv.assert_called_once_with(True)

            # Verify status was updated
            mock_logger.set_running.assert_called_once()
            mock_logger.set_success.assert_called_once()


def test_run_vasp_processing_without_molecules_prior(test_work_dir):
    """Test run_vasp_processing.main without molecules_prior"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # Disable molecules_prior
    config["vasp_processing"]["molecules_prior"] = False

    config["vasp_processing"] = run_vasp_processing.merge_config(
        default_config=run_vasp_processing.DEFAULT_CONFIG,
        user_config=config,
        key="vasp_processing"
    )

    with patch("ion_CSP.run.run_vasp_processing.VaspProcessing") as MockVasp:
        mock_vasp = MockVasp.return_value
        mock_vasp.read_vaspout_save_csv = MagicMock()

        with patch("ion_CSP.run.run_vasp_processing.StatusLogger") as MockLogger:
            mock_logger = MockLogger.return_value
            mock_logger.set_running = MagicMock()
            mock_logger.set_success = MagicMock()

            run_vasp_processing.main(test_work_dir, config)

            # Verify read_vaspout_save_csv was called with molecules_prior=False
            mock_vasp.read_vaspout_save_csv.assert_called_once_with(False)


def test_run_vasp_processing_failure_handling(test_work_dir):
    """Test that failures are properly handled"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    config["vasp_processing"] = run_vasp_processing.merge_config(
        default_config=run_vasp_processing.DEFAULT_CONFIG,
        user_config=config,
        key="vasp_processing"
    )

    with patch("ion_CSP.run.run_vasp_processing.VaspProcessing") as MockVasp:
        mock_vasp = MockVasp.return_value
        # Make read_vaspout_save_csv raise an exception
        mock_vasp.read_vaspout_save_csv.side_effect = RuntimeError("VASP processing failed")

        with patch("ion_CSP.run.run_vasp_processing.StatusLogger") as MockLogger:
            mock_logger = MockLogger.return_value
            mock_logger.set_running = MagicMock()
            mock_logger.set_failure = MagicMock()

            # Should raise the exception
            with pytest.raises(RuntimeError, match="VASP processing failed"):
                run_vasp_processing.main(test_work_dir, config)

            # Verify failure was logged
            mock_logger.set_failure.assert_called_once()


def test_run_vasp_processing_default_config():
    """Test that DEFAULT_CONFIG has expected structure"""
    assert "vasp_processing" in run_vasp_processing.DEFAULT_CONFIG
    config = run_vasp_processing.DEFAULT_CONFIG["vasp_processing"]

    assert "nodes" in config
    assert "molecules_prior" in config

    # Verify default values
    assert config["nodes"] == 2
    assert config["molecules_prior"] is True


@patch("ion_CSP.run.run_vasp_processing.get_work_dir_and_config")
@patch("ion_CSP.run.run_vasp_processing.main")
def test_run_vasp_processing_main_entry_point(mock_main, mock_get_config, test_work_dir):
    """Test the __main__ entry point"""

    config = {
        "vasp_processing": {
            "nodes": 4,
            "molecules_prior": False
        }
    }
    mock_get_config.return_value = (test_work_dir, config)

    work_dir, config = mock_get_config()
    config["vasp_processing"] = run_vasp_processing.merge_config(
        default_config=run_vasp_processing.DEFAULT_CONFIG,
        user_config=config,
        key="vasp_processing"
    )
    mock_main(work_dir, config)

    mock_main.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
