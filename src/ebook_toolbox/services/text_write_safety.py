"""Shared TXT preflight, source protection and guarded atomic publication.

Checks cover a stable local filesystem; they are not a lock against a malicious
process changing paths between the final check and the OS publication call.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_WINDOWS = os.name == "nt"
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


class TextWriteSafetyError(OSError):
    def __init__(self, code: str, message: str, path: Path):
        super().__init__(message)
        self.code = code
        self.path = path


def freeze_path(path: Path) -> Path:
    """Capture cwd without collapsing symlink/.. traversal before resolution."""
    return Path(path).absolute()


@dataclass(frozen=True)
class _FileState:
    path: Path
    resolved: Path
    identity: tuple[int, int] | None
    signature: tuple[int, int, int, int, int] | None
    regular: bool
    linked: bool

    @property
    def keys(self) -> set[str]:
        # Conservative even on a case-sensitive Windows directory.
        return {str(self.path).casefold(), str(self.resolved).casefold()}


def _identity(info: os.stat_result, path: Path) -> tuple[int, int]:
    if not info.st_ino:
        raise TextWriteSafetyError("identity_unavailable", "无法确认文件身份", path)
    return info.st_dev, info.st_ino


def _snapshot(path: Path) -> _FileState:
    try:
        resolved = path.resolve(strict=False)
        try:
            entry = path.lstat()
        except FileNotFoundError:
            entry = None
        try:
            info = path.stat()
        except FileNotFoundError:
            info = None
        linked = entry is not None and (
            stat.S_ISLNK(entry.st_mode)
            or bool(getattr(entry, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        )
        identity = None if info is None else _identity(info, path)
        signature = (
            None if info is None else (*identity, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        )
        return _FileState(
            path,
            resolved,
            identity,
            signature,
            info is not None and stat.S_ISREG(info.st_mode),
            linked,
        )
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, TextWriteSafetyError):
            raise
        raise TextWriteSafetyError(
            "identity_unavailable", f"无法确认路径身份：{exc}", path
        ) from exc


def _aliases(first: _FileState, second: _FileState) -> bool:
    return bool(first.keys & second.keys) or (
        first.identity is not None and first.identity == second.identity
    )


def _validate_output(state: _FileState) -> None:
    path = state.path
    # Reject ADS, device names, trailing dots/spaces and extended/device namespaces.
    # This TXT service deliberately uses ordinary Windows output names.
    invalid_name = str(path).startswith(("\\\\?\\", "\\\\.\\")) or any(
        part not in {".", ".."}
        and (
            part.endswith((".", " "))
            or _RESERVED.match(part)
            or any(char in '<>:"|?*' or ord(char) < 32 for char in part)
        )
        for part in path.parts[1:]
    )
    if invalid_name or state.linked or (state.identity is not None and not state.regular):
        raise TextWriteSafetyError("unsafe_output", "输出必须是普通文件且使用安全文件名", path)


@dataclass(frozen=True)
class TextOutputPlan:
    sources: tuple[_FileState, ...]
    outputs: tuple[_FileState, ...]
    overwrite: bool


def validate_output_plan(
    sources: Iterable[Path], outputs: Iterable[Path], *, overwrite: bool
) -> TextOutputPlan:
    inputs = tuple(_snapshot(freeze_path(path)) for path in sources)
    targets = tuple(_snapshot(freeze_path(path)) for path in outputs)
    for source in inputs:
        if not source.regular:
            raise TextWriteSafetyError("invalid_source", "输入不是可确认的普通文件", source.path)
    for index, output in enumerate(targets):
        if any(_aliases(source, output) for source in inputs):
            raise TextWriteSafetyError(
                "source_output_conflict", "输出与输入原稿指向同一文件", output.path
            )
        if any(_aliases(other, output) for other in targets[:index]):
            raise TextWriteSafetyError(
                "output_collision", "整批输出路径存在重名或同文件碰撞", output.path
            )
    for output in targets:
        _validate_output(output)
        if output.identity is not None and not overwrite:
            raise TextWriteSafetyError("output_exists", "输出文件已存在", output.path)
    return TextOutputPlan(inputs, targets, overwrite)


@dataclass(frozen=True)
class OwnedTemporary:
    path: Path
    identity: tuple[int, int]

    @classmethod
    def capture(cls, temporary) -> OwnedTemporary:
        path = Path(temporary.name)
        return cls(path, _identity(os.fstat(temporary.fileno()), path))


def cleanup_temporary(temporary: OwnedTemporary | None) -> str | None:
    """Never unlink a path substituted for the temporary file we created."""
    if temporary is None:
        return None
    try:
        entry = temporary.path.lstat()
        if (
            not stat.S_ISREG(entry.st_mode)
            or _identity(entry, temporary.path) != temporary.identity
        ):
            return f"临时路径身份已变化，保留该路径：{temporary.path}"
        temporary.path.unlink()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"临时文件未能清理：{temporary.path}：{exc}"
    return None


def publish_text(temporary: OwnedTemporary, output: Path, *, plan: TextOutputPlan) -> None:
    """Recheck the plan; an absent preflight target NEVER gets replace permission."""
    output = freeze_path(output)
    expected = next((state for state in plan.outputs if state.path == output), None)
    if expected is None:
        raise TextWriteSafetyError("output_changed", "输出未在预检计划内", output)
    current_sources = tuple(_snapshot(source.path) for source in plan.sources)
    current = _snapshot(output)
    if any(_aliases(source, current) for source in (*plan.sources, *current_sources)):
        raise TextWriteSafetyError("source_output_conflict", "输出与输入原稿指向同一文件", output)
    for before, after in zip(plan.sources, current_sources, strict=True):
        if before.signature != after.signature or before.resolved != after.resolved:
            raise TextWriteSafetyError("input_changed", "输入原稿在预检后发生变化", before.path)
    _validate_output(current)
    if expected.signature != current.signature or expected.resolved != current.resolved:
        raise TextWriteSafetyError("output_changed", "输出路径在预检后发生变化，拒绝发布", output)
    temp_state = _snapshot(temporary.path)
    if temp_state.linked or not temp_state.regular or temp_state.identity != temporary.identity:
        raise TextWriteSafetyError(
            "temporary_changed", "临时路径身份已变化，拒绝发布", temporary.path
        )
    if any(_aliases(source, temp_state) for source in (*plan.sources, *current_sources)):
        raise TextWriteSafetyError(
            "source_output_conflict", "临时文件与输入原稿同一", temporary.path
        )
    if expected.identity is not None:
        if not plan.overwrite:
            raise TextWriteSafetyError("output_exists", "输出文件已存在", output)
        os.replace(temporary.path, output)
    else:
        try:
            os.link(temporary.path, output)
        except FileExistsError:
            raise
        except OSError:
            if not _WINDOWS:
                raise
            # Unlike POSIX rename, Windows rename refuses an existing destination.
            # Never fall back to os.replace or a truncating copy for a new target.
            os.rename(temporary.path, output)
