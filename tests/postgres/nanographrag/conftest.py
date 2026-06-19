# ABOUTME: Skip the whole nano-graphrag adapter dir unless its client stack is
# ABOUTME: importable. Fixtures are inherited from tests/postgres/conftest.py.

import pytest

pytest.importorskip("nano_graphrag", reason="nano-graphrag not installed")
pytest.importorskip("networkx")
pytest.importorskip("numpy")
