"""Strict parsing and deterministic identity for the source CSV"""

import ast
import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO
from uuid import UUID, uuid5

DOCUMENT_UUID_NAMESPACE = UUID("3d0cb153-b2d5-5c1c-b89f-eea840b2ac6b")
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
REQUIRED_COLUMNS = ("text", "created_date", "rubrics")


class CsvImportError(ValueError):
    """The source file does not match the documented CSV contract"""

    def __init__(
        self,
        message: str,
        *,
        record_number: int | None = None,
        physical_line: int | None = None,
        field: str | None = None,
    ) -> None:
        self.record_number = record_number
        self.physical_line = physical_line
        self.field = field

        location: list[str] = []
        if record_number is not None:
            location.append(f"record {record_number}")
        if physical_line is not None:
            location.append(f"physical line {physical_line}")
        if field is not None:
            location.append(f"field {field!r}")
        prefix = f"CSV {', '.join(location)}: " if location else "CSV: "
        super().__init__(f"{prefix}{message}")


@dataclass(frozen=True, slots=True)
class ImportDocument:
    """One fully validated document ready for both storage backends"""

    id: UUID
    rubrics: tuple[str, ...]
    text: str
    created_date: datetime


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """A validated and globally deduplicated source file"""

    rows_read: int
    documents: tuple[ImportDocument, ...]

    @property
    def duplicate_rows(self) -> int:
        """Return the number of source rows collapsed by deterministic ID"""
        return self.rows_read - len(self.documents)


def parse_document_csv(path: Path) -> ImportPlan:
    """Read and validate a UTF-8 CSV file without mutating external systems"""
    with path.open(mode="r", encoding="utf-8-sig", newline="") as stream:
        return parse_document_csv_stream(stream)


def parse_document_csv_stream(stream: TextIO) -> ImportPlan:
    """Parse a CSV stream, preserving document text and rubric order exactly"""
    reader = csv.reader(stream, strict=True)
    try:
        header = next(reader, None)
        if header is None:
            raise CsvImportError("missing header")
        if header:
            header[0] = header[0].removeprefix("\ufeff")
        _validate_header(header)

        documents_by_id: dict[UUID, ImportDocument] = {}
        rows_read = 0
        for record_number, row in enumerate(reader, start=2):
            rows_read += 1
            if len(row) != len(header):
                raise CsvImportError(
                    f"expected {len(header)} fields, got {len(row)}",
                    record_number=record_number,
                    physical_line=reader.line_num,
                )
            values = dict(zip(header, row, strict=True))
            document = _parse_row(
                values,
                record_number=record_number,
                physical_line=reader.line_num,
            )
            existing = documents_by_id.get(document.id)
            if existing is not None and existing != document:
                raise CsvImportError(
                    "deterministic UUID collision",
                    record_number=record_number,
                    physical_line=reader.line_num,
                )
            documents_by_id.setdefault(document.id, document)
    except csv.Error as error:
        raise CsvImportError(
            str(error),
            physical_line=reader.line_num,
        ) from error

    return ImportPlan(
        rows_read=rows_read,
        documents=tuple(documents_by_id.values()),
    )


def make_document_id(
    *,
    text: str,
    created_date: datetime,
    rubrics: tuple[str, ...],
) -> UUID:
    """Derive a stable UUIDv5 from the canonical document representation"""
    canonical = json.dumps(
        {
            "created_date": created_date.isoformat(timespec="seconds"),
            "rubrics": list(rubrics),
            "text": text,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return uuid5(DOCUMENT_UUID_NAMESPACE, canonical)


def _validate_header(header: list[str]) -> None:
    if len(header) == len(REQUIRED_COLUMNS) and set(header) == set(REQUIRED_COLUMNS):
        return

    missing = sorted(set(REQUIRED_COLUMNS) - set(header))
    unexpected = sorted(set(header) - set(REQUIRED_COLUMNS))
    duplicates = sorted({column for column in header if header.count(column) > 1})
    details = []
    if missing:
        details.append(f"missing columns: {', '.join(missing)}")
    if unexpected:
        details.append(f"unexpected columns: {', '.join(unexpected)}")
    if duplicates:
        details.append(f"duplicate columns: {', '.join(duplicates)}")
    if not details:
        details.append("invalid columns")
    raise CsvImportError("; ".join(details))


def _parse_row(
    values: dict[str, str],
    *,
    record_number: int,
    physical_line: int,
) -> ImportDocument:
    text = values["text"]
    _reject_null_character(
        text,
        record_number=record_number,
        physical_line=physical_line,
        field="text",
    )
    created_date = _parse_created_date(
        values["created_date"],
        record_number=record_number,
        physical_line=physical_line,
    )
    rubrics = _parse_rubrics(
        values["rubrics"],
        record_number=record_number,
        physical_line=physical_line,
    )
    return ImportDocument(
        id=make_document_id(
            text=text,
            created_date=created_date,
            rubrics=rubrics,
        ),
        rubrics=rubrics,
        text=text,
        created_date=created_date,
    )


def _parse_created_date(
    value: str,
    *,
    record_number: int,
    physical_line: int,
) -> datetime:
    try:
        parsed = datetime.strptime(value, DATE_FORMAT)
    except ValueError as error:
        raise CsvImportError(
            f"expected format {DATE_FORMAT!r}",
            record_number=record_number,
            physical_line=physical_line,
            field="created_date",
        ) from error
    if parsed.strftime(DATE_FORMAT) != value:
        raise CsvImportError(
            f"expected format {DATE_FORMAT!r}",
            record_number=record_number,
            physical_line=physical_line,
            field="created_date",
        )
    return parsed


def _parse_rubrics(
    value: str,
    *,
    record_number: int,
    physical_line: int,
) -> tuple[str, ...]:
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError) as error:
        raise CsvImportError(
            "expected a Python list literal containing strings",
            record_number=record_number,
            physical_line=physical_line,
            field="rubrics",
        ) from error
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise CsvImportError(
            "expected a Python list literal containing only strings",
            record_number=record_number,
            physical_line=physical_line,
            field="rubrics",
        )
    for rubric in parsed:
        _reject_null_character(
            rubric,
            record_number=record_number,
            physical_line=physical_line,
            field="rubrics",
        )
    return tuple(parsed)


def _reject_null_character(
    value: str,
    *,
    record_number: int,
    physical_line: int,
    field: str,
) -> None:
    if "\x00" in value:
        raise CsvImportError(
            "NUL characters cannot be stored in PostgreSQL text fields",
            record_number=record_number,
            physical_line=physical_line,
            field=field,
        )
