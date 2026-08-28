from __future__ import annotations

from coding_agent.gui.i18n import CATALOG
from coding_agent.gui.settings import AppSettings, SettingsStore


def test_settings_default_to_chinese_and_round_trip(tmp_path) -> None:
    store = SettingsStore(tmp_path)

    assert store.load() == AppSettings()

    expected = AppSettings(
        language="en",
        model="deepseek-v4-flash",
        reasoning_effort="max",
        max_steps=40,
    )
    store.save(expected)
    assert SettingsStore(tmp_path).load() == expected


def test_chinese_and_english_catalogs_have_matching_keys() -> None:
    assert set(CATALOG["zh"]) == set(CATALOG["en"])
