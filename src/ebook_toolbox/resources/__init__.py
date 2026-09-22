"""Runtime paths for application-owned resources."""

from pathlib import Path


def resource_path(name: str) -> Path:
    """Return an application resource path in source and frozen builds."""

    root = Path(__file__).resolve().parent
    candidate = root / name
    if candidate.parent != root:
        raise ValueError(f"Resource name must not contain directories: {name!r}")
    return candidate


__all__ = ["resource_path"]
