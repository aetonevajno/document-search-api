"""Tests for strict source CSV parsing and deterministic UUIDs"""

import csv
from datetime import datetime
from io import StringIO
from uuid import UUID

import pytest

from app.importers.csv_parser import (
    CsvImportError,
    make_document_id,
    parse_document_csv_stream,
)


def make_csv(
    rows: list[list[str]],
    *,
    header: list[str] | None = None,
    bom: bool = False,
) -> StringIO:
    """Create a standards-compliant in-memory CSV for parser tests"""
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(header or ["text", "created_date", "rubrics"])
    writer.writerows(rows)
    value = stream.getvalue()
    return StringIO(("\ufeff" if bom else "") + value, newline="")


def test_parser_preserves_unicode_multiline_text_and_bom() -> None:
    """Quoted multiline text and a UTF-8 BOM survive parsing unchanged"""
    stream = make_csv(
        [["Привет,\r\nмир", "2019-01-02 03:04:05", "['VK-1', 'рубрика']"]],
        bom=True,
    )

    plan = parse_document_csv_stream(stream)

    assert plan.rows_read == 1
    assert plan.duplicate_rows == 0
    assert len(plan.documents) == 1
    document = plan.documents[0]
    assert document.text == "Привет,\r\nмир"
    assert document.created_date == datetime(2019, 1, 2, 3, 4, 5)
    assert document.created_date.tzinfo is None
    assert document.rubrics == ("VK-1", "рубрика")


def test_parser_accepts_header_only_without_documents() -> None:
    """An empty but structurally valid data set produces an empty plan"""
    plan = parse_document_csv_stream(make_csv([]))

    assert plan.rows_read == 0
    assert plan.documents == ()
    assert plan.duplicate_rows == 0


@pytest.mark.parametrize(
    "header",
    [
        ["text", "created_date"],
        ["text", "created_date", "rubrics", "extra"],
        ["text", "created_date", "text"],
    ],
)
def test_parser_rejects_invalid_headers(header: list[str]) -> None:
    """Missing, unexpected, and duplicate columns fail before any import"""
    with pytest.raises(CsvImportError):
        parse_document_csv_stream(make_csv([], header=header))


def test_parser_accepts_columns_in_any_order() -> None:
    """Column names, rather than their positions, define the source contract"""
    stream = make_csv(
        [["[]", "text", "2019-01-02 03:04:05"]],
        header=["rubrics", "text", "created_date"],
    )

    document = parse_document_csv_stream(stream).documents[0]

    assert document.text == "text"
    assert document.rubrics == ()


@pytest.mark.parametrize(
    "rubrics",
    [
        "{'not': 'a list'}",
        "('tuple',)",
        "'string'",
        "[1]",
        "__import__('os').system('echo unsafe')",
    ],
)
def test_parser_rejects_invalid_rubrics_without_evaluation(rubrics: str) -> None:
    """Rubrics use literal parsing and must be exactly a list of strings"""
    stream = make_csv([["text", "2019-01-02 03:04:05", rubrics]])

    with pytest.raises(CsvImportError) as captured:
        parse_document_csv_stream(stream)

    assert captured.value.field == "rubrics"
    assert captured.value.record_number == 2


@pytest.mark.parametrize(
    "created_date",
    [
        "2019-01-02",
        "2019-1-2 03:04:05",
        "2019-01-02T03:04:05",
        "2019-01-02 03:04:05+00:00",
        "not-a-date",
    ],
)
def test_parser_rejects_noncanonical_dates(created_date: str) -> None:
    """Only the naive timestamp format present in the assignment is accepted"""
    stream = make_csv([["text", created_date, "[]"]])

    with pytest.raises(CsvImportError) as captured:
        parse_document_csv_stream(stream)

    assert captured.value.field == "created_date"


def test_parser_rejects_wrong_field_count() -> None:
    """Short and long records cannot silently shift document fields"""
    source = StringIO("text,created_date,rubrics\nvalue,2019-01-02 03:04:05\n")

    with pytest.raises(CsvImportError, match="expected 3 fields, got 2"):
        parse_document_csv_stream(source)


def test_parser_rejects_malformed_csv() -> None:
    """Unclosed quoted fields are reported as CSV contract errors"""
    source = StringIO('text,created_date,rubrics\n"unclosed,2019-01-02 03:04:05,[]\n')

    with pytest.raises(CsvImportError):
        parse_document_csv_stream(source)


def test_parser_globally_collapses_identical_rows() -> None:
    """Duplicates receive one stable UUID even when they are not adjacent"""
    row = ["same", "2019-01-02 03:04:05", "['A']"]
    stream = make_csv([row, ["different", "2019-01-02 03:04:05", "[]"], row])

    plan = parse_document_csv_stream(stream)

    assert plan.rows_read == 3
    assert len(plan.documents) == 2
    assert plan.duplicate_rows == 1


def test_parser_collapses_equivalent_rubric_literal_formatting() -> None:
    """Literal whitespace and quote style do not alter the parsed document"""
    stream = make_csv(
        [
            ["same", "2019-01-02 03:04:05", "['A', 'B']"],
            ["same", "2019-01-02 03:04:05", '["A","B"]'],
        ]
    )

    plan = parse_document_csv_stream(stream)

    assert plan.rows_read == 2
    assert len(plan.documents) == 1
    assert plan.duplicate_rows == 1


@pytest.mark.parametrize(
    ("text", "rubrics", "field"),
    [
        ("before\x00after", "[]", "text"),
        ("text", "['before\\x00after']", "rubrics"),
    ],
)
def test_parser_rejects_postgresql_incompatible_nul_characters(
    text: str,
    rubrics: str,
    field: str,
) -> None:
    """NUL bytes fail validation before PostgreSQL can reject a batch"""
    stream = make_csv([[text, "2019-01-02 03:04:05", rubrics]])

    with pytest.raises(CsvImportError, match="NUL") as captured:
        parse_document_csv_stream(stream)

    assert captured.value.field == field
    assert captured.value.record_number == 2


def test_document_uuid_has_a_stable_golden_value() -> None:
    """An accidental canonicalization change cannot rewrite every document ID"""
    document_id = make_document_id(
        text="Привет\nмир",
        created_date=datetime(2019, 1, 2, 3, 4, 5),
        rubrics=("VK-1", "рубрика"),
    )

    assert document_id == UUID("760a584e-187f-539d-957d-ca741171a875")


def test_document_uuid_changes_with_every_source_field() -> None:
    """Text, timestamp, rubrics, and rubric order all participate in identity"""
    base = make_document_id(
        text="text",
        created_date=datetime(2019, 1, 2, 3, 4, 5),
        rubrics=("A", "B"),
    )

    variants = {
        make_document_id(
            text="text ",
            created_date=datetime(2019, 1, 2, 3, 4, 5),
            rubrics=("A", "B"),
        ),
        make_document_id(
            text="text",
            created_date=datetime(2019, 1, 2, 3, 4, 6),
            rubrics=("A", "B"),
        ),
        make_document_id(
            text="text",
            created_date=datetime(2019, 1, 2, 3, 4, 5),
            rubrics=("B", "A"),
        ),
    }

    assert base not in variants
    assert len(variants) == 3
