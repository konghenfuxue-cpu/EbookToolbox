import json

import pytest

from ebook_toolbox import build_info


@pytest.mark.parametrize("payload", [None, "{bad", "[]", '{"revision": 3}'])
def test_unknown_identity_is_explicit(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(build_info, "__file__", str(tmp_path / "build_info.py"))
    if payload is not None:
        (tmp_path / "build-info.json").write_text(payload, encoding="utf-8")
    assert "未知" in build_info.build_description()


@pytest.mark.parametrize("dirty", [True, False])
def test_identity_displays_revision_date_and_dirty_state(tmp_path, monkeypatch, dirty):
    monkeypatch.setattr(build_info, "__file__", str(tmp_path / "build_info.py"))
    (tmp_path / "build-info.json").write_text(
        json.dumps(
            {
                "revision": "abc123",
                "built_at": "2026-10-18T12:00:00+08:00",
                "dirty": dirty,
            }
        ),
        encoding="utf-8",
    )
    description = build_info.build_description()
    assert "abc123" in description
    assert "2026-10-18T12:00:00+08:00" in description
    assert ("含未提交修改" in description) is dirty
