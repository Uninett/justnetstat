"""Tests for the interface list views of ``justnetstat -i``

Expected output lines are kept whole, so they read like real output.  Their
``# noqa: E501`` tags let them exceed the line length limit.
"""

import re
from ipaddress import ip_address

from justnetstat.interface_list import Variant, render
from justnetstat.interfaces import Address, Interface


class TestBasicVariant:
    def test_it_should_reproduce_the_tcl_layout(self):
        # The expected lines follow the Tcl's format string for `justnetstat -i`.
        assert render([LOOPBACK, UPLINK]) == [
            "Ix  Name                 as   os Address         Netmask              Type   MTU",  # noqa: E501
            "16  lo0.0                on   up 192.0.2.1       255.255.255.255  loopback  none",  # noqa: E501
            "501 xe-0/0/0.0           on   up 198.51.100.1    255.255.255.252  propVirt  9178",  # noqa: E501
        ]

    def test_when_interface_has_no_ipv4_address_it_should_show_none(self):
        lines = render([IPV6_ONLY], header=False)

        assert lines == [
            "503 xe-0/0/1.0           on lldn none            none             propVirt  9178"  # noqa: E501
        ]

    def test_when_all_addresses_are_requested_it_should_list_ipv4_before_ipv6(self):
        lines = render([UPLINK], all_addresses=True, header=False)

        assert [line.split()[4:6] for line in lines] == [
            ["198.51.100.1", "255.255.255.252"],
            ["198.51.100.5", "255.255.255.252"],
            ["2001:db8:1::1", "/64"],
            ["fe80::1", "/64"],
        ]

    def test_when_value_is_missing_it_should_show_none(self):
        lines = render([Interface(index=7, name="em0")], header=False)

        assert lines[0].split() == [
            "7",
            "em0",
            "none",
            "none",
            "none",
            "none",
            "none",
            "none",
        ]


class TestMediaVariant:
    def test_it_should_show_physical_address_and_speed(self):
        # The Tcl implementation's -m used ifSpeed alone.
        # So every interface of 10G or more showed "4.29G".
        assert render([UPLINK], Variant.MEDIA) == [
            "Ix  Name                Address         Physaddr           Speed      Type   MTU",  # noqa: E501
            "501 xe-0/0/0.0          198.51.100.1    02:00:00:00:00:02    10G  propVirt  9178",  # noqa: E501
        ]


class TestDescriptionsVariant:
    def test_it_should_show_quoted_aliases(self):
        # The Tcl implementation right-aligned the "Ix" header as " Ix".
        # The numbers below it were left-aligned.
        # It also sized the Name column by the full ifDescr, not by the names shown.
        assert render([LOOPBACK, UPLINK], Variant.DESCRIPTIONS) == [
            "Ix  Name        Speed Descr",
            '16  lo0.0          0  ""',
            '501 xe-0/0/0.0    10G "core, example-line-1"',
        ]

    def test_when_interface_has_no_alias_it_should_show_nil(self):
        lines = render(
            [Interface(index=600, descr="GigabitEthernet0/1", speed=10**9)],
            Variant.DESCRIPTIONS,
        )

        assert lines[-1] == '600 GE0/1     1G "(nil)"'

    def test_when_kilo_is_requested_it_should_show_speed_in_whole_thousands(self):
        lines = render([UPLINK], Variant.DESCRIPTIONS, kilo=True, header=False)

        assert lines == ['501 xe-0/0/0.0 10000000 "core, example-line-1"']


class TestDescriptionsAndAddressesVariant:
    def test_it_should_show_addresses_and_quoted_aliases(self):
        assert render(
            [UPLINK],
            Variant.DESCRIPTIONS_AND_ADDRESSES,
            all_addresses=True,
            header=False,
        ) == [
            '501 xe-0/0/0.0 198.51.100.1       10G "core, example-line-1"',
            '501 xe-0/0/0.0 198.51.100.5       10G "core, example-line-1"',
            '501 xe-0/0/0.0 2001:db8:1::1      10G "core, example-line-1"',
            '501 xe-0/0/0.0 fe80::1            10G "core, example-line-1"',
        ]


class TestInterfaceNames:
    def test_when_interface_has_an_ifname_it_should_use_it(self):
        interface = Interface(index=1, name="ge-0/0/0", descr="GigabitEthernet0/0")

        assert render([interface], header=False)[0].split()[1] == "ge-0/0/0"

    def test_when_interface_has_no_ifname_it_should_abbreviate_its_ifdescr(self):
        interface = Interface(index=1, descr="GigabitEthernet0/0")

        assert render([interface], header=False)[0].split()[1] == "GE0/0"

    def test_when_ifname_contains_whitespace_it_should_replace_it_with_dashes(self):
        interface = Interface(index=1, name="Port 1 \t uplink")

        assert render([interface], header=False)[0].split()[1] == "Port-1-uplink"


class TestTrafficStatisticsCompatibility:
    """Zino 1's traffic statistics parse `-inh` and `-idnh` output.

    The parsing scripts are update-dbs.pl and warn-dups.pl.
    """

    def test_inh_output_should_give_admin_status_and_ipv4_address_in_fields_2_and_4(
        self,
    ):
        lines = render(ALL_INTERFACES, header=False)

        assert {
            fields[0]: (fields[2], fields[4]) for fields in map(str.split, lines)
        } == {
            "16": ("on", "192.0.2.1"),
            "501": ("on", "198.51.100.1"),
            "502": ("off", "none"),
            "503": ("on", "none"),
        }

    def test_idnh_output_should_match_the_line_name_pattern(self):
        lines = render(ALL_INTERFACES, Variant.DESCRIPTIONS, header=False)

        matches = [IDNH_LINE_PATTERN.match(line) for line in lines]
        assert all(matches), lines
        assert {match[1]: match[3] for match in matches} == {
            "16": "",
            "501": "core, example-line-1",
            "502": "spare",
            "503": "core, example-line-2",
        }

    def test_when_speed_is_unknown_idnh_output_should_still_match_the_line_name_pattern(  # noqa: E501
        self,
    ):
        lines = render(
            [Interface(index=7, name="em0")], Variant.DESCRIPTIONS, header=False
        )

        assert lines == ['7   em0     none "(nil)"']
        assert IDNH_LINE_PATTERN.match(lines[0])


# The pattern update-dbs.pl and warn-dups.pl match each line of `-idnh` output against
IDNH_LINE_PATTERN = re.compile(r'^(\d+)\s+(\S+)\s+\S+\s+"(.*)"$')


def address(text: str, prefix_length: int) -> Address:
    return Address(ip_address(text), prefix_length)


LOOPBACK = Interface(
    index=16,
    name="lo0.0",
    type="softwareLoopback",
    mtu=2147483647,
    speed=0,
    admin_status="up",
    oper_status="up",
    alias="",
    addresses=[address("192.0.2.1", 32), address("2001:db8::1", 128)],
)

UPLINK = Interface(
    index=501,
    name="xe-0/0/0.0",
    type="propVirtual",
    mtu=9178,
    speed=10_000_000_000,
    phys_address=bytes.fromhex("020000000002"),
    admin_status="up",
    oper_status="up",
    alias="core, example-line-1",
    addresses=[
        address("198.51.100.1", 30),
        address("198.51.100.5", 30),
        address("2001:db8:1::1", 64),
        address("fe80::1", 64),
    ],
)

ADMIN_DOWN = Interface(
    index=502,
    name="xe-0/0/1",
    type="ethernetCsmacd",
    mtu=9192,
    speed=10_000_000_000,
    admin_status="down",
    oper_status="down",
    alias="spare",
)

IPV6_ONLY = Interface(
    index=503,
    name="xe-0/0/1.0",
    type="propVirtual",
    mtu=9178,
    speed=10_000_000_000,
    admin_status="up",
    oper_status="lowerLayerDown",
    alias="core, example-line-2",
    addresses=[address("2001:db8:2::1", 64)],
)

ALL_INTERFACES = [LOOPBACK, UPLINK, ADMIN_DOWN, IPV6_ONLY]
