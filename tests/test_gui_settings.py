from __future__ import annotations

from coding_agent.gui.i18n import CATALOG
from coding_agent.gui.settings import MODELS, AppSettings, SettingsStore


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


def test_flash_and_pro_models_are_supported_and_persisted(tmp_path) -> None:
    assert MODELS == ("deepseek-v4-flash", "deepseek-v4-pro")
    assert AppSettings().model == "deepseek-v4-flash"
    store = SettingsStore(tmp_path)

    for model in MODELS:
        expected = AppSettings(model=model)
        store.save(expected)
        assert store.load() == expected
