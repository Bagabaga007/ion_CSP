__author__ = "Ze Yang"
__contact__ = "yangze1995007@163.com"
__license__ = "MIT"
__version__ = "2.1.3"
__date__ = "2025-06-11"


try:
    from importlib.metadata import version  # python >= 3.11
except Exception:
    try:
        from importlib_metadata import version
    except Exception:
        pass

try:
    __version__ = version("ion_CSP")
except Exception:
    pass
