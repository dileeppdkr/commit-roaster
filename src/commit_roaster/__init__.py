"""commit-roaster: roast a git diff and suggest a proper commit message."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("commit-roaster")
except PackageNotFoundError:  # running from a source checkout without installing
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
