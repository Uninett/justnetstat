"""Tests for aligned text table rendering

Expected output lines are kept whole, so they read like real output.  Their
``# noqa: E501`` tags let them exceed the line length limit.
"""

import pytest

from justnetstat.table import Column, render_table


class TestRenderTable:
    def test_when_values_fit_the_minimum_widths_it_should_reproduce_the_tcl_interface_list(  # noqa: E501
        self,
    ):
        # These lines were produced by the Tcl's format string for `justnetstat -i`.
        rows = [
            ["1", "fxp0", "on", "down", "none", "none", "ether", "1514"],
            [
                "16",
                "lo0.0",
                "on",
                "up",
                "192.0.2.1",
                "255.255.255.255",
                "loopback",
                "none",
            ],
        ]

        assert render_table(INTERFACE_LIST_COLUMNS, rows) == [
            "Ix  Name                 as   os Address         Netmask              Type   MTU",  # noqa: E501
            "1   fxp0                 on down none            none                ether  1514",  # noqa: E501
            "16  lo0.0                on   up 192.0.2.1       255.255.255.255  loopback  none",  # noqa: E501
        ]

    def test_when_a_value_is_wider_than_its_column_it_should_widen_the_whole_column(
        self,
    ):
        # The Tcl implementation's -B format "%-13s%-18s" ran such values into the next
        # column, e.g. "et-0/0/10.1000192.0.2.1"
        columns = [Column("Name", 4), Column("Address", 8)]
        rows = [["lo0", "192.0.2.1"], ["et-0/0/10.1000", "192.0.2.2"]]

        assert render_table(columns, rows) == [
            "Name           Address",
            "lo0            192.0.2.1",
            "et-0/0/10.1000 192.0.2.2",
        ]

    def test_when_right_aligned_values_are_wider_than_their_columns_it_should_keep_them_aligned(  # noqa: E501
        self,
    ):
        # The Tcl implementation's -C format "%5u%7u" showed the last row as
        # "1234561234567"
        columns = [Column("ierr", 5, ">"), Column("idisc", 7, ">")]
        rows = [["0", "0"], ["123456", "1234567"]]

        assert render_table(columns, rows) == [
            "  ierr   idisc",
            "     0       0",
            "123456 1234567",
        ]

    def test_when_a_header_is_wider_than_its_column_it_should_widen_the_column(self):
        columns = [Column("Proto", 4), Column("State", 5)]

        assert render_table(columns, [["tcp", "listen"]]) == [
            "Proto State",
            "tcp   listen",
        ]

    def test_when_header_is_disabled_it_should_only_render_rows(self):
        columns = [Column("Name", 4), Column("Value", 5)]

        assert render_table(columns, [["a", "b"]], header=False) == ["a    b"]

    def test_it_should_strip_trailing_whitespace(self):
        # The Tcl implementation's -r format ended in "%-8s", leaving trailing spaces
        columns = [Column("Name", 10), Column("Proto", 8)]

        assert render_table(columns, [["ae0.100", "bgp"]], header=False) == [
            "ae0.100    bgp"
        ]

    @pytest.mark.parametrize("row", [["a"], ["a", "b", "c"]])
    def test_when_a_row_has_the_wrong_number_of_values_it_should_raise(self, row):
        columns = [Column("Name", 4), Column("Value", 5)]

        with pytest.raises(ValueError):
            render_table(columns, [row])


INTERFACE_LIST_COLUMNS = [
    Column("Ix", 3),
    Column("Name", 19),
    Column("as", 3, ">"),
    Column("os", 4, ">"),
    Column("Address", 15),
    Column("Netmask", 15),
    Column("Type", 9, ">"),
    Column("MTU", 5, ">"),
]
