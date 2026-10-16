"""The interface list views of ``justnetstat -i``"""

from collections.abc import Sequence
from enum import Enum
from functools import partial

from justnetstat import pretty
from justnetstat.interfaces import Address, Interface
from justnetstat.table import Column, render_table


class Variant(Enum):
    """The variants of the interface list, selected by flags combined with ``-i``"""

    BASIC = "basic"
    MEDIA = "-m"
    DESCRIPTIONS = "-d"
    DESCRIPTIONS_AND_ADDRESSES = "-D"


def render(
    interfaces: Sequence[Interface],
    variant: Variant = Variant.BASIC,
    all_addresses: bool = False,
    header: bool = True,
    kilo: bool = False,
) -> list[str]:
    """Renders an interface list as lines of text.

    :param interfaces: The interfaces to list, in the order to list them
    :param variant: Which variant of the list to render
    :param all_addresses: List an interface once per address, instead of only with
        its lowest IPv4 address
    :param header: Whether to start with a line of column headers
    :param kilo: Show speeds as whole thousands of bits per second
    :return: The lines of the list
    """
    columns, make_row = _VARIANTS[variant]
    rows = (
        make_row(interface, address, kilo)
        for interface in interfaces
        for address in _listed_addresses(interface, all_addresses, variant)
    )
    return render_table(columns, rows, header=header)


def _listed_addresses(
    interface: Interface, all_addresses: bool, variant: Variant
) -> list[Address | None]:
    """Returns the addresses to list an interface with.

    ``None`` stands for a row without an address.
    """
    if variant is Variant.DESCRIPTIONS:
        return [None]
    if all_addresses:
        return interface.addresses or [None]
    return [interface.first_ipv4_address]


def _basic_row(interface: Interface, address: Address | None, kilo: bool) -> list[str]:
    return [
        str(interface.index),
        interface.display_name,
        pretty.or_none(interface.admin_status, pretty.admin_status),
        pretty.or_none(interface.oper_status, pretty.oper_status),
        pretty.or_none(address),
        pretty.netmask(address.ip, address.prefix_length) if address else "none",
        pretty.or_none(interface.type, pretty.if_type),
        pretty.mtu(interface.mtu),
    ]


def _media_row(interface: Interface, address: Address | None, kilo: bool) -> list[str]:
    return [
        str(interface.index),
        interface.display_name,
        pretty.or_none(address),
        pretty.phys_address(interface.phys_address),
        _speed(interface, kilo),
        pretty.or_none(interface.type, pretty.if_type),
        pretty.mtu(interface.mtu),
    ]


def _descriptions_row(
    interface: Interface, address: Address | None, kilo: bool
) -> list[str]:
    return [
        str(interface.index),
        interface.display_name,
        _speed(interface, kilo),
        _quoted_alias(interface),
    ]


def _descriptions_and_addresses_row(
    interface: Interface, address: Address | None, kilo: bool
) -> list[str]:
    return [
        str(interface.index),
        interface.display_name,
        pretty.or_none(address),
        _speed(interface, kilo),
        _quoted_alias(interface),
    ]


def _speed(interface: Interface, kilo: bool) -> str:
    return pretty.or_none(interface.speed, partial(pretty.speed, kilo=kilo))


def _quoted_alias(interface: Interface) -> str:
    if interface.alias is None:
        return '"(nil)"'
    return f'"{interface.alias}"'


# The minimum widths match the Tcl format strings
_BASIC_COLUMNS = [
    Column("Ix", 3),
    Column("Name", 19),
    Column("as", 3, ">"),
    Column("os", 4, ">"),
    Column("Address", 15),
    Column("Netmask", 15),
    Column("Type", 9, ">"),
    Column("MTU", 5, ">"),
]

_MEDIA_COLUMNS = [
    Column("Ix", 3),
    Column("Name", 19),
    Column("Address", 15),
    Column("Physaddr", 17),
    Column("Speed", 6, ">"),
    Column("Type", 9, ">"),
    Column("MTU", 5, ">"),
]

_DESCRIPTIONS_COLUMNS = [
    Column("Ix", 3),
    Column("Name", 5),
    Column("Speed", 6, ">"),
    Column("Descr", 0),
]

_DESCRIPTIONS_AND_ADDRESSES_COLUMNS = [
    Column("Ix", 3),
    Column("Name", 5),
    Column("Address", 15),
    Column("Speed", 6, ">"),
    Column("Descr", 0),
]

_VARIANTS = {
    Variant.BASIC: (_BASIC_COLUMNS, _basic_row),
    Variant.MEDIA: (_MEDIA_COLUMNS, _media_row),
    Variant.DESCRIPTIONS: (_DESCRIPTIONS_COLUMNS, _descriptions_row),
    Variant.DESCRIPTIONS_AND_ADDRESSES: (
        _DESCRIPTIONS_AND_ADDRESSES_COLUMNS,
        _descriptions_and_addresses_row,
    ),
}
