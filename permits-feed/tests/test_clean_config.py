import textwrap

import pytest

from permits_feed.clean import CleanError, clean_value
from permits_feed.config import ConfigError, load_config


@pytest.mark.parametrize(
    "value, kind, expected",
    [
        ("2026-11-03T00:00:00.000", "date", "2026-11-03"),
        ("2026-11-03", "date", "2026-11-03"),
        ("250000", "money", 250000.0),
        ("$1,234.567", "money", 1234.57),
        (" 12 ", "int", 12),
        ("12.0", "int", 12),
        ("41.881832", "float", 41.881832),
        ("  Gut  rehab\n of building ", "text", "Gut rehab of building"),
        ("", "money", None),
        (None, "date", None),
    ],
)
def test_clean_value(value, kind, expected):
    assert clean_value(value, kind) == expected


@pytest.mark.parametrize("value, kind", [("4.5", "int"), ("N/A", "money"), ("soon", "date")])
def test_clean_value_rejects_bad_values(value, kind):
    with pytest.raises(CleanError):
        clean_value(value, kind)


def test_shipped_config_loads():
    config = load_config("permits.toml")
    assert config.source.dataset == "ydr8-5enu"
    assert config.date_column == "issue_date"
    assert "contact_1_name" not in config.fields.values()


def write(tmp_path, body):
    path = tmp_path / "c.toml"
    path.write_text(textwrap.dedent(body))
    return path


BASE = """
[source]
name = "x"
domain = "example.org"
dataset = "abcd-1234"
id_field = "id"
date_field = "issued"
start_date = "2026-01-01"
"""


@pytest.mark.parametrize(
    "extra, message",
    [
        ("[fields]\nissued = 'issued'\n[types]\nissued = 'datetime'\n", "use one of"),
        ("[fields]\nissued = 'issued'\n[types]\ncost = 'money'\n", "not a column"),
        ("[fields]\nwhen = 'other_date'\n", "must include the date field"),
        ("[fields]\nissued = 'issued; DROP'\n", "letters, digits"),
        ("[fields]\nissued = 'issued'\n[report]\ngroup_by = ['ward']\n", "not a column"),
        ("[settings]\npage_sise = 10\n[fields]\nissued = 'issued'\n", "Unknown keys"),
    ],
)
def test_config_errors(tmp_path, extra, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, BASE + extra))
