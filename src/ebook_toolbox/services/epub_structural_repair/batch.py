"""Sequential batch scheduling for EPUB structural repair."""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from .models import (
    BatchRepairRequest,
    BatchRepairResult,
    FileRepairResult,
    RepairError,
    RepairRequest,
    RepairResult,
    RepairWarning,
)
from .repair import default_output_path, repair_epub_structure


def _stable_path_key(path: Path) -> tuple[str, str]:
    value = str(path)
    return value.casefold(), value


def _discover_inputs(
    inputs: tuple[Path, ...], recursive: bool, result: BatchRepairResult
) -> list[Path]:
    discovered: dict[str, Path] = {}
    for raw in inputs:
        path = Path(raw)
        try:
            resolved = path.resolve()
        except OSError as exc:
            result.warnings.append(RepairWarning("input_skipped", str(exc), path))
            continue
        candidates: list[Path] = []
        if resolved.is_file():
            if resolved.suffix.casefold() == ".epub":
                candidates.append(resolved)
            else:
                result.warnings.append(
                    RepairWarning("non_epub_skipped", "Input file is not an EPUB", resolved)
                )
        elif resolved.is_dir():
            try:
                iterator = resolved.rglob("*") if recursive else resolved.iterdir()
                candidates.extend(
                    candidate.resolve()
                    for candidate in iterator
                    if candidate.is_file() and candidate.suffix.casefold() == ".epub"
                )
            except OSError as exc:
                result.warnings.append(RepairWarning("input_scan_failed", str(exc), resolved))
        else:
            result.warnings.append(
                RepairWarning("input_skipped", "Input path does not exist", resolved)
            )
        for candidate in candidates:
            identity = os.path.normcase(os.path.abspath(candidate)).casefold()
            discovered.setdefault(identity, candidate)
    return sorted(discovered.values(), key=_stable_path_key)


def _failed_result(source: Path, output: Path, code: str, message: str) -> FileRepairResult:
    repair = RepairResult(source=source, output=output)
    repair.errors.append(RepairError(code, message, output))
    return FileRepairResult(source, output, repair)


def repair_epub_batch(request: BatchRepairRequest) -> BatchRepairResult:
    """Repair discovered EPUBs sequentially; one failure never stops later files."""

    result = BatchRepairResult()
    output_directory = (
        Path(request.output_directory) if request.output_directory is not None else None
    )
    if output_directory is not None:
        try:
            output_directory.mkdir(parents=True, exist_ok=True)
            if not output_directory.is_dir():
                raise NotADirectoryError(str(output_directory))
        except OSError as exc:
            result.errors.append(
                RepairError("invalid_output_directory", str(exc), output_directory)
            )
            return result

    sources = _discover_inputs(tuple(request.inputs), request.recursive, result)
    planned: list[tuple[Path, Path]] = [
        (source, default_output_path(source, output_directory)) for source in sources
    ]
    by_output: defaultdict[str, list[tuple[Path, Path]]] = defaultdict(list)
    for source, output in planned:
        key = os.path.normcase(os.path.abspath(output)).casefold()
        by_output[key].append((source, output))
    conflicts = {key: values for key, values in by_output.items() if len(values) > 1}

    for source, output in planned:
        output_key = os.path.normcase(os.path.abspath(output)).casefold()
        if output_key in conflicts:
            names = ", ".join(str(item[0]) for item in conflicts[output_key])
            result.files.append(
                _failed_result(
                    source,
                    output,
                    "batch_output_conflict",
                    f"Multiple inputs map to the same output: {names}",
                )
            )
            continue
        try:
            repair = repair_epub_structure(
                RepairRequest(
                    source=source,
                    output=output,
                    overwrite=request.overwrite,
                    reference=request.reference,
                    run_calibre_check=request.run_calibre_check,
                    wrap_image_spine=request.wrap_image_spine,
                )
            )
        except Exception as exc:
            repair = RepairResult(source=source, output=output)
            repair.errors.append(RepairError("unexpected_error", str(exc), source))
        result.files.append(FileRepairResult(source, output, repair))
    return result
