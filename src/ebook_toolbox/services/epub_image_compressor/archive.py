"""Safe EPUB archive extraction, validation, and publication."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from urllib.parse import unquote
from xml.etree import ElementTree as ET

MIMETYPE_CONTENT = b"application/epub+zip"
CONTAINER_PATH = Path("META-INF/container.xml")
MAX_ARCHIVE_MEMBERS = 10_000
MAX_MEMBER_SIZE = 256 * 1024 * 1024
MAX_TOTAL_SIZE = 2 * 1024 * 1024 * 1024
MAX_TOTAL_COMPRESSED_SIZE = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1_000
MAX_TOTAL_COMPRESSION_RATIO = 200
_IS_WINDOWS = os.name == "nt"

_WINDOWS_ILLEGAL = frozenset('<>:"|?*')
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class ArchiveError(ValueError):
    """Raised when an EPUB archive is unsafe or structurally invalid."""


def _safe_destination(root: Path, name: str) -> Path:
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or "\\" in name:
        raise ArchiveError(f"Unsafe ZIP member path: {name}")
    destination = (root / Path(*pure.parts)).resolve()
    resolved_root = root.resolve()
    if destination != resolved_root and resolved_root not in destination.parents:
        raise ArchiveError(f"Unsafe ZIP member path: {name}")
    return destination


def _validate_archive_metadata(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ArchiveError("ZIP contains too many members")
    total_size = sum(info.file_size for info in infos)
    total_compressed = sum(info.compress_size for info in infos)
    if total_size > MAX_TOTAL_SIZE:
        raise ArchiveError("ZIP total uncompressed size exceeds the limit")
    if total_compressed > MAX_TOTAL_COMPRESSED_SIZE:
        raise ArchiveError("ZIP total compressed size exceeds the limit")
    for info in infos:
        if info.file_size > MAX_MEMBER_SIZE:
            raise ArchiveError(f"ZIP member is too large: {info.filename}")
        if info.file_size and (
            info.compress_size == 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
        ):
            raise ArchiveError(f"ZIP member compression ratio is unsafe: {info.filename}")
    if total_size and (
        total_compressed == 0 or total_size / total_compressed > MAX_TOTAL_COMPRESSION_RATIO
    ):
        raise ArchiveError("ZIP aggregate compression ratio is unsafe")


def validate_archive_limits(infos: list[zipfile.ZipInfo]) -> None:
    """Reject archives whose member sizes or compression ratios exceed safe limits."""

    _validate_archive_metadata(infos)


def _windows_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    is_directory = normalized.endswith("/")
    path_parts = parts[:-1] if is_directory else parts
    if (
        not normalized
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in path_parts)
    ):
        raise ArchiveError(f"Unsafe ZIP member path: {name}")
    result: list[str] = []
    for part in path_parts:
        has_illegal_character = any(
            ord(char) < 32 or char in _WINDOWS_ILLEGAL for char in part
        )
        if part != part.rstrip(" .") or has_illegal_character:
            raise ArchiveError(f"ZIP member is not safely representable on Windows: {name}")
        base = part.split(".", 1)[0].casefold()
        if base in _WINDOWS_RESERVED:
            raise ArchiveError(f"ZIP member uses a reserved Windows name: {name}")
        result.append(part.casefold())
    return tuple(result)


def _validate_member_paths(infos: list[zipfile.ZipInfo], target: Path) -> None:
    entries: dict[tuple[str, ...], bool] = {}
    for info in infos:
        _safe_destination(target, info.filename)
        parts = _windows_parts(info.filename)
        is_directory = info.is_dir() or info.filename.endswith(("/", "\\"))
        if parts in entries:
            raise ArchiveError(f"ZIP member path collision: {info.filename}")
        for index in range(1, len(parts)):
            if entries.get(parts[:index]) is False:
                raise ArchiveError(f"ZIP file/directory path conflict: {info.filename}")
        if not is_directory and any(key[: len(parts)] == parts for key in entries):
            raise ArchiveError(f"ZIP file/directory path conflict: {info.filename}")
        entries[parts] = is_directory


def safe_extract(epub: Path, target: Path) -> tuple[Path, ...]:
    """Validate and extract an EPUB, returning its OPF paths."""

    try:
        with zipfile.ZipFile(epub) as archive:
            infos = archive.infolist()
            validate_archive_limits(infos)
            _validate_member_paths(infos, target)
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ArchiveError(f"Corrupt ZIP member: {bad_member}")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ArchiveError("Duplicate ZIP member names are not supported")
            for info in infos:
                _safe_destination(target, info.filename)
            archive.extractall(target)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("Input is not a valid ZIP archive") from exc

    mimetype = target / "mimetype"
    if not mimetype.is_file() or mimetype.read_bytes() != MIMETYPE_CONTENT:
        raise ArchiveError("EPUB mimetype is missing or invalid")
    container = target / CONTAINER_PATH
    if not container.is_file():
        raise ArchiveError("META-INF/container.xml is missing")
    try:
        tree = ET.parse(container)
        rootfiles = [
            element.attrib.get("full-path")
            for element in tree.iter()
            if element.tag.rsplit("}", 1)[-1] == "rootfile"
        ]
    except ET.ParseError as exc:
        raise ArchiveError("META-INF/container.xml is invalid") from exc
    if not rootfiles or any(not path for path in rootfiles):
        raise ArchiveError("container.xml does not declare an OPF rootfile")

    opf_paths: list[Path] = []
    for path in rootfiles:
        assert path is not None
        decoded = unquote(path)
        opf = _safe_destination(target, decoded)
        if not opf.is_file():
            raise ArchiveError(f"Declared OPF does not exist: {path}")
        opf_paths.append(opf)
    return tuple(opf_paths)


def validate_output(path: Path | BinaryIO) -> None:
    """Validate ZIP integrity and minimum EPUB structure in a built output."""

    if hasattr(path, "seek"):
        path.seek(0)
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if not infos or infos[0].filename != "mimetype":
            raise ArchiveError("mimetype is not the first ZIP member")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise ArchiveError("mimetype must use ZIP_STORED")
        if archive.read("mimetype") != MIMETYPE_CONTENT:
            raise ArchiveError("EPUB mimetype is invalid")
        if archive.testzip() is not None:
            raise ArchiveError("Output ZIP integrity check failed")
        if CONTAINER_PATH.as_posix() not in archive.namelist():
            raise ArchiveError("Output is missing META-INF/container.xml")
        try:
            container = ET.fromstring(archive.read(CONTAINER_PATH.as_posix()))
        except ET.ParseError as exc:
            raise ArchiveError("Output container.xml is invalid") from exc
        opf_names = [
            element.attrib.get("full-path")
            for element in container.iter()
            if element.tag.rsplit("}", 1)[-1] == "rootfile"
        ]
        names = set(archive.namelist())
        if not opf_names or any(unquote(name or "") not in names for name in opf_names):
            raise ArchiveError("Output container.xml references a missing OPF")


def build_epub(root: Path, temporary_output: BinaryIO) -> None:
    """Build an EPUB through an already-open, exclusively created binary file."""

    temporary_output.seek(0)
    temporary_output.truncate(0)
    with zipfile.ZipFile(temporary_output, "w", allowZip64=True) as archive:
        mimetype = root / "mimetype"
        archive.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for file in sorted(root.rglob("*")):
            if file.is_file() and file != mimetype:
                archive.write(
                    file,
                    file.relative_to(root).as_posix(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
    temporary_output.flush()
    os.fsync(temporary_output.fileno())


def _temporary_path_is_owned(path: Path, identity: os.stat_result) -> bool:
    """Return whether *path* is still the regular file represented by *identity*."""

    if not stat.S_ISREG(identity.st_mode) or identity.st_ino == 0:
        return False
    try:
        current = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(current.st_mode)
        and current.st_ino != 0
        and os.path.samestat(identity, current)
    )


def ensure_owned_temporary(path: Path, identity: os.stat_result) -> None:
    """Reject a missing, replaced, non-regular, or unverifiable temporary path."""

    if not _temporary_path_is_owned(path, identity):
        raise ArchiveError(f"Temporary output ownership was lost: {path}")


def unlink_owned_temporary(path: Path, identity: os.stat_result) -> None:
    """Delete a temporary path only while it remains the file this call created."""

    if _temporary_path_is_owned(path, identity):
        path.unlink(missing_ok=True)


def _publish_staging_no_clobber(staging: Path, output: Path) -> None:
    """Atomically publish a complete sibling staging file without overwriting."""

    try:
        os.link(staging, output)
        return
    except FileExistsError:
        raise
    except OSError as link_error:
        if os.path.lexists(output):
            raise FileExistsError(f"Output already exists: {output}") from link_error
        if not _IS_WINDOWS:
            raise ArchiveError(
                "Atomic no-clobber publication is unavailable on this filesystem"
            ) from link_error

    # Windows rename is an atomic directory-entry operation and fails rather than
    # replacing an existing destination. Unlike os.replace, it preserves no-clobber.
    try:
        os.rename(staging, output)
    except FileExistsError:
        raise
    except OSError as rename_error:
        if os.path.lexists(output):
            raise FileExistsError(f"Output already exists: {output}") from rename_error
        raise ArchiveError(
            "Atomic no-clobber publication is unavailable on this filesystem"
        ) from rename_error


def publish_epub(
    temporary_output: Path,
    output: Path,
    *,
    overwrite: bool,
    temporary_identity: os.stat_result | None = None,
) -> None:
    """Atomically publish a validated temporary EPUB."""

    if temporary_identity is not None:
        ensure_owned_temporary(temporary_output, temporary_identity)
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output}")
    if overwrite:
        os.replace(temporary_output, output)
        return
    # A hard link provides atomic create-if-absent semantics. If it is unavailable,
    # copy to a sibling staging file first and only then attempt exclusive publication.
    try:
        os.link(temporary_output, output)
    except OSError:
        if temporary_identity is not None:
            ensure_owned_temporary(temporary_output, temporary_identity)
        staging_file = tempfile.NamedTemporaryFile(
            prefix=f".{output.name}.", suffix=".copying", dir=output.parent, delete=False
        )
        staging = Path(staging_file.name)
        staging_identity = None
        try:
            staging_identity = os.fstat(staging_file.fileno())
            with staging_file as destination, temporary_output.open("rb") as source:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            ensure_owned_temporary(staging, staging_identity)
            _publish_staging_no_clobber(staging, output)
        finally:
            if not staging_file.closed:
                staging_file.close()
            if staging_identity is not None:
                unlink_owned_temporary(staging, staging_identity)
