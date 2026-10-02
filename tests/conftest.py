"""Load api.py standalone; it has no Home Assistant imports by design."""
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "simplygreen"

# Load api.py and stats.py under a stub package, so relative imports work
# without executing __init__.py (which imports Home Assistant).
_pkg = types.ModuleType("simplygreen_pkg")
_pkg.__path__ = [str(PKG_DIR)]
sys.modules["simplygreen_pkg"] = _pkg
api = importlib.import_module("simplygreen_pkg.api")
stats = importlib.import_module("simplygreen_pkg.stats")


@pytest.fixture
def sg_api():
    return api


@pytest.fixture
def sg_stats():
    return stats


@pytest.fixture
def payload_15min():
    return json.loads((Path(__file__).parent / "fixtures" / "load_profile_15min.json").read_text())
