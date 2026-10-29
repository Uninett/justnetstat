"""Tests for the interface traffic views of ``justnetstat -B``

Expected output lines are kept whole, so they read like real output.  Their
``# noqa: E501`` tags let them exceed the line length limit.
"""

from ipaddress import ip_address

from justnetstat.interfaces import Address, Counters, Interface
from justnetstat.traffic import Rates, RateTable, rates, render_totals


class TestRenderTotals:
    def test_it_should_reproduce_the_tcl_row_layout(self):
        # The Tcl implementation's format string for `justnetstat -B` produced the row.
        # Its header was one character off from its rows, which is fixed here.
        lines = render_totals([LOOPBACK], {16: Counters(16000, 23, 32000, 11)})

        assert lines == [
            "Ix  Name         Address               ibytes      ipkts       obytes      opkts",  # noqa: E501
            "16  lo0.0        192.0.2.1              16000         23        32000         11",  # noqa: E501
        ]

    def test_when_counters_are_too_wide_for_their_columns_it_should_widen_them(self):
        lines = render_totals(
            [LOOPBACK], {16: Counters(12_345_678_901_234, 23, 32000, 11)}, header=False
        )

        assert lines == [
            "16  lo0.0        192.0.2.1         12345678901234         23        32000         11"  # noqa: E501
        ]

    def test_when_interface_has_no_counters_it_should_show_zeros(self):
        lines = render_totals([LOOPBACK], {}, header=False)

        assert lines[0].split()[3:] == ["0", "0", "0", "0"]


class TestRateTable:
    def test_it_should_reproduce_the_tcl_row_layout(self):
        # The Tcl implementation's format for `justnetstat -B -w 1` produced the row.
        # Its header was one character off from its rows, which is fixed here.
        previous = {16: Counters(0, 0, 0, 0)}
        current = {16: Counters(187_500, 120, 250_000_000, 64_000)}

        lines = RateTable().render([LOOPBACK], previous, current, interval=1)

        assert lines == [
            "Ix  Name         Address               ibit/s    ipkts/s       obit/s    opkts/s",  # noqa: E501
            "16  lo0.0        192.0.2.1               1.5M       120            2G        64k",  # noqa: E501
        ]

    def test_when_header_is_disabled_it_should_only_render_rows(self):
        lines = RateTable(header=False).render(
            [LOOPBACK], {16: Counters()}, {16: Counters()}, interval=1
        )

        assert len(lines) == 1

    def test_when_kilo_is_requested_it_should_show_whole_thousands(self):
        current = {16: Counters(187_500, 1_200, 0, 0)}

        lines = RateTable(header=False, kilo=True).render(
            [LOOPBACK], {16: Counters()}, current, interval=1
        )

        assert lines[0].split()[3:] == ["1500", "1", "0", "0"]

    def test_when_rates_shrink_it_should_keep_the_wider_columns(self):
        table = RateTable(header=False, kilo=True)
        busy = {
            16: Counters(10**14, 0, 0, 0)
        }  # 800000000000 kbit/s is wider than the ibit/s column

        first = table.render([LOOPBACK], {16: Counters()}, busy, interval=1)
        second = table.render([LOOPBACK], busy, busy, interval=1)

        assert len(first[0]) == len(second[0])

    def test_when_interface_lacks_a_sample_it_should_leave_it_out(self):
        lines = RateTable(header=False).render(
            [LOOPBACK, UPLINK], {16: Counters()}, {16: Counters(), 501: Counters()}, 1
        )

        assert [line.split()[0] for line in lines] == ["16"]


class TestRates:
    def test_it_should_return_bits_and_packets_per_second(self):
        assert rates(
            Counters(1000, 10, 2000, 20), Counters(2000, 30, 6000, 60), interval=2
        ) == Rates(4000, 10, 16000, 20)

    def test_when_32_bit_counters_wrap_around_it_should_count_across_the_wrap(self):
        # The Tcl implementation got a negative rate, and showed it as "0 "
        previous = Counters(2**32 - 1000, 0, 0, 0, bits=32)
        current = Counters(1000, 0, 0, 0, bits=32)

        assert rates(previous, current, interval=1).in_bits == 2000 * 8

    def test_when_counters_change_width_it_should_give_a_zero_rate(self):
        # As when a cut-short ifXTable walk makes some interfaces fall back on ifTable
        previous = Counters(10**12, 10**9, 0, 0, bits=64)
        current = Counters(1000, 10, 0, 0, bits=32)

        assert rates(previous, current, interval=1) == Rates(0, 0, 0, 0)

    def test_when_64_bit_counter_goes_backwards_it_should_give_a_zero_rate(self):
        # A reset counter.
        # The Tcl implementation got a negative rate, and showed it as "0 ".
        assert (
            rates(Counters(5000, 0, 0, 0), Counters(1000, 0, 0, 0), interval=1).in_bits
            == 0
        )


LOOPBACK = Interface(
    index=16, name="lo0.0", addresses=[Address(ip_address("192.0.2.1"), 32)]
)

UPLINK = Interface(
    index=501, name="xe-0/0/0.0", addresses=[Address(ip_address("198.51.100.1"), 30)]
)
