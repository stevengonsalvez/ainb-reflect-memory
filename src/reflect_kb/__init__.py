"""reflect-kb — universal cross-harness retrieval + learning KB."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("reflect-kb")
except PackageNotFoundError:  # running from source without an install
    __version__ = "0.2.0"
