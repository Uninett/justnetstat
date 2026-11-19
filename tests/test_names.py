"""Tests for reverse DNS lookups"""

import asyncio
from ipaddress import ip_address

from aiodns.error import DNSError

from justnetstat import names
from justnetstat.names import reverse_lookup


class TestReverseLookup:
    async def test_it_should_look_up_each_distinct_address_once(self, monkeypatch):
        looked_up = []

        async def resolve(resolver, address):
            looked_up.append(address)
            return "gw1.example.net"

        monkeypatch.setattr(names, "_resolve", resolve)
        address = ip_address("192.0.2.1")

        assert await reverse_lookup([address, address]) == {address: "gw1.example.net"}
        assert looked_up == [address]

    async def test_it_should_not_look_up_unspecified_addresses(self, monkeypatch):
        async def resolve(resolver, address):
            raise AssertionError(f"looked up {address}")

        monkeypatch.setattr(names, "_resolve", resolve)

        assert await reverse_lookup([ip_address("0.0.0.0"), ip_address("::")]) == {}

    async def test_when_an_address_has_no_name_it_should_keep_its_numeric_form(
        self, monkeypatch
    ):
        async def resolve(resolver, address):
            raise DNSError(4, "Domain name not found")

        monkeypatch.setattr(names, "_resolve", resolve)
        address = ip_address("2001:db8::1")

        assert await reverse_lookup([address]) == {address: "2001:db8::1"}

    async def test_when_a_lookup_is_too_slow_it_should_keep_the_numeric_form(
        self, monkeypatch
    ):
        async def resolve(resolver, address):
            await asyncio.sleep(10)

        monkeypatch.setattr(names, "_resolve", resolve)
        address = ip_address("192.0.2.1")

        assert await reverse_lookup([address], timeout=0.01) == {address: "192.0.2.1"}
