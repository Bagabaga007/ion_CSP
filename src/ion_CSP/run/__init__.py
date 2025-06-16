from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("binary4fun")
except PackageNotFoundError:
    __version__ = "unknown version"

name = "run"
