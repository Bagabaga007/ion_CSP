"""配置验证测试"""
import pytest
from pathlib import Path
from ion_CSP.log_and_time import merge_config


def test_merge_config_with_partial_user_config():
    """测试用户配置部分覆盖默认配置"""
    default_config = {
        "module1": {
            "param1": "default1",
            "param2": "default2",
            "param3": "default3"
        }
    }
    user_config = {
        "module1": {
            "param1": "user1"
        }
    }

    result = merge_config(default_config, user_config, "module1")

    assert result["param1"] == "user1"
    assert result["param2"] == "default2"
    assert result["param3"] == "default3"


def test_merge_config_with_full_user_config():
    """测试用户配置完全覆盖默认配置"""
    default_config = {
        "module1": {
            "param1": "default1",
            "param2": "default2"
        }
    }
    user_config = {
        "module1": {
            "param1": "user1",
            "param2": "user2"
        }
    }

    result = merge_config(default_config, user_config, "module1")

    assert result["param1"] == "user1"
    assert result["param2"] == "user2"


def test_merge_config_missing_default_module():
    """测试默认配置中模块不存在时抛出异常"""
    default_config = {
        "module1": {
            "param1": "default1"
        }
    }
    user_config = {
        "module2": {
            "param1": "user1"
        }
    }

    with pytest.raises(KeyError, match="not found in default configuration"):
        merge_config(default_config, user_config, "module2")


def test_merge_config_missing_user_module():
    """测试用户配置中模块不存在时抛出异常"""
    default_config = {
        "module1": {
            "param1": "default1"
        }
    }
    user_config = {}

    with pytest.raises(KeyError, match="not found in user configuration"):
        merge_config(default_config, user_config, "module1")


def test_merge_config_nested_dict():
    """测试嵌套字典的合并"""
    default_config = {
        "module1": {
            "param1": "default1",
            "nested": {
                "key1": "value1",
                "key2": "value2"
            }
        }
    }
    user_config = {
        "module1": {
            "nested": {
                "key1": "user_value1"
            }
        }
    }

    result = merge_config(default_config, user_config, "module1")

    # merge_config 只合并第一层，嵌套字典会被完全替换
    assert result["param1"] == "default1"
    assert result["nested"]["key1"] == "user_value1"
    # key2 不会保留，因为嵌套字典被完全替换
    assert "key2" not in result["nested"]

