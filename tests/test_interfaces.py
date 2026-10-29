"""Tests for fetching interface data, mostly run against simulated agents"""

import asyncio
from ipaddress import ip_address

import pytest
from netsnmpy.errors import SNMPError
from netsnmpy.oids import OID

from justnetstat.interfaces import (
    Address,
    Counters,
    RetrievalError,
    fetch_addresses,
    fetch_counters,
    fetch_interfaces,
)


class TestFetchInterfaces:
    def test_it_should_return_interfaces_in_ifindex_order(self, query_agent):
        interfaces = query_agent("interfaces", fetch_interfaces)

        assert [interface.index for interface in interfaces] == [
            1,
            6,
            16,
            500,
            501,
            502,
            503,
            600,
        ]

    def test_it_should_convert_interface_attributes(self, query_agent):
        uplink = by_index(query_agent("interfaces", fetch_interfaces))[501]

        assert (uplink.name, uplink.descr, uplink.type, uplink.mtu) == (
            "xe-0/0/0.0",
            "xe-0/0/0.0",
            "propVirtual",
            9178,
        )
        assert (uplink.admin_status, uplink.oper_status) == ("up", "up")
        assert uplink.phys_address == bytes.fromhex("020000000002")
        assert uplink.alias == "core, example-line-1"

    def test_when_ifspeed_is_saturated_it_should_use_ifhighspeed(self, query_agent):
        assert (
            by_index(query_agent("interfaces", fetch_interfaces))[501].speed
            == 10_000_000_000
        )

    def test_when_ifspeed_is_not_saturated_it_should_use_it(self, query_agent):
        assert (
            by_index(query_agent("interfaces", fetch_interfaces))[1].speed
            == 1_000_000_000
        )

    def test_when_interface_has_no_ifxtable_row_it_should_leave_those_attributes_empty(
        self, query_agent
    ):
        interface = by_index(query_agent("interfaces", fetch_interfaces))[600]

        assert (interface.name, interface.alias, interface.descr) == (
            None,
            None,
            "GigabitEthernet0/1",
        )

    def test_it_should_list_ipv4_addresses_before_ipv6_ones_lowest_first(
        self, query_agent
    ):
        uplink = by_index(query_agent("interfaces", fetch_interfaces))[501]

        assert uplink.addresses == [
            address("198.51.100.1", 30),
            address("198.51.100.5", 30),
            address("2001:db8:1::1", 64),
            address("fe80::1", 64),
        ]

    def test_when_addresses_are_not_requested_it_should_leave_them_out(
        self, query_agent
    ):
        interfaces = query_agent(
            "interfaces",
            lambda session: fetch_interfaces(session, with_addresses=False),
        )

        assert all(interface.addresses == [] for interface in interfaces)

    def test_when_agent_does_not_answer_it_should_report_no_response(
        self, query_agent, monkeypatch
    ):
        async def time_out(*args, **kwargs):
            raise TimeoutError()

        monkeypatch.setattr("justnetstat.interfaces.walk_columns", time_out)

        with pytest.raises(
            RetrievalError, match="Could not retrieve ifTable; reason noResponse"
        ):
            query_agent("interfaces", fetch_interfaces)

    def test_when_request_fails_it_should_report_the_reason(
        self, query_agent, monkeypatch
    ):
        async def fail(*args, **kwargs):
            raise SNMPError("tooBig")

        monkeypatch.setattr("justnetstat.interfaces.walk_columns", fail)

        with pytest.raises(
            RetrievalError, match="Could not retrieve ifTable; reason tooBig"
        ):
            query_agent("interfaces", fetch_interfaces)


class TestFetchAddresses:
    def test_when_agent_lacks_ipaddresstable_it_should_use_ipaddrtable(
        self, query_agent
    ):
        addresses = query_agent("ipv4-only", fetch_addresses)

        assert addresses == {1: [address("192.0.2.9", 30), address("192.0.2.65", 26)]}

    @pytest.mark.parametrize(
        "index, row",
        [
            (".192.0.2.300", {"IP-MIB::ipAdEntIfIndex": 7}),  # An octet out of range
            (".192.0.2", {"IP-MIB::ipAdEntIfIndex": 7}),  # Too short for an address
            (".192.0.2.5", {}),  # No interface
        ],
    )
    def test_when_ipaddrtable_row_is_unusable_it_should_skip_it(
        self, monkeypatch, index, row
    ):
        fake_tables(
            monkeypatch,
            ipAdEntIfIndex={
                OID(index): row,
                OID(".192.0.2.1"): {"IP-MIB::ipAdEntIfIndex": 7},
            },
        )

        assert asyncio.run(fetch_addresses(session=None)) == {
            7: [Address(ip_address("192.0.2.1"))]
        }

    def test_when_ipaddrtable_netmask_is_not_contiguous_it_should_be_unknown(
        self, monkeypatch
    ):
        fake_tables(
            monkeypatch,
            ipAdEntIfIndex={
                OID(".192.0.2.1"): {
                    "IP-MIB::ipAdEntIfIndex": 7,
                    "IP-MIB::ipAdEntNetMask": ip_address("255.0.255.0"),
                }
            },
        )

        assert asyncio.run(fetch_addresses(session=None)) == {
            7: [Address(ip_address("192.0.2.1"), None)]
        }

    def test_when_ipaddresstable_has_no_ipv4_addresses_it_should_add_those_of_ipaddrtable(  # noqa: E501
        self, monkeypatch
    ):
        fake_tables(
            monkeypatch,
            ipAddressIfIndex={
                OID(".2.16.32.1.13.184" + ".0" * 11 + ".1"): {
                    "IP-MIB::ipAddressIfIndex": 7
                }
            },
            ipAdEntIfIndex={OID(".192.0.2.1"): {"IP-MIB::ipAdEntIfIndex": 7}},
        )

        addresses = asyncio.run(fetch_addresses(session=None))

        assert addresses == {
            7: [Address(ip_address("192.0.2.1")), Address(ip_address("2001:db8::1"))]
        }

    @pytest.mark.parametrize(
        "index",
        [
            ".1.4.192.0.2.300",  # An octet out of range
            ".1.5.192.0.2.1",  # A length that does not match the address
            ".1",  # No address at all
            ".16.11.103.119.49.46.101.120.97.109.112.108.101",  # A DNS name
        ],
    )
    def test_when_address_index_is_unusable_it_should_skip_the_row(
        self, monkeypatch, index
    ):
        fake_tables(
            monkeypatch,
            ipAddressIfIndex={
                OID(index): {"IP-MIB::ipAddressIfIndex": 7},
                OID(".1.4.192.0.2.1"): {"IP-MIB::ipAddressIfIndex": 7},
            },
        )

        assert asyncio.run(fetch_addresses(session=None)) == {
            7: [Address(ip_address("192.0.2.1"))]
        }

    def test_it_should_skip_broadcast_and_anycast_addresses(self, monkeypatch):
        fake_tables(
            monkeypatch,
            ipAddressIfIndex={
                OID(".1.4.192.0.2.1"): {
                    "IP-MIB::ipAddressIfIndex": 7,
                    "IP-MIB::ipAddressType": "unicast",
                },
                OID(".1.4.192.0.2.3"): {
                    "IP-MIB::ipAddressIfIndex": 7,
                    "IP-MIB::ipAddressType": "broadcast",
                },
                OID(".2.16.32.1.13.184" + ".0" * 12): {
                    "IP-MIB::ipAddressIfIndex": 7,
                    "IP-MIB::ipAddressType": "anycast",
                },
            },
        )

        assert asyncio.run(fetch_addresses(session=None)) == {
            7: [Address(ip_address("192.0.2.1"))]
        }

    def test_when_prefix_length_is_too_long_for_the_address_it_should_be_unknown(
        self, monkeypatch
    ):
        prefix_row = OID(".1.3.6.1.2.1.4.32.1.5.7.1.4.192.0.2.0.99")
        fake_tables(
            monkeypatch,
            ipAddressIfIndex={
                OID(".1.4.192.0.2.1"): {
                    "IP-MIB::ipAddressIfIndex": 7,
                    "IP-MIB::ipAddressPrefix": prefix_row,
                }
            },
        )

        assert asyncio.run(fetch_addresses(session=None)) == {
            7: [Address(ip_address("192.0.2.1"), None)]
        }


class TestFetchCounters:
    def test_when_interface_has_both_kinds_of_counters_it_should_prefer_64_bit_ones(
        self, query_agent
    ):
        # Asking for interface 600 too makes the 32-bit counters get fetched as well
        counters = query_agent(
            "interfaces", lambda session: fetch_counters(session, [501, 600])
        )

        assert counters[501] == Counters(
            501_000_000_000, 501_023, 1_002_000_000_000, 1_002_011, bits=64
        )

    def test_when_interface_lacks_64_bit_counters_it_should_use_32_bit_ones(
        self, query_agent
    ):
        counters = query_agent(
            "interfaces", lambda session: fetch_counters(session, [600])
        )

        assert counters[600] == Counters(
            4_000_000_000, 5007, 3_000_000_000, 4006, bits=32
        )

    def test_when_agent_has_no_counters_for_an_interface_it_should_leave_it_out(
        self, query_agent
    ):
        counters = query_agent(
            "interfaces", lambda session: fetch_counters(session, [501, 999])
        )

        assert list(counters) == [501]


def by_index(interfaces):
    return {interface.index: interface for interface in interfaces}


def address(text: str, prefix_length: int) -> Address:
    return Address(ip_address(text), prefix_length)


def fake_tables(monkeypatch, **tables_by_first_column):
    """Makes walks return hand-made tables, picked by the first column's name.

    Walks of other tables return empty tables.
    """

    async def walk_columns(session, *columns):
        return tables_by_first_column.get(columns[0].split("::")[1], {})

    monkeypatch.setattr("justnetstat.interfaces.walk_columns", walk_columns)
