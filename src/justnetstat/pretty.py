"""Formatters that turn single SNMP values into short strings for table output.

These are ports of the pretty-printing procedures in the original Tcl
implementation (``justPretty.tcl``).  Enumerated values are expected as the
symbolic names defined by the modern IF-MIB and IANAifType-MIB.
"""

import re
from collections.abc import Callable
from ipaddress import IPv4Address, IPv4Network, IPv6Address
from typing import TypeVar

T = TypeVar("T")


def if_descr(descr: str) -> str:
    """Abbreviates an interface description (ifDescr) to save column space."""
    if not descr:
        return "none"
    descr = _substitute_first(descr, _VENDOR_NAME_ABBREVIATIONS)
    if descr in _LEGACY_DESCRIPTIONS:
        return _LEGACY_DESCRIPTIONS[descr]
    if descr.startswith("utp fast ethernet "):
        return "FaEth/UTP"
    descr = _substitute_first(descr, _DESCRIPTION_CLEANUPS)
    descr = descr.replace(" ", "-")
    return _substitute_first(descr, _DASHED_DESCRIPTION_ABBREVIATIONS)


def interface_name(name: str | None, descr: str | None) -> str:
    """Returns an interface's name for display, as a single word.

    Prefers ifName, which is short and needs no abbreviation.  Falls back to
    the abbreviated ifDescr.  Scripts split the output on whitespace, so any
    whitespace in the name becomes dashes.
    """
    text = name if name else if_descr(descr or "")
    return re.sub(r"\s+", "-", text)


def or_none(value: T | None, formatter: Callable[[T], str] = str) -> str:
    """Formats a value, showing a missing value as ``none`` like the Tcl implementation.

    :param value: The value, or ``None`` if it is missing
    :param formatter: The function that formats a value that is present
    """
    return "none" if value is None else formatter(value)


def if_type(name: str) -> str:
    """Abbreviates an IANAifType name."""
    return _IF_TYPE_ABBREVIATIONS.get(name, name)


def admin_status(name: str) -> str:
    """Abbreviates an ifAdminStatus name."""
    return _ADMIN_STATUS_ABBREVIATIONS.get(name, name)


def oper_status(name: str) -> str:
    """Abbreviates an ifOperStatus name."""
    return _OPER_STATUS_ABBREVIATIONS.get(name, name)


def mtu(value: int | None) -> str:
    """Formats an ifMtu value, showing missing or unlimited MTUs as ``none``."""
    if value is None or value == _UNLIMITED_MTU:
        return "none"
    return str(value)


def netmask(ip: IPv4Address | IPv6Address, prefix_length: int | None) -> str:
    """Formats the netmask of an address.

    IPv4 netmasks are dotted, as in the Tcl implementation.  IPv6 netmasks are
    prefix lengths, e.g. ``/64``.  Their users may want that changed, so every
    view formats netmasks here.
    """
    if prefix_length is None:
        return "none"
    if ip.version == 4:
        return str(IPv4Network(f"0.0.0.0/{prefix_length}").netmask)
    return f"/{prefix_length}"


def phys_address(address: bytes | None) -> str:
    """Formats a physical address (ifPhysAddress) as colon-separated lowercase hex."""
    if not address:
        return "(null)"
    return ":".join(f"{byte:02x}" for byte in address)


def speed(value: float, kilo: bool = False) -> str:
    """Formats a speed or rate with three significant digits and a unit suffix.

    Values too small for a suffix get a trailing space instead, so they line up
    with values that have one.

    :param value: The value in units per second, e.g. bits per second
    :param kilo: Show the value as a whole number of thousands, without a suffix
    :return: The formatted value
    """
    if kilo:
        return str(int(value / 1000))
    for divisor, suffix in _SPEED_UNITS:
        # Compare against the rounding threshold rather than the divisor itself,
        # so that e.g. 999999 becomes "1M" instead of "1e+03k"
        if value >= divisor * _ROUNDS_UP_TO_NEXT_UNIT:
            return f"{value / divisor:4.3g}{suffix}"
    if value < 0.001:
        return "0 "
    return f"{value:4.3g} "


def long_time(seconds: int) -> str:
    """Formats a non-negative duration, showing only its two most significant units.

    Examples: ``0:05`` (5 seconds), ``1:40`` (100 seconds), ``1h06m`` (1 hour,
    6 minutes), ``1d01h`` (1 day, 1 hour), ``1w1d`` (1 week, 1 day).
    """
    weeks, seconds = divmod(seconds, _SECONDS_PER_WEEK)
    days, seconds = divmod(seconds, _SECONDS_PER_DAY)
    hours, seconds = divmod(seconds, _SECONDS_PER_HOUR)
    minutes, seconds = divmod(seconds, _SECONDS_PER_MINUTE)
    if weeks:
        return f"{weeks}w{days}d"
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}:{seconds:02d}"


def _substitute_first(text: str, rules: tuple[tuple[str, str], ...]) -> str:
    """Applies (pattern, replacement) rules in order, each to its first match only.

    Like Tcl regular expressions, ``.`` also matches newlines.
    """
    for pattern, replacement in rules:
        text = re.sub(pattern, replacement, text, count=1, flags=re.DOTALL)
    return text


# Applied before anything else, so the legacy description lookup sees the result
_VENDOR_NAME_ABBREVIATIONS = (
    ("FastEthernet", "FE"),
    ("GigabitEthernet", "GE"),
    ("TenGigE", "TE"),
)

# Complete descriptions used by Cisco Catalyst switches of the late 1990s
_LEGACY_DESCRIPTIONS = {
    "short wave fiber gigabit ethernet": "GBIC/SX",
    "gigabit ethernet without GBIC installed": "No-GBIC",
    "no gbic present": "No-GBIC",
    "long haul fiber gigabit ethernet": "GBIC/LH",
    "unsupported gbic gigabit ethernet": "GBIC/??",
    "multi mode fiber fast ethernet": "FaEth/MM",
    "utp ethernet": "Ether/UTP",
}

# Applied before spaces are replaced by dashes.
# The patterns are copied verbatim from the Tcl, except that Python needs \Z
# where Tcl uses $ to match only at the very end.
_DESCRIPTION_CLEANUPS = (
    ("Ethernet", "Ether"),
    ("-B-Channel-", "-B-Ch-"),
    (r" layer\Z", ""),
    (" - 802.1q vlan subinterface", ""),
    ("-802.1Q vLAN subif", ""),
)

# Applied after spaces are replaced by dashes
_DASHED_DESCRIPTION_ABBREVIATIONS = (
    ("multi-mode-fiber-fddi", "Fddi/MM"),
    ("10/100-utp-ethernet-.*", "10/100UTP"),
    ("Virtual-Access", "V-Acc"),
    ("Virtual-Template", "V-Templ"),
)

# The Tcl tables also had keys for RFC 1213 names and raw numbers, which its
# modern MIBs never produced.  Those keys are left out, so values such as
# frameRelay and unknown pass through unabbreviated, just as in the Tcl.
_IF_TYPE_ABBREVIATIONS = {
    "ethernetCsmacd": "ether",
    "propPointToPointSerial": "prop-ser",
    "softwareLoopback": "loopback",
    "bgppolicyaccounting": "bgpacc",
    "atmSubInterface": "atm-sub",
    "propVirtual": "propVirt",
    "gigabitEthernet": "GiEth",
    "mplsTunnel": "mplsTun",
    "ieee8023adLag": "LinkAgg",
}

# Zino 1's traffic statistics skip interfaces whose admin status reads "off"
_ADMIN_STATUS_ABBREVIATIONS = {
    "up": "on",
    "down": "off",
}

_OPER_STATUS_ABBREVIATIONS = {
    "testing": "test",
    "dormant": "dorm",
    "lowerLayerDown": "lldn",
}

# The largest signed 32-bit integer.
# Some devices report it as the MTU of unlimited interfaces.
_UNLIMITED_MTU = 2**31 - 1

_SPEED_UNITS = (
    (1e9, "G"),
    (1e6, "M"),
    (1e3, "k"),
)
# The fraction of a unit at which three significant digits round up to a whole unit
_ROUNDS_UP_TO_NEXT_UNIT = 0.9995

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 60 * _SECONDS_PER_MINUTE
_SECONDS_PER_DAY = 24 * _SECONDS_PER_HOUR
_SECONDS_PER_WEEK = 7 * _SECONDS_PER_DAY
