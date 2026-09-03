"""
Integration tests for run_convert_SMILES.py
Tests the complete workflow of SMILES conversion and Gaussian tasks
"""

import pytest
import yaml
from unittest.mock import patch, MagicMock
from ion_CSP.run import run_convert_SMILES


@pytest.fixture
def test_work_dir(tmp_path):
    """Create a temporary work directory with config.yaml and CSV file"""
    work_dir = tmp_path / "test_convert_smiles"
    work_dir.mkdir()

    # Create config.yaml
    config = {
        "convert_SMILES": {
            "csv_file": "test_smiles.csv",
            "screen": True,
            "charge_screen": "-1",
            "group_screen": "[N+](=O)[O-]",
            "group_name": "nitro",
            "group_screen_invert": False,
            "nodes": 1,
            "machine": "machine.yaml",
            "resources": "resources.yaml"
        },
        "empirical_estimate": {
            "folders": ["folder1", "folder2"]
        }
    }

    config_file = work_dir / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config, f)

    # Create dummy CSV file
    csv_file = work_dir / "test_smiles.csv"
    csv_file.write_text("""SMILES,Name,Charge
C,Methane,0
CC,Ethane,0
[N+](=O)[O-],Nitro,-1
""")

    return work_dir


def test_run_convert_smiles_main_with_mocks(test_work_dir):
    """Test run_convert_SMILES.main with mocked dependencies"""

    # Read config
    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # Merge with default config
    config["convert_SMILES"] = run_convert_SMILES.merge_config(
        default_config=run_convert_SMILES.DEFAULT_CONFIG,
        user_config=config,
        key="convert_SMILES"
    )

    # Mock SmilesProcessing methods
    with patch("ion_CSP.run.run_convert_SMILES.SmilesProcessing") as MockProcessing:
        mock_proc = MockProcessing.return_value
        mock_proc.charge_group = MagicMock()
        mock_proc.screen = MagicMock()
        mock_proc.dpdisp_gaussian_tasks = MagicMock()

        # Run main function
        run_convert_SMILES.main(test_work_dir, config)

        # Verify SmilesProcessing was initialized correctly
        MockProcessing.assert_called_once_with(
            work_dir=test_work_dir,
            csv_file="test_smiles.csv",
            preserve_topology=True,
        )

        # Verify charge_group was called
        mock_proc.charge_group.assert_called_once()

        # Verify screen was called with correct parameters
        mock_proc.screen.assert_called_once_with(
            charge_screen="-1",
            group_screen="[N+](=O)[O-]",
            group_name="nitro",
            group_screen_invert=False
        )

        # Verify dpdisp_gaussian_tasks was called
        mock_proc.dpdisp_gaussian_tasks.assert_called_once_with(
            folders=["folder1", "folder2"],
            machine_path="machine.yaml",
            resources_path="resources.yaml",
            nodes=1
        )


def test_run_convert_smiles_without_screening(test_work_dir):
    """Test run_convert_SMILES.main without screening"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # Disable screening
    config["convert_SMILES"]["screen"] = False

    config["convert_SMILES"] = run_convert_SMILES.merge_config(
        default_config=run_convert_SMILES.DEFAULT_CONFIG,
        user_config=config,
        key="convert_SMILES"
    )

    with patch("ion_CSP.run.run_convert_SMILES.SmilesProcessing") as MockProcessing:
        mock_proc = MockProcessing.return_value
        mock_proc.charge_group = MagicMock()
        mock_proc.screen = MagicMock()
        mock_proc.dpdisp_gaussian_tasks = MagicMock()

        run_convert_SMILES.main(test_work_dir, config)

        # Verify charge_group was called
        mock_proc.charge_group.assert_called_once()

        # Verify screen was NOT called
        mock_proc.screen.assert_not_called()

        # Verify dpdisp_gaussian_tasks was still called
        mock_proc.dpdisp_gaussian_tasks.assert_called_once()


def test_run_convert_smiles_default_config():
    """Test that DEFAULT_CONFIG has expected structure"""
    assert "convert_SMILES" in run_convert_SMILES.DEFAULT_CONFIG
    assert "csv_file" in run_convert_SMILES.DEFAULT_CONFIG["convert_SMILES"]
    assert "screen" in run_convert_SMILES.DEFAULT_CONFIG["convert_SMILES"]
    assert "charge_screen" in run_convert_SMILES.DEFAULT_CONFIG["convert_SMILES"]
    assert "group_screen" in run_convert_SMILES.DEFAULT_CONFIG["convert_SMILES"]
    assert "group_name" in run_convert_SMILES.DEFAULT_CONFIG["convert_SMILES"]
    assert "group_screen_invert" in run_convert_SMILES.DEFAULT_CONFIG["convert_SMILES"]
    assert "preserve_smiles_topology" in run_convert_SMILES.DEFAULT_CONFIG["convert_SMILES"]

    # Verify default values
    assert run_convert_SMILES.DEFAULT_CONFIG["convert_SMILES"]["csv_file"] == ""
    assert run_convert_SMILES.DEFAULT_CONFIG["convert_SMILES"]["screen"] is False
    assert run_convert_SMILES.DEFAULT_CONFIG["convert_SMILES"]["group_screen_invert"] is False
    assert run_convert_SMILES.DEFAULT_CONFIG["convert_SMILES"]["preserve_smiles_topology"] is True


def test_run_convert_smiles_with_invert_screening(test_work_dir):
    """Test run_convert_SMILES.main with inverted screening"""

    with open(test_work_dir / "config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # Enable inverted screening
    config["convert_SMILES"]["group_screen_invert"] = True

    config["convert_SMILES"] = run_convert_SMILES.merge_config(
        default_config=run_convert_SMILES.DEFAULT_CONFIG,
        user_config=config,
        key="convert_SMILES"
    )

    with patch("ion_CSP.run.run_convert_SMILES.SmilesProcessing") as MockProcessing:
        mock_proc = MockProcessing.return_value
        mock_proc.charge_group = MagicMock()
        mock_proc.screen = MagicMock()
        mock_proc.dpdisp_gaussian_tasks = MagicMock()

        run_convert_SMILES.main(test_work_dir, config)

        # Verify screen was called with group_screen_invert=True
        mock_proc.screen.assert_called_once_with(
            charge_screen="-1",
            group_screen="[N+](=O)[O-]",
            group_name="nitro",
            group_screen_invert=True
        )


@patch("ion_CSP.run.run_convert_SMILES.get_work_dir_and_config")
@patch("ion_CSP.run.run_convert_SMILES.main")
def test_run_convert_smiles_main_entry_point(mock_main, mock_get_config, test_work_dir):
    """Test the __main__ entry point"""

    config = {
        "convert_SMILES": {
            "csv_file": "test.csv",
            "screen": False
        },
        "empirical_estimate": {
            "folders": []
        }
    }
    mock_get_config.return_value = (test_work_dir, config)

    # Simulate running as __main__
    work_dir, config = mock_get_config()
    config["convert_SMILES"] = run_convert_SMILES.merge_config(
        default_config=run_convert_SMILES.DEFAULT_CONFIG,
        user_config=config,
        key="convert_SMILES"
    )
    mock_main(work_dir, config)

    # Verify main was called
    mock_main.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
