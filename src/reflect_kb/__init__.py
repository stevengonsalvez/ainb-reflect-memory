"""Legacy import path for the plugly-reflect-memory engine."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("plugly-reflect-memory")
except PackageNotFoundError:
    try:
        __version__ = version("reflect-kb")
    except PackageNotFoundError:
        __version__ = "0.4.0"
