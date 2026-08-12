"""Safe EPUB archive extraction, validation, and publication."""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree as ET

MIMETYPE_CONTENT = b"application/epub+zip"
CONTAINER_PATH = Path("META-INF/container.xml")


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


def safe_extract(epub: Path, target: Path) -> tuple[Path, ...]:
    """Validate and extract an EPUB, returning its OPF paths."""

    try:
        with zipfile.ZipFile(epub) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ArchiveError(f"Corrupt ZIP member: {bad_member}")
            infos = archive.infolist()
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


def validate_output(path: Path) -> None:
    """Validate ZIP integrity and minimum EPUB structure in a built output."""

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


def build_epub(root: Path, temporary_output: Path) -> None:
    """Build and validate an EPUB at *temporary_output*."""

    temporary_output.unlink(missing_ok=True)
    try:
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
        validate_output(temporary_output)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        raise


def publish_epub(temporary_output: Path, output: Path, *, overwrite: bool) -> None:
    """Atomically publish a validated temporary EPUB."""

    if output.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output}")
    if overwrite:
        os.replace(temporary_output, output)
        return
    # A hard link provides atomic create-if-absent semantics; fall back to exclusive copy.
    try:
        os.link(temporary_output, output)
    except OSError:
        with temporary_output.open("rb") as source, output.open("xb") as destination:
            shutil.copyfileobj(source, destination)
    temporary_output.unlink()
