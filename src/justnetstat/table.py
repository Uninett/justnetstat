"""Rendering of aligned text tables"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal


def render_table(
    columns: Sequence["Column"], rows: Iterable[Sequence[str]], header: bool = True
) -> list[str]:
    """Renders rows of already formatted values as lines of an aligned text table.

    Each column is made wide enough for its header and its widest value, so
    headers and values always line up.  Columns are separated by at least one
    space, and trailing whitespace is removed.  All rows are read before any
    line is rendered, since any row can affect the column widths.

    :param columns: The table's column definitions
    :param rows: The table rows, each with one value per column
    :param header: Whether to start the table with a line of column headers
    :return: The table's lines
    :raises ValueError: If a row's number of values differs from the number of columns
    """
    lines = [[column.header for column in columns]] if header else []
    lines.extend(_check_row_length(columns, row) for row in rows)
    widths = _column_widths(columns, lines)
    return [_format_line(columns, widths, line) for line in lines]


def _check_row_length(columns: Sequence["Column"], row: Sequence[str]) -> Sequence[str]:
    if len(row) != len(columns):
        raise ValueError(f"Expected {len(columns)} values, got {len(row)}: {row!r}")
    return row


def _column_widths(
    columns: Sequence["Column"], lines: Sequence[Sequence[str]]
) -> list[int]:
    return [
        max([column.width, *(len(line[index]) for line in lines)])
        for index, column in enumerate(columns)
    ]


def _format_line(
    columns: Sequence["Column"], widths: Sequence[int], values: Sequence[str]
) -> str:
    cells = (
        f"{value:{column.align}{width}}"
        for column, width, value in zip(columns, widths, values)
    )
    return " ".join(cells).rstrip()


@dataclass(frozen=True)
class Column:
    """A table column definition.

    :param header: The column header text
    :param width: The minimum column width.  The column grows to fit wider headers
        or values.
    :param align: ``<`` to left-align values, ``>`` to right-align them
    """

    header: str
    width: int
    align: Literal["<", ">"] = "<"
