"""Fetching of interface data from network equipment"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network, IPv6Address, ip_address

from netsnmpy.errors import SNMPError
from netsnmpy.oids import OID
from netsnmpy.session import SNMPSession

from justnetstat import pretty
from justnetstat.snmp import Row, Table, walk_columns

IPAddress = IPv4Address | IPv6Address


@dataclass(frozen=True)
class Address:
    """An IP address assigned to an interface"""

    ip: IPAddress
    prefix_length: int | None = None

    def __str__(self) -> str:
        return str(self.ip)


@dataclass
class Interface:
    """An interface, as described by IF-MIB and IP-MIB.

    Attributes are ``None`` when the agent has no value for them.
    Enumerated values hold their MIB names.
    ``addresses`` lists IPv4 addresses before IPv6 ones, lowest first.
    """

    index: int
    name: str | None = None
    descr: str | None = None
    type: str | None = None
    mtu: int | None = None
    speed: int | None = None
    phys_address: bytes | None = None
    admin_status: str | None = None
    oper_status: str | None = None
    alias: str | None = None
    addresses: list[Address] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """The interface's name as views show it: a single word, ifName if any"""
        return pretty.interface_name(self.name, self.descr)

    @property
    def first_ipv4_address(self) -> Address | None:
        """The interface's lowest IPv4 address, if any.

        Views show this address when not asked to list them all.  Zino 1's
        traffic statistics build file names from it, so it must never be an
        IPv6 address.
        """
        return next(
            (address for address in self.addresses if address.ip.version == 4), None
        )


class RetrievalError(Exception):
    """Raised when a table cannot be retrieved from an agent"""

    def __init__(self, table: str, reason: str):
        super().__init__(f"Could not retrieve {table}; reason {reason}")
        self.table = table
        self.reason = reason


async def fetch_interfaces(
    session: SNMPSession, with_addresses: bool = True
) -> list[Interface]:
    """Fetches all interfaces from an agent, in ifIndex order.

    :param session: An open session
    :param with_addresses: Whether to also fetch the IP addresses of each interface
    :return: The interfaces
    :raises RetrievalError: If the agent stops answering, or the request fails
    """
    table = await _walk("ifTable", session, *_INTERFACE_COLUMNS)
    interfaces = [_make_interface(index, row) for index, row in table.items()]
    if with_addresses:
        addresses = await fetch_addresses(session)
        for interface in interfaces:
            interface.addresses = addresses.get(interface.index, [])
    return interfaces


async def fetch_addresses(session: SNMPSession) -> dict[int, list[Address]]:
    """Fetches the IP addresses of all interfaces of an agent.

    Uses the modern ipAddressTable, which also has IPv6 addresses.  When it
    has no IPv4 addresses, they come from the IPv4-only ipAddrTable instead.
    That covers agents that lack ipAddressTable, or only list IPv6 addresses
    in it.

    :param session: An open session
    :return: Addresses by ifIndex, IPv4 before IPv6, lowest first
    :raises RetrievalError: If the agent stops answering, or the request fails
    """
    assignments = _parse_ip_address_table(
        await _walk("ipAddressTable", session, *_IP_ADDRESS_COLUMNS)
    )
    if not any(address.ip.version == 4 for _, address in assignments):
        assignments += _parse_ip_addr_table(
            await _walk("ipAddrTable", session, *_IP_ADDR_COLUMNS)
        )
    addresses: dict[int, list[Address]] = {}
    for if_index, address in sorted(
        assignments, key=lambda assignment: _address_order(assignment[1])
    ):
        addresses.setdefault(if_index, []).append(address)
    return addresses


async def _walk(table_name: str, session: SNMPSession, *columns: str) -> Table:
    try:
        return await walk_columns(session, *columns)
    except TimeoutError as error:
        # Scotty's word for a timeout, which scripts parsing the Tcl output look for
        raise RetrievalError(table_name, "noResponse") from error
    except SNMPError as error:
        raise RetrievalError(table_name, str(error)) from error


def _make_interface(index: OID, row: Row) -> Interface:
    return Interface(
        index=index[0],
        name=row.get("IF-MIB::ifName"),
        descr=row.get("IF-MIB::ifDescr"),
        type=row.get("IF-MIB::ifType"),
        mtu=row.get("IF-MIB::ifMtu"),
        speed=_speed(row.get("IF-MIB::ifSpeed"), row.get("IF-MIB::ifHighSpeed")),
        phys_address=row.get("IF-MIB::ifPhysAddress"),
        admin_status=row.get("IF-MIB::ifAdminStatus"),
        oper_status=row.get("IF-MIB::ifOperStatus"),
        alias=row.get("IF-MIB::ifAlias"),
    )


def _speed(if_speed: int | None, if_high_speed: int | None) -> int | None:
    """Returns an interface's speed in bits per second.

    ifSpeed saturates at its maximum value for interfaces faster than it can
    express, in which case ifHighSpeed has the speed in millions of bits per second.
    """
    if if_speed == _MAX_GAUGE32 and if_high_speed is not None:
        return if_high_speed * 1_000_000
    return if_speed


def _parse_ip_address_table(table: Table) -> list[tuple[int, Address]]:
    """Parses ipAddressTable rows, skipping malformed rows and non-IP addresses.

    Broadcast and anycast addresses are skipped too, since ipAddrTable, which
    the Tcl implementation read, only had the interfaces' own addresses.
    """
    assignments = []
    for index, row in table.items():
        ip = _decode_address_index(index)
        if_index = row.get("IP-MIB::ipAddressIfIndex")
        if ip is None or if_index is None:
            continue
        if row.get("IP-MIB::ipAddressType", "unicast") != "unicast":
            continue
        assignments.append(
            (
                if_index,
                Address(ip, _prefix_length(row.get("IP-MIB::ipAddressPrefix"), ip)),
            )
        )
    return assignments


def _decode_address_index(index: OID) -> IPAddress | None:
    """Decodes an ipAddressTable index: address type, then length-prefixed address.

    The zone index of zoned addresses is dropped.  Returns ``None`` for
    malformed indexes, and for address types other than IPv4 and IPv6, e.g. DNS names.
    """
    if len(index) < 2:
        return None
    address_type, length, octets = index[0], index[1], index[2:]
    size = _INET_ADDRESS_SIZES.get(address_type)
    if (
        size is None
        or len(octets) != length
        or length < size
        or not _are_octets(octets)
    ):
        return None
    return ip_address(bytes(octets[:size]))


def _prefix_length(prefix_row: OID | None, ip: IPAddress) -> int | None:
    """Extracts the prefix length from a pointer to an ipAddressPrefixTable row.

    The prefix length is the last component of that row's index.  Agents
    without prefix information point to zeroDotZero instead.  Lengths too
    long for the address are ignored.
    """
    if prefix_row is None or not _IP_ADDRESS_PREFIX_ENTRY.is_a_prefix_of(prefix_row):
        return None
    if prefix_row[-1] > ip.max_prefixlen:
        return None
    return prefix_row[-1]


def _parse_ip_addr_table(table: Table) -> list[tuple[int, Address]]:
    """Parses ipAddrTable rows, which are indexed by their IPv4 address."""
    assignments = []
    for index, row in table.items():
        if_index = row.get("IP-MIB::ipAdEntIfIndex")
        if if_index is None or len(index) != 4 or not _are_octets(index):
            continue
        mask = row.get("IP-MIB::ipAdEntNetMask")
        assignments.append(
            (if_index, Address(IPv4Address(bytes(index)), _mask_length(mask)))
        )
    return assignments


def _are_octets(values: Sequence[int]) -> bool:
    return all(0 <= value <= 255 for value in values)


def _mask_length(mask: IPv4Address | None) -> int | None:
    """Converts a netmask into a prefix length.

    Returns ``None`` for missing or non-contiguous masks.
    """
    if mask is None:
        return None
    try:
        return IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except ValueError:
        return None


def _address_order(address: Address) -> tuple[int, int]:
    return address.ip.version, int(address.ip)


_INTERFACE_COLUMNS = (
    "IF-MIB::ifDescr",
    "IF-MIB::ifType",
    "IF-MIB::ifMtu",
    "IF-MIB::ifSpeed",
    "IF-MIB::ifPhysAddress",
    "IF-MIB::ifAdminStatus",
    "IF-MIB::ifOperStatus",
    "IF-MIB::ifName",
    "IF-MIB::ifHighSpeed",
    "IF-MIB::ifAlias",
)

_IP_ADDRESS_COLUMNS = (
    "IP-MIB::ipAddressIfIndex",
    "IP-MIB::ipAddressType",
    "IP-MIB::ipAddressPrefix",
)

_IP_ADDR_COLUMNS = ("IP-MIB::ipAdEntIfIndex", "IP-MIB::ipAdEntNetMask")

_IP_ADDRESS_PREFIX_ENTRY = OID(".1.3.6.1.2.1.4.32.1")

_MAX_GAUGE32 = 2**32 - 1

# Address sizes by InetAddressType: ipv4, ipv6, ipv4z and ipv6z.
# Zoned addresses carry a four-byte zone index after the address itself.
_INET_ADDRESS_SIZES = {1: 4, 2: 16, 3: 4, 4: 16}
