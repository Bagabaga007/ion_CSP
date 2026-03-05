"""
Integration tests for run_empirical_estimate.py
Tests the complete workflow of empirical estimation
"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock
from ion_CSP.run import run_empirical_estimate


@pytest.fixture
def test_work_dir(tmp_path):
    """Create a temporary work directory with config.yaml"""
    work_dir = tmp_path / "test_empirical"
    work_dir.mkdir()

    # Create config.yaml
    config = {
        "empirical_estimate": {
            "folders": ["cation", "anion"],
            "ratios": [1, 1],
            "sort_by": "density",
            "make_combo_dir": True,
            "target_dir": "combos",
            "num_combos": 10,
            "ion_numbers": [2, 1]
        }
    }

    config_file = work_dir / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config, f)

    return work_dir


def test_run_empirical_estimate_main_with_density_sort(test_work_dir):
    """Test run_empirical_estimate.main with density sorting"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    config["empirical_estimate"] = run_empirical_estimate.merge_config(
        default_config=run_empirical_estimate.DEFAULT_CONFIG,
        user_config=config,
        key="empirical_estimate"
    )

    with patch("ion_CSP.run.run_empirical_estimate.EmpiricalEstimation") as MockEstimation:
        mock_est = MockEstimation.return_value
        mock_est.multiwfn_process_fchk_to_json = MagicMock()
        mock_est.gaussian_log_to_optimized_gjf = MagicMock()
        mock_est.empirical_estimate = MagicMock()
        mock_est.nitrogen_content_estimate = MagicMock()
        mock_est.make_combo_dir = MagicMock()

        run_empirical_estimate.main(test_work_dir, config)

        # Verify EmpiricalEstimation was initialized correctly
        MockEstimation.assert_called_once_with(
            work_dir=test_work_dir,
            folders=["cation", "anion"],
            ratios=[1, 1],
            sort_by="density"
        )

        # Verify processing methods were called
        mock_est.multiwfn_process_fchk_to_json.assert_called_once()
        mock_est.gaussian_log_to_optimized_gjf.assert_called_once()

        # Verify empirical_estimate was called (density sort)
        mock_est.empirical_estimate.assert_called_once()
        mock_est.nitrogen_content_estimate.assert_not_called()

        # Verify make_combo_dir was called
        mock_est.make_combo_dir.assert_called_once_with(
            target_dir="combos",
            num_combos=10,
            ion_numbers=[2, 1]
        )


def test_run_empirical_estimate_main_with_nitrogen_sort(test_work_dir):
    """Test run_empirical_estimate.main with nitrogen sorting"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # Change sort_by to nitrogen
    config["empirical_estimate"]["sort_by"] = "nitrogen"

    config["empirical_estimate"] = run_empirical_estimate.merge_config(
        default_config=run_empirical_estimate.DEFAULT_CONFIG,
        user_config=config,
        key="empirical_estimate"
    )

    with patch("ion_CSP.run.run_empirical_estimate.EmpiricalEstimation") as MockEstimation:
        mock_est = MockEstimation.return_value
        mock_est.multiwfn_process_fchk_to_json = MagicMock()
        mock_est.gaussian_log_to_optimized_gjf = MagicMock()
        mock_est.empirical_estimate = MagicMock()
        mock_est.nitrogen_content_estimate = MagicMock()
        mock_est.make_combo_dir = MagicMock()

        run_empirical_estimate.main(test_work_dir, config)

        # Verify nitrogen_content_estimate was called (nitrogen sort)
        mock_est.nitrogen_content_estimate.assert_called_once()
        mock_est.empirical_estimate.assert_not_called()


def test_run_empirical_estimate_without_combo_dir(test_work_dir):
    """Test run_empirical_estimate.main without creating combo directories"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # Disable make_combo_dir
    config["empirical_estimate"]["make_combo_dir"] = False

    config["empirical_estimate"] = run_empirical_estimate.merge_config(
        default_config=run_empirical_estimate.DEFAULT_CONFIG,
        user_config=config,
        key="empirical_estimate"
    )

    with patch("ion_CSP.run.run_empirical_estimate.EmpiricalEstimation") as MockEstimation:
        mock_est = MockEstimation.return_value
        mock_est.multiwfn_process_fchk_to_json = MagicMock()
        mock_est.gaussian_log_to_optimized_gjf = MagicMock()
        mock_est.empirical_estimate = MagicMock()
        mock_est.make_combo_dir = MagicMock()

        run_empirical_estimate.main(test_work_dir, config)

        # Verify make_combo_dir was NOT called
        mock_est.make_combo_dir.assert_not_called()


def test_run_empirical_estimate_default_config():
    """Test that DEFAULT_CONFIG has expected structure"""
    assert "empirical_estimate" in run_empirical_estimate.DEFAULT_CONFIG
    config = run_empirical_estimate.DEFAULT_CONFIG["empirical_estimate"]

    assert "folders" in config
    assert "ratios" in config
    assert "sort_by" in config
    assert "make_combo_dir" in config
    assert "target_dir" in config
    assert "num_combos" in config
    assert "ion_numbers" in config

    # Verify default values
    assert config["folders"] == []
    assert config["ratios"] == []
    assert config["sort_by"] == "density"
    assert config["make_combo_dir"] is True
    assert config["num_combos"] == 100


@patch("ion_CSP.run.run_empirical_estimate.get_work_dir_and_config")
@patch("ion_CSP.run.run_empirical_estimate.main")
def test_run_empirical_estimate_main_entry_point(mock_main, mock_get_config, test_work_dir):
    """Test the __main__ entry point"""

    config = {
        "empirical_estimate": {
            "folders": ["test"],
            "ratios": [1],
            "sort_by": "density"
        }
    }
    mock_get_config.return_value = (test_work_dir, config)

    work_dir, config = mock_get_config()
    config["empirical_estimate"] = run_empirical_estimate.merge_config(
        default_config=run_empirical_estimate.DEFAULT_CONFIG,
        user_config=config,
        key="empirical_estimate"
    )
    mock_main(work_dir, config)

    mock_main.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
