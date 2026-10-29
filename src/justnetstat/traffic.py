"""The interface traffic views of ``justnetstat -B``"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from justnetstat import pretty
from justnetstat.interfaces import Counters, Interface
from justnetstat.table import Column, fit_columns, render_table


def render_totals(
    interfaces: Sequence[Interface],
    counters: Mapping[int, Counters],
    header: bool = True,
) -> list[str]:
    """Renders the traffic counters of interfaces as lines of text.

    :param interfaces: The interfaces to list, in the order to list them
    :param counters: Counters by ifIndex.  Interfaces without counters show zeros,
        as in the Tcl implementation.
    :param header: Whether to start with a line of column headers
    :return: The lines of the list
    """
    rows = (
        _totals_row(interface, counters.get(interface.index, Counters()))
        for interface in interfaces
    )
    return render_table(_TOTALS_COLUMNS, rows, header=header)


class RateTable:
    """Renders rounds of traffic rates, computed from consecutive counter samples.

    Columns keep the widths of earlier rounds, so they line up from one round
    to the next.
    """

    def __init__(self, header: bool = True, kilo: bool = False):
        self.header = header
        self.kilo = kilo
        self.columns = _RATES_COLUMNS

    def render(
        self,
        interfaces: Sequence[Interface],
        previous: Mapping[int, Counters],
        current: Mapping[int, Counters],
        interval: float,
    ) -> list[str]:
        """Renders one round of rates.

        Rates are counter differences divided by the time between the samples.

        :param interfaces: The interfaces to list, in the order to list them
        :param previous: The previous sample of counters by ifIndex
        :param current: The current sample of counters by ifIndex
        :param interval: Seconds between the samples
        :return: The lines of this round
        """
        rows = [
            [
                *_identity(interface),
                *self._format(rates(previous[index], current[index], interval)),
            ]
            for interface in interfaces
            if (index := interface.index) in previous and index in current
        ]
        self.columns = fit_columns(self.columns, rows)
        return render_table(self.columns, rows, header=self.header)

    def _format(self, values: "Rates") -> list[str]:
        return [
            pretty.speed(value, kilo=self.kilo)
            for value in (
                values.in_bits,
                values.in_packets,
                values.out_bits,
                values.out_packets,
            )
        ]


@dataclass(frozen=True)
class Rates:
    """Traffic rates, per second"""

    in_bits: float
    in_packets: float
    out_bits: float
    out_packets: float


def rates(previous: Counters, current: Counters, interval: float) -> Rates:
    """Computes the traffic rates between two counter samples.

    32-bit counters often wrap around between samples on fast interfaces, so
    their differences are taken modulo 2^32.  A 64-bit counter that goes
    backwards has been reset, and gets a rate of zero.  So do counters that
    changed width between the samples, which cannot be compared.

    :param previous: The earlier sample
    :param current: The later sample
    :param interval: Seconds between the samples
    :return: The rates
    """

    def per_second(before: int, after: int) -> float:
        if previous.bits != current.bits:
            return 0.0
        difference = after - before
        if current.bits == 32:
            difference %= 2**32
        return max(0.0, difference / interval)

    return Rates(
        in_bits=per_second(previous.in_octets, current.in_octets) * 8,
        in_packets=per_second(previous.in_packets, current.in_packets),
        out_bits=per_second(previous.out_octets, current.out_octets) * 8,
        out_packets=per_second(previous.out_packets, current.out_packets),
    )


def _totals_row(interface: Interface, totals: Counters) -> list[str]:
    return [
        *_identity(interface),
        str(totals.in_octets),
        str(totals.in_packets),
        str(totals.out_octets),
        str(totals.out_packets),
    ]


def _identity(interface: Interface) -> list[str]:
    return [
        str(interface.index),
        interface.display_name,
        pretty.or_none(interface.first_ipv4_address),
    ]


# The widths reproduce the Tcl format strings, which leave no space before the
# Address and the first counter column.  The separator space makes up for it.
_TOTALS_COLUMNS = [
    Column("Ix", 3),
    Column("Name", 12),
    Column("Address", 17),
    Column("ibytes", 10, ">"),
    Column("ipkts", 10, ">"),
    Column("obytes", 12, ">"),
    Column("opkts", 10, ">"),
]

_RATES_COLUMNS = [
    Column("Ix", 3),
    Column("Name", 12),
    Column("Address", 17),
    Column("ibit/s", 10, ">"),
    Column("ipkts/s", 10, ">"),
    Column("obit/s", 12, ">"),
    Column("opkts/s", 10, ">"),
]
