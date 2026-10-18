"""Read build-time identity without requiring Git on the user's computer."""

import json
from pathlib import Path


def build_description() -> str:
    try:
        value = json.loads(Path(__file__).with_name("build-info.json").read_text(encoding="utf-8"))
        revision = value["revision"]
        built_at = value["built_at"]
        dirty = value["dirty"]
        if (
            not isinstance(revision, str)
            or not isinstance(built_at, str)
            or type(dirty) is not bool
        ):
            raise ValueError("invalid build identity")
    except (OSError, ValueError, KeyError, TypeError):
        return "构建标识：未知（源码运行或未提供构建信息）"
    suffix = " · 含未提交修改" if dirty else ""
    return f"构建：{built_at} · {revision}{suffix}（此标识不代表正式发布）"
