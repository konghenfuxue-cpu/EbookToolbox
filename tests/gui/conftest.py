"""GUI tests must never read or write the user's application preferences."""

import pytest

from ebook_toolbox import preferences


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "default_preferences_path", lambda: tmp_path / "settings.json")
