"""Canonical Python interface to plugly-reflect-memory.

The implementation retains its legacy package path during migration. Aliases
resolve to the same module objects so class identities, caches and singleton
state are shared across old and new callers.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys

from reflect_kb import __version__

_PREFIX = __name__ + "."


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, legacy_name):
        self.legacy_name = legacy_name
        self.original_spec = None

    def create_module(self, spec):
        module = importlib.import_module(self.legacy_name)
        self.original_spec = module.__spec__
        return module

    def exec_module(self, module):
        module.__spec__ = self.original_spec

    def get_code(self, fullname):
        # Support python -m for existing operational entry points.
        source = f"import runpy; runpy.run_module({self.legacy_name!r}, run_name='__main__')"
        return compile(source, "<plugly-reflect-memory compatibility entry>", "exec")

    def is_package(self, fullname):
        return importlib.util.find_spec(self.legacy_name).submodule_search_locations is not None


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_PREFIX):
            return None
        legacy = "reflect_kb." + fullname[len(_PREFIX):]
        try:
            original = importlib.util.find_spec(legacy)
        except ModuleNotFoundError:
            return None
        if original is None:
            return None
        spec = importlib.util.spec_from_loader(
            fullname, _AliasLoader(legacy), origin=original.origin,
            is_package=original.submodule_search_locations is not None,
        )
        spec.has_location = True
        return spec


if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

__all__ = ["__version__"]
