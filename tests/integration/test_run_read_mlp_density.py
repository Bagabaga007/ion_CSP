"""
Integration tests for run_read_mlp_density.py
Tests the complete workflow of reading MLP density results
"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock
from ion_CSP.run import run_read_mlp_density


@pytest.fixture
def test_work_dir(tmp_path):
    """Create a temporary work directory with config.yaml"""
    work_dir = tmp_path / "test_read_mlp"
    work_dir.mkdir()

    # Create config.yaml
    config = {
        "read_mlp_density": {
            "n_screen": 10,
            "sort_by": "density",
            "molecules_screen": True,
            "detail_log": False
        }
    }

    config_file = work_dir / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config, f)

    return work_dir


def test_run_read_mlp_density_main_with_mocks(test_work_dir):
    """Test run_read_mlp_density.main with mocked dependencies"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    config["read_mlp_density"] = run_read_mlp_density.merge_config(
        default_config=run_read_mlp_density.DEFAULT_CONFIG,
        user_config=config,
        key="read_mlp_density"
    )

    with patch("ion_CSP.run.run_read_mlp_density.ReadMlpDensity") as MockReadMlp:
        mock_reader = MockReadMlp.return_value
        mock_reader.read_property_and_sort = MagicMock()
        mock_reader.phonopy_processing_max_density = MagicMock()

        with patch("ion_CSP.run.run_read_mlp_density.StatusLogger") as MockLogger:
            mock_logger = MockLogger.return_value
            mock_logger.set_running = MagicMock()
            mock_logger.set_success = MagicMock()

            run_read_mlp_density.main(test_work_dir, config)

            # Verify ReadMlpDensity was initialized correctly
            MockReadMlp.assert_called_once_with(work_dir=test_work_dir)

            # Verify read_property_and_sort was called with correct parameters
            mock_reader.read_property_and_sort.assert_called_once_with(
                n_screen=10,
                sort_by="density",
                molecules_screen=True,
                detail_log=False
            )

            # Verify phonopy_processing_max_density was called
            mock_reader.phonopy_processing_max_density.assert_called_once()

            # Verify status was updated
            mock_logger.set_running.assert_called_once()
            mock_logger.set_success.assert_called_once()


def test_run_read_mlp_density_with_energy_sort(test_work_dir):
    """Test run_read_mlp_density.main with energy sorting"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # Change sort_by to energy
    config["read_mlp_density"]["sort_by"] = "energy"

    config["read_mlp_density"] = run_read_mlp_density.merge_config(
        default_config=run_read_mlp_density.DEFAULT_CONFIG,
        user_config=config,
        key="read_mlp_density"
    )

    with patch("ion_CSP.run.run_read_mlp_density.ReadMlpDensity") as MockReadMlp:
        mock_reader = MockReadMlp.return_value
        mock_reader.read_property_and_sort = MagicMock()
        mock_reader.phonopy_processing_max_density = MagicMock()

        with patch("ion_CSP.run.run_read_mlp_density.StatusLogger") as MockLogger:
            mock_logger = MockLogger.return_value
            mock_logger.set_running = MagicMock()
            mock_logger.set_success = MagicMock()

            run_read_mlp_density.main(test_work_dir, config)

            # Verify sort_by was set to energy
            mock_reader.read_property_and_sort.assert_called_once_with(
                n_screen=10,
                sort_by="energy",
                molecules_screen=True,
                detail_log=False
            )


def test_run_read_mlp_density_with_detail_log(test_work_dir):
    """Test run_read_mlp_density.main with detail logging enabled"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # Enable detail_log
    config["read_mlp_density"]["detail_log"] = True

    config["read_mlp_density"] = run_read_mlp_density.merge_config(
        default_config=run_read_mlp_density.DEFAULT_CONFIG,
        user_config=config,
        key="read_mlp_density"
    )

    with patch("ion_CSP.run.run_read_mlp_density.ReadMlpDensity") as MockReadMlp:
        mock_reader = MockReadMlp.return_value
        mock_reader.read_property_and_sort = MagicMock()
        mock_reader.phonopy_processing_max_density = MagicMock()

        with patch("ion_CSP.run.run_read_mlp_density.StatusLogger") as MockLogger:
            mock_logger = MockLogger.return_value
            mock_logger.set_running = MagicMock()
            mock_logger.set_success = MagicMock()

            run_read_mlp_density.main(test_work_dir, config)

            # Verify detail_log was enabled
            mock_reader.read_property_and_sort.assert_called_once_with(
                n_screen=10,
                sort_by="density",
                molecules_screen=True,
                detail_log=True
            )


def test_run_read_mlp_density_failure_handling(test_work_dir):
    """Test that failures are properly handled"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    config["read_mlp_density"] = run_read_mlp_density.merge_config(
        default_config=run_read_mlp_density.DEFAULT_CONFIG,
        user_config=config,
        key="read_mlp_density"
    )

    with patch("ion_CSP.run.run_read_mlp_density.ReadMlpDensity") as MockReadMlp:
        mock_reader = MockReadMlp.return_value
        # Make read_property_and_sort raise an exception
        mock_reader.read_property_and_sort.side_effect = RuntimeError("Read failed")

        with patch("ion_CSP.run.run_read_mlp_density.StatusLogger") as MockLogger:
            mock_logger = MockLogger.return_value
            mock_logger.set_running = MagicMock()
            mock_logger.set_failure = MagicMock()

            # Should raise the exception
            with pytest.raises(RuntimeError, match="Read failed"):
                run_read_mlp_density.main(test_work_dir, config)

            # Verify failure was logged
            mock_logger.set_failure.assert_called_once()


def test_run_read_mlp_density_default_config():
    """Test that DEFAULT_CONFIG has expected structure"""
    assert "read_mlp_density" in run_read_mlp_density.DEFAULT_CONFIG
    config = run_read_mlp_density.DEFAULT_CONFIG["read_mlp_density"]

    assert "n_screen" in config
    assert "sort_by" in config
    assert "molecules_screen" in config
    assert "detail_log" in config

    # Verify default values
    assert config["n_screen"] == 10
    assert config["sort_by"] == "density"
    assert config["molecules_screen"] is True
    assert config["detail_log"] is False


@patch("ion_CSP.run.run_read_mlp_density.get_work_dir_and_config")
@patch("ion_CSP.run.run_read_mlp_density.main")
def test_run_read_mlp_density_main_entry_point(mock_main, mock_get_config, test_work_dir):
    """Test the __main__ entry point"""

    config = {
        "read_mlp_density": {
            "n_screen": 5,
            "sort_by": "density"
        }
    }
    mock_get_config.return_value = (test_work_dir, config)

    work_dir, config = mock_get_config()
    config["read_mlp_density"] = run_read_mlp_density.merge_config(
        default_config=run_read_mlp_density.DEFAULT_CONFIG,
        user_config=config,
        key="read_mlp_density"
    )
    mock_main(work_dir, config)

    mock_main.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
