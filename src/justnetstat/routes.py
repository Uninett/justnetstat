"""The routing table view of ``justnetstat -r``"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address

from netsnmpy.errors import SNMPError
from netsnmpy.oids import OID
from netsnmpy.session import SNMPSession

from justnetstat import pretty
from justnetstat.interfaces import Interface, RetrievalError
from justnetstat.snmp import Row, walk_columns
from justnetstat.table import Column, render_table

IPAddress = IPv4Address | IPv6Address


@dataclass(frozen=True)
class Route:
    """A route, as described by IP-FORWARD-MIB's inetCidrRouteTable.

    Attributes are ``None`` when the agent has no value for them.
    Enumerated values hold their MIB names.
    """

    destination: IPAddress
    prefix_length: int
    next_hop: IPAddress | None = None
    if_index: int | None = None
    type: str | None = None
    protocol: str | None = None


def parse_prefix(text: str) -> tuple[int, ...]:
    """Parses the route prefix argument of ``-r``, e.g. ``10.1`` or ``192.0.2.0``.

    Like the Tcl implementation, leading and trailing dots are ignored, an
    empty prefix means all routes, and each component is taken modulo 256.
    Routes are listed when their destination starts with those components.

    :raises ValueError: If the text is not dotted decimal components
    """
    text = text.strip(".")
    if not re.fullmatch(r"([0-9]+(\.[0-9]+)*)?", text):
        raise ValueError(f"{text} is not an IP-prefix")
    return tuple(int(component) % 256 for component in text.split(".") if text)


async def fetch_routes(
    session: SNMPSession,
    prefix: Sequence[int] = (),
    max_routes: int | None = None,
) -> list[Route]:
    """Fetches IPv4 routes from an agent's inetCidrRouteTable, in index order.

    The index orders routes by destination, then prefix length.  Like the Tcl
    implementation, which read the IPv4-only ipRouteTable, a whole address as
    the prefix fetches a single route, and a longer prefix fetches none.

    :param session: An open session
    :param prefix: Only fetch routes whose destination starts with these octets
    :param max_routes: Stop after this many routes
    :return: The routes.  Rows with malformed indexes are skipped.
    :raises RetrievalError: If the agent stops answering, or the request fails
    """
    if len(prefix) > 4:
        return []
    if len(prefix) == 4:
        max_routes = 1
    try:
        table = await walk_columns(
            session,
            *_ROUTE_COLUMNS,
            index_prefix=(_IPV4, 4, *prefix),
            max_rows=max_routes,
        )
    except TimeoutError as error:
        # Timeout term from the Tcl implementation
        raise RetrievalError("inetCidrRouteTable", "noResponse") from error
    except SNMPError as error:
        raise RetrievalError("inetCidrRouteTable", str(error)) from error
    routes = (_make_route(index, row) for index, row in table.items())
    return [route for route in routes if route is not None]


def render(
    routes: Sequence[Route],
    interfaces: Sequence[Interface],
    names: Mapping[IPAddress, str] | None = None,
    header: bool = True,
) -> list[str]:
    """Renders routes as lines of text.

    :param routes: The routes to list, in the order to list them
    :param interfaces: The agent's interfaces, to show routes' interfaces by name
    :param names: Names to show instead of gateway addresses, e.g. from DNS
    :param header: Whether to start with a line of column headers
    :return: The lines of the list
    """
    interface_names = {
        interface.index: interface.display_name for interface in interfaces
    }
    names = names or {}
    rows = (_row(route, interface_names, names) for route in routes)
    return render_table(_COLUMNS, rows, header=header)


def _row(
    route: Route, interface_names: Mapping[int, str], names: Mapping[IPAddress, str]
) -> list[str]:
    return [
        f"{route.destination}/{route.prefix_length}",
        _gateway(route.next_hop, names),
        _flags(route),
        _interface(route.if_index, interface_names),
        pretty.or_none(route.protocol, _protocol),
    ]


def _gateway(next_hop: IPAddress | None, names: Mapping[IPAddress, str]) -> str:
    # ipRouteNextHop, which the Tcl implementation showed, was 0.0.0.0 for none
    if next_hop is None:
        return "0.0.0.0"
    return names.get(next_hop, str(next_hop))


def _flags(route: Route) -> str:
    """Returns a route's flags like the Tcl implementation: U(p), G(ateway), H(ost).

    inetCidrRouteType numbers its types like ipRouteType, which the Tcl
    implementation read.  So reject routes get no U, like invalid ones, and
    remote routes get ``UG``, like indirect ones.  Other types get ``U``.
    """
    flags = {"reject": "", "remote": "UG"}.get(route.type or "", "U")
    if route.prefix_length == route.destination.max_prefixlen:
        flags += "H"
    return flags


def _interface(if_index: int | None, interface_names: Mapping[int, str]) -> str:
    if if_index is None:
        return "none"
    return interface_names.get(if_index, f"if({if_index})")


def _protocol(name: str) -> str:
    return _TCL_PROTOCOL_NAMES.get(name, name)


def _make_route(index: OID, row: Row) -> Route | None:
    decoded = _decode_route_index(index)
    if decoded is None:
        return None
    destination, prefix_length, next_hop = decoded
    return Route(
        destination=destination,
        prefix_length=prefix_length,
        next_hop=next_hop,
        if_index=row.get("IP-FORWARD-MIB::inetCidrRouteIfIndex"),
        type=row.get("IP-FORWARD-MIB::inetCidrRouteType"),
        protocol=row.get("IP-FORWARD-MIB::inetCidrRouteProto"),
    )


def _decode_route_index(
    index: Sequence[int],
) -> tuple[IPAddress, int, IPAddress | None] | None:
    """Decodes an inetCidrRouteTable index.

    The index is the destination (an address type, then a length-prefixed
    address), the prefix length, the policy (a length-prefixed OID), and the
    next hop (like the destination).
    A next hop of unknown type and no length means the route has none.

    :return: The destination, prefix length and next hop, or ``None`` if the index
        is malformed, or the destination is not an IPv4 or IPv6 address
    """
    try:
        destination, position = _take_address(index, 0)
        prefix_length = index[position]
        policy_length = index[position + 1]
        next_hop, position = _take_address(index, position + 2 + policy_length)
    except (IndexError, ValueError):
        return None
    if (
        destination is None
        or position != len(index)
        or prefix_length > destination.max_prefixlen
    ):
        return None
    return destination, prefix_length, next_hop


def _take_address(index: Sequence[int], start: int) -> tuple[IPAddress | None, int]:
    """Decodes an InetAddressType and InetAddress pair inside an index.

    The zone index of zoned addresses is dropped.

    :return: The address, ``None`` for an empty address of unknown type, and the
        position after the address in the index
    """
    address_type, length = index[start], index[start + 1]
    end = start + 2 + length
    octets = index[start + 2 : end]
    if len(octets) != length or not all(0 <= octet <= 255 for octet in octets):
        raise ValueError("malformed address")
    if address_type == _UNKNOWN and length == 0:
        return None, end
    size = _INET_ADDRESS_SIZES.get(address_type)
    if size is None or length < size:
        raise ValueError(f"unsupported address type {address_type}")
    return ip_address(bytes(octets[:size])), end


_ROUTE_COLUMNS = (
    "IP-FORWARD-MIB::inetCidrRouteIfIndex",
    "IP-FORWARD-MIB::inetCidrRouteType",
    "IP-FORWARD-MIB::inetCidrRouteProto",
)

# InetAddressType values
_UNKNOWN = 0
_IPV4 = 1

# Address sizes by InetAddressType: ipv4, ipv6, ipv4z and ipv6z.
# Zoned addresses carry a four-byte zone index after the address itself.
_INET_ADDRESS_SIZES = {1: 4, 2: 16, 3: 4, 4: 16}

# The Tcl implementation's names, from RFC1213-MIB's ipRouteProto, where IANA's
# IANAipRouteProtocol renamed them.  It showed local, i.e. manual, as local/static.
# RFC1213-MIB had no names for later protocols, so it showed their numbers.
_TCL_PROTOCOL_NAMES = {
    "local": "local/static",
    "isIs": "is-is",
    "esIs": "es-is",
    "idpr": "15",
    "ciscoEigrp": "16",
    "dvmrp": "17",
}

# The minimum widths match the Tcl format string
_COLUMNS = [
    Column("Destination (pfx/len)", 21),
    Column("Gateway", 22),
    Column("Flags", 5),
    Column("Interface", 15),
    Column("Protocol", 8),
]
