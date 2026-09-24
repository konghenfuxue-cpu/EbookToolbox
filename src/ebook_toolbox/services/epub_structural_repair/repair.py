"""Single-book EPUB structural repair orchestration."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from ebook_toolbox.services.epub_image_compressor.archive import (
    MIMETYPE_CONTENT,
    ArchiveError,
    ensure_owned_temporary,
    publish_epub,
    unlink_owned_temporary,
    validate_archive_limits,
)

from .models import (
    RenamedResource,
    RepairError,
    RepairRequest,
    RepairResult,
    RepairWarning,
)
from .package import manifest_targets, repair_package, rootfiles, validate_repaired_epub
from .paths import TEXT_EXTENSIONS, build_name_map, rewrite_text, validate_source_member_names


class _RepairFailure(Exception):
    def __init__(self, code: str, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


def default_output_path(source: Path, output_directory: Path | None = None) -> Path:
    directory = output_directory if output_directory is not None else source.parent
    return directory / f"{source.stem}_repaired.epub"


def _paths_refer_to_same_file(source: Path, output: Path) -> bool:
    try:
        return source.samefile(output)
    except OSError:
        source_identity = os.path.normcase(os.path.abspath(source))
        output_identity = os.path.normcase(os.path.abspath(output.parent.resolve() / output.name))
        return source_identity == output_identity


def _validate_input_archive(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    validate_archive_limits(infos)
    files = validate_source_member_names(infos)
    for info in files:
        mode = info.external_attr >> 16 if info.create_system == 3 else 0
        file_type = stat.S_IFMT(mode)
        if file_type and not stat.S_ISREG(mode):
            raise ArchiveError(f"ZIP member is not a regular file: {info.filename}")
    bad_member = archive.testzip()
    if bad_member is not None:
        raise ArchiveError(f"Corrupt ZIP member: {bad_member}")
    names = {info.filename for info in files}
    if "mimetype" not in names or archive.read("mimetype").strip() != MIMETYPE_CONTENT:
        raise ArchiveError("EPUB mimetype is missing or invalid")
    if "META-INF/encryption.xml" in names:
        encryption = archive.read("META-INF/encryption.xml")
        if b"EncryptedData" in encryption:
            raise _RepairFailure(
                "encrypted_epub",
                "Encrypted EPUB resources are not modified by structural repair",
            )
    return files


def _reference_members(
    source: Path,
    reference: Path | None,
    source_archive: zipfile.ZipFile,
    source_infos: list[zipfile.ZipInfo],
    package_paths: list[str],
) -> tuple[zipfile.ZipFile | None, list[zipfile.ZipInfo], list[str]]:
    source_names = {info.filename for info in source_infos}
    missing: list[str] = []
    for opf_path in package_paths:
        if opf_path not in source_names:
            raise ArchiveError(f"container.xml references a missing OPF: {opf_path}")
        missing.extend(
            target
            for target in manifest_targets(source_archive.read(opf_path), opf_path)
            if target not in source_names
        )
    missing = list(dict.fromkeys(missing))
    if reference is None:
        return None, [], []
    if _paths_refer_to_same_file(source, reference):
        raise _RepairFailure(
            "invalid_reference", "Reference EPUB must differ from the source", reference
        )
    try:
        reference_archive = zipfile.ZipFile(reference)
    except (OSError, zipfile.BadZipFile) as exc:
        raise _RepairFailure(
            "invalid_reference", "Reference must be a readable ZIP/EPUB", reference
        ) from exc
    try:
        reference_infos = _validate_input_archive(reference_archive)
        by_name = {info.filename: info for info in reference_infos}
        unavailable = [name for name in missing if name not in by_name]
        if unavailable:
            raise _RepairFailure(
                "reference_resource_mismatch",
                "Reference EPUB does not contain the exact manifest path: " + unavailable[0],
                reference,
            )
        selected = [by_name[name] for name in missing]
        return reference_archive, selected, missing
    except Exception:
        reference_archive.close()
        raise


def _copy_info(info: zipfile.ZipInfo, filename: str) -> zipfile.ZipInfo:
    copied = zipfile.ZipInfo(filename, info.date_time)
    copied.compress_type = zipfile.ZIP_DEFLATED
    copied.external_attr = info.external_attr
    copied.internal_attr = info.internal_attr
    copied.create_system = info.create_system
    copied.comment = info.comment
    copied.extra = info.extra
    return copied


def _run_calibre_check(path: Path, result: RepairResult) -> None:
    executable = shutil.which("ebook-meta")
    if not executable:
        result.calibre_status = "not_installed"
        result.warnings.append(
            RepairWarning(
                "calibre_not_installed",
                "Calibre ebook-meta was not found; internal validation passed",
            )
        )
        return
    try:
        completed = subprocess.run(
            [executable, str(path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.calibre_status = "check_failed"
        result.warnings.append(RepairWarning("calibre_check_failed", str(exc)))
        return
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip() or "ebook-meta failed"
        result.calibre_status = "check_failed"
        result.warnings.append(RepairWarning("calibre_check_failed", message))
    else:
        result.calibre_status = "passed"


def _build_repaired_epub(
    temporary_file,
    source_archive: zipfile.ZipFile,
    source_infos: list[zipfile.ZipInfo],
    reference_archive: zipfile.ZipFile | None,
    reference_infos: list[zipfile.ZipInfo],
    mapping: dict[str, str],
    package_paths: list[str],
    result: RepairResult,
    wrap_image_spine: bool,
) -> None:
    available = set(mapping.values())
    generated_resources: dict[str, bytes] = {}
    providers: dict[str, tuple[zipfile.ZipFile, zipfile.ZipInfo]] = {
        info.filename: (source_archive, info) for info in source_infos
    }
    if reference_archive is not None:
        providers.update({info.filename: (reference_archive, info) for info in reference_infos})
    temporary_file.seek(0)
    temporary_file.truncate(0)
    with zipfile.ZipFile(temporary_file, "w", allowZip64=True) as output_archive:
        output_archive.writestr("mimetype", MIMETYPE_CONTENT, compress_type=zipfile.ZIP_STORED)
        ordered = sorted(
            (old for old in providers if old != "mimetype"),
            key=lambda old: (mapping[old].casefold(), mapping[old]),
        )
        for old in ordered:
            provider, info = providers[old]
            new = mapping[old]
            data = provider.read(info)
            if PurePosixPath(old).suffix.casefold() in TEXT_EXTENSIONS:
                data = rewrite_text(data, old, new, mapping)
            if old in package_paths:
                changes = repair_package(data, new, available, wrap_image_spine=wrap_image_spine)
                data = changes.data
                result.added_manifest_resources.extend(changes.added_resources)
                result.removed_manifest_items.extend(changes.removed_manifest_items)
                result.removed_spine_items.extend(changes.removed_spine_items)
                result.detected_image_spine_items.extend(changes.detected_image_spine_items)
                result.wrapped_image_spine_items.extend(changes.wrapped_image_spine_items)
                generated_resources.update(changes.generated_resources)
                result.language_added = result.language_added or changes.language_added
            output_archive.writestr(_copy_info(info, new), data)
        for target, data in sorted(generated_resources.items()):
            output_archive.writestr(target, data, compress_type=zipfile.ZIP_DEFLATED)
    temporary_file.flush()
    os.fsync(temporary_file.fileno())


def repair_epub_structure(request: RepairRequest) -> RepairResult:
    """Repair one EPUB and atomically publish a validated new archive."""

    source = Path(request.source)
    output = Path(request.output) if request.output is not None else default_output_path(source)
    result = RepairResult(source=source, output=output)
    try:
        if not source.is_file() or source.suffix.casefold() != ".epub":
            raise _RepairFailure("invalid_source", "Source must be an existing .epub file", source)
        source = source.resolve()
        result.source = source
        result.output = output
        if _paths_refer_to_same_file(source, output):
            raise _RepairFailure("source_output_same", "Output must not be the source EPUB")
        if os.path.lexists(output) and not request.overwrite:
            raise _RepairFailure("output_exists", f"Output already exists: {output}", output)
        if not output.parent.is_dir():
            raise _RepairFailure(
                "invalid_output_directory", f"Output directory does not exist: {output.parent}"
            )
        reference = Path(request.reference).resolve() if request.reference is not None else None

        try:
            source_archive = zipfile.ZipFile(source)
        except zipfile.BadZipFile as exc:
            raise ArchiveError("Input is not a valid ZIP archive") from exc
        with source_archive:
            source_infos = _validate_input_archive(source_archive)
            package_paths = rootfiles(source_archive)
            reference_archive, reference_infos, restored = _reference_members(
                source,
                reference,
                source_archive,
                source_infos,
                package_paths,
            )
            try:
                combined_infos = [*source_infos, *reference_infos]
                mapping = build_name_map(combined_infos)
                result.renamed_resources.extend(
                    RenamedResource(old, new) for old, new in mapping.items() if old != new
                )
                result.restored_resources.extend(restored)
                temporary_file = tempfile.NamedTemporaryFile(
                    prefix=f".{output.name}.",
                    suffix=".tmp.epub",
                    dir=output.parent,
                    delete=False,
                )
                temporary_output = Path(temporary_file.name)
                temporary_identity = None
                try:
                    temporary_identity = os.fstat(temporary_file.fileno())
                    with temporary_file:
                        ensure_owned_temporary(temporary_output, temporary_identity)
                        _build_repaired_epub(
                            temporary_file,
                            source_archive,
                            source_infos,
                            reference_archive,
                            reference_infos,
                            mapping,
                            package_paths,
                            result,
                            request.wrap_image_spine,
                        )
                        ensure_owned_temporary(temporary_output, temporary_identity)
                        stats = validate_repaired_epub(temporary_file)
                        ensure_owned_temporary(temporary_output, temporary_identity)
                    result.file_count = stats["files"]
                    result.manifest_count = stats["manifest"]
                    result.spine_count = stats["spine"]
                    if request.run_calibre_check:
                        _run_calibre_check(temporary_output, result)
                        ensure_owned_temporary(temporary_output, temporary_identity)
                    publish_epub(
                        temporary_output,
                        output,
                        overwrite=request.overwrite,
                        temporary_identity=temporary_identity,
                    )
                finally:
                    if not temporary_file.closed:
                        temporary_file.close()
                    if temporary_identity is not None:
                        unlink_owned_temporary(temporary_output, temporary_identity)
            finally:
                if reference_archive is not None:
                    reference_archive.close()

        if result.added_manifest_resources:
            result.warnings.append(
                RepairWarning(
                    "manifest_resources_added",
                    f"Added {len(result.added_manifest_resources)} unregistered resources",
                )
            )
        if result.removed_manifest_items:
            result.warnings.append(
                RepairWarning(
                    "invalid_manifest_items_removed",
                    f"Removed {len(result.removed_manifest_items)} invalid manifest items",
                )
            )
        if result.removed_spine_items:
            result.warnings.append(
                RepairWarning(
                    "invalid_spine_items_removed",
                    f"Removed {len(result.removed_spine_items)} invalid spine items",
                )
            )
        if result.detected_image_spine_items:
            if result.wrapped_image_spine_items:
                result.warnings.append(
                    RepairWarning(
                        "image_spine_wrapped",
                        "Wrapped "
                        f"{len(result.wrapped_image_spine_items)} direct image spine items "
                        "in generated XHTML documents",
                    )
                )
            else:
                result.warnings.append(
                    RepairWarning(
                        "image_spine_detected",
                        "Detected "
                        f"{len(result.detected_image_spine_items)} direct image spine items; "
                        "enable XHTML wrapping to repair them",
                    )
                )
        result.success = True
    except _RepairFailure as exc:
        result.errors.append(RepairError(exc.code, str(exc), exc.path))
    except ArchiveError as exc:
        result.errors.append(RepairError("archive_error", str(exc), source))
    except FileExistsError as exc:
        result.errors.append(RepairError("output_exists", str(exc), output))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        result.errors.append(RepairError(type(exc).__name__, str(exc)))
    except Exception as exc:
        result.errors.append(RepairError("unexpected_error", str(exc)))
    return result
