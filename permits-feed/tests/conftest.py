import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from fake_socrata import FakeSocrata  # noqa: E402
from synthetic import COLUMNS  # noqa: E402

from permits_feed.config import load_config  # noqa: E402


@pytest.fixture
def portal():
    p = FakeSocrata("test-permits", COLUMNS)
    yield p
    p.close()


@pytest.fixture
def config(portal):
    """The shipped permits.toml, pointed at the local fake portal."""
    base = load_config(ROOT / "permits.toml")
    return dataclasses.replace(
        base,
        source=dataclasses.replace(base.source, domain=portal.domain, dataset="test-permits"),
        settings=dataclasses.replace(base.settings, scheme="http", delay_seconds=0, max_retries=0, page_size=50),
    )
