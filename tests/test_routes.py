"""Tests for the routing table view

Expected output lines are kept whole, so they read like real output.
"""

from ipaddress import ip_address

import pytest
from netsnmpy.oids import OID

from justnetstat import routes
from justnetstat.interfaces import Interface, RetrievalError
from justnetstat.routes import Route, fetch_routes, parse_prefix


class TestFetchRoutes:
    async def test_it_should_decode_the_next_hop_and_convert_attributes(
        self, query_agent
    ):
        default = (await query_agent("routes", fetch_routes))[0]

        assert default == Route(
            destination=ip_address("0.0.0.0"),
            prefix_length=0,
            next_hop=ip_address("192.0.2.1"),
            if_index=1,
            type="remote",
            protocol="bgp",
        )

    async def test_when_a_route_has_no_next_hop_it_should_be_none(self, query_agent):
        blackhole = (await query_agent("routes", fetch_routes))[4]

        assert (blackhole.next_hop, blackhole.type) == (None, "blackhole")

    async def test_when_given_a_prefix_it_should_only_fetch_routes_starting_with_it(
        self, query_agent
    ):
        found = await query_agent(
            "routes", lambda session: fetch_routes(session, prefix=(192, 0, 2))
        )

        assert [str(route.destination) for route in found] == [
            "192.0.2.0",
            "192.0.2.10",
        ]

    async def test_when_given_a_maximum_it_should_stop_after_that_many_routes(
        self, query_agent
    ):
        found = await query_agent(
            "routes", lambda session: fetch_routes(session, max_routes=2)
        )

        assert [str(route.destination) for route in found] == ["0.0.0.0", "192.0.2.0"]

    async def test_when_the_prefix_is_a_whole_address_it_should_fetch_one_route(
        self, monkeypatch
    ):
        # The Tcl implementation got the single ipRouteTable row of that address
        walks = fake_route_table(monkeypatch)

        await fetch_routes(None, prefix=(192, 0, 2, 0), max_routes=5)

        assert walks == [{"index_prefix": (1, 4, 192, 0, 2, 0), "max_rows": 1}]

    async def test_when_the_prefix_is_longer_than_an_address_it_should_fetch_nothing(
        self, monkeypatch
    ):
        walks = fake_route_table(monkeypatch)

        assert await fetch_routes(None, prefix=(192, 0, 2, 0, 1)) == []
        assert walks == []

    async def test_when_using_snmpv1_it_should_fetch_the_same_routes(self, query_agent):
        v2c = await query_agent("routes", fetch_routes)
        v1 = await query_agent("routes", fetch_routes, version="1")

        assert v1 == v2c

    async def test_when_the_agent_has_no_route_table_it_should_return_no_routes(
        self, query_agent
    ):
        assert await query_agent("interfaces", fetch_routes) == []

    async def test_when_the_agent_does_not_answer_it_should_raise_retrieval_error(
        self, monkeypatch
    ):
        async def walk_columns(*args, **kwargs):
            raise TimeoutError

        monkeypatch.setattr(routes, "walk_columns", walk_columns)

        with pytest.raises(RetrievalError, match="inetCidrRouteTable.*noResponse"):
            await fetch_routes(None)


class TestFetchRoutesIndexes:
    async def test_it_should_drop_the_zone_index_of_zoned_addresses(self, monkeypatch):
        # 192.0.2.0/24, no policy, next hop ipv4z 192.0.2.1 in zone 7
        fake_route_table(monkeypatch, ".1.4.192.0.2.0.24.2.0.0.3.8.192.0.2.1.0.0.0.7")

        (found,) = await fetch_routes(None)

        assert found.next_hop == ip_address("192.0.2.1")

    @pytest.mark.parametrize(
        "index",
        [
            ".1.4.192.0.2",  # Address shorter than its length
            ".1.4.192.0.2.0.24.2.0.0.1.4.192.0.2.1.99",  # Junk at the end
            ".1.4.192.0.2.0.33.2.0.0.0.0",  # Prefix too long for IPv4
            ".16.4.192.0.2.0.24.2.0.0.0.0",  # DNS name destination
            ".1.4.192.0.2.256.24.2.0.0.0.0",  # Not an octet
        ],
    )
    async def test_when_the_index_is_malformed_it_should_skip_the_route(
        self, index, monkeypatch
    ):
        fake_route_table(monkeypatch, index)

        assert await fetch_routes(None) == []


class TestParsePrefix:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("10", (10,)),
            ("10.1.", (10, 1)),
            (".192.0.2.0", (192, 0, 2, 0)),
            ("", ()),
            (".", ()),
            ("300", (44,)),  # The Tcl took each component modulo 256
            ("192.0.2.0.1", (192, 0, 2, 0, 1)),
        ],
    )
    def test_it_should_accept_dotted_prefixes_like_the_tcl(self, text, expected):
        assert parse_prefix(text) == expected

    @pytest.mark.parametrize("text", ["10..1", "gw1.example.net", "2001:db8::", "1²"])
    def test_it_should_refuse_anything_else(self, text):
        with pytest.raises(ValueError, match="is not an IP-prefix"):
            parse_prefix(text)


class TestRender:
    def test_it_should_show_routes_like_the_tcl(self):
        lines = routes.render(
            [
                route("0.0.0.0", 0, "192.0.2.1", 1, "remote", "bgp"),
                route("192.0.2.0", 24, "0.0.0.0", 1, "local", "local"),
            ],
            [Interface(index=1, name="ge-0/0/0")],
        )

        assert lines == [
            "Destination (pfx/len) Gateway                Flags Interface       Protocol",  # noqa: E501
            "0.0.0.0/0             192.0.2.1              UG    ge-0/0/0        bgp",
            "192.0.2.0/24          0.0.0.0                U     ge-0/0/0        local/static",  # noqa: E501
        ]

    def test_when_a_route_is_a_host_route_it_should_add_the_h_flag(self):
        lines = routes.render(
            [
                route("192.0.2.10", 32, "0.0.0.0", 1, "local", "local"),
                route("192.0.2.11", 32, "0.0.0.0", 1, "reject", "local"),
            ],
            [],
            header=False,
        )

        assert [line[45:51].strip() for line in lines] == ["UH", "H"]

    @pytest.mark.parametrize(
        "route_type, expected",
        [("other", "U"), ("reject", ""), ("blackhole", "U"), (None, "U")],
    )
    def test_it_should_flag_other_route_types_like_the_tcl(self, route_type, expected):
        # inetCidrRouteType's reject(2) is ipRouteType's invalid(2), which had no flags.
        # The Tcl implementation flagged any type but invalid and indirect with U.
        (line,) = routes.render(
            [route("203.0.113.0", 24, "192.0.2.1", 1, route_type, "netmgmt")],
            [],
            header=False,
        )

        assert line[45:51].strip() == expected

    def test_when_a_route_has_no_interface_or_next_hop_it_should_show_them_like_the_tcl(  # noqa: E501
        self,
    ):
        # ipRouteNextHop and ipRouteIfIndex had 0.0.0.0 and 0 for none
        (line,) = routes.render(
            [route("203.0.113.0", 24, None, 0, "blackhole", "netmgmt")],
            [],
            header=False,
        )

        assert line.split() == ["203.0.113.0/24", "0.0.0.0", "U", "if(0)", "netmgmt"]

    @pytest.mark.parametrize(
        "protocol, expected",
        [
            ("local", "local/static"),
            ("isIs", "is-is"),
            ("esIs", "es-is"),
            ("ciscoEigrp", "16"),
            ("bgp", "bgp"),
        ],
    )
    def test_it_should_name_protocols_like_the_tcl(self, protocol, expected):
        # The Tcl implementation used RFC1213-MIB's names, which IANA changed,
        # and numbers for protocols that RFC1213-MIB did not name
        (line,) = routes.render(
            [route("198.51.100.0", 24, "192.0.2.1", 1, "remote", protocol)],
            [],
            header=False,
        )

        assert line.split()[4] == expected

    def test_when_the_interface_is_unknown_it_should_show_its_ifindex_like_the_tcl(
        self,
    ):
        (line,) = routes.render(
            [route("198.51.100.0", 24, "192.0.2.1", 7, "remote", "ospf")],
            [],
            header=False,
        )

        assert line.split()[3] == "if(7)"

    def test_when_given_names_it_should_show_gateways_by_name(self):
        (line,) = routes.render(
            [route("198.51.100.0", 24, "192.0.2.1", 1, "remote", "ospf")],
            [],
            names={ip_address("192.0.2.1"): "gw1.example.net"},
            header=False,
        )

        assert line.split()[1] == "gw1.example.net"


def route(
    destination: str,
    prefix_length: int,
    next_hop: str | None,
    if_index: int,
    route_type: str | None,
    protocol: str,
) -> Route:
    return Route(
        destination=ip_address(destination),
        prefix_length=prefix_length,
        next_hop=ip_address(next_hop) if next_hop else None,
        if_index=if_index,
        type=route_type,
        protocol=protocol,
    )


def fake_route_table(monkeypatch, *indexes: str) -> list[dict]:
    """Makes route walks return a table with the given indexes and no values.

    :return: The keyword arguments of each walk, as walks happen
    """
    walks = []

    async def walk_columns(session, *columns, **kwargs):
        walks.append(kwargs)
        return {OID(index): {} for index in indexes}

    monkeypatch.setattr(routes, "walk_columns", walk_columns)
    return walks
