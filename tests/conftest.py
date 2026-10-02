"""Load api.py standalone; it has no Home Assistant imports by design."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "simplygreen_api", ROOT / "custom_components" / "simplygreen" / "api.py"
)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


@pytest.fixture
def sg_api():
    return api


@pytest.fixture
def payload_15min():
    return json.loads((Path(__file__).parent / "fixtures" / "load_profile_15min.json").read_text())
