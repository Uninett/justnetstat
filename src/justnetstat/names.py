"""Reverse DNS lookups of IP addresses, for views that show names instead"""

import asyncio
from collections.abc import Iterable
from ipaddress import IPv4Address, IPv6Address

import aiodns
from aiodns.error import DNSError

IPAddress = IPv4Address | IPv6Address


async def reverse_lookup(
    addresses: Iterable[IPAddress],
    timeout: float = 2.0,
    concurrency: int = 10,
) -> dict[IPAddress, str]:
    """Looks up the names of IP addresses in DNS, several at a time.

    Like the Tcl implementation, this asks DNS servers directly, so
    ``/etc/hosts`` is not used, and an address without a name keeps its
    numeric form.  So does one whose lookup takes longer than the timeout.
    Each distinct address is looked up once.  Unspecified addresses, such as
    ``0.0.0.0``, are not looked up.

    :param timeout: Seconds to wait for each lookup
    :param concurrency: How many lookups to run at the same time, at most
    :return: The name of each address, or its numeric form
    """
    unique = {address for address in addresses if not address.is_unspecified}
    semaphore = asyncio.Semaphore(concurrency)

    async with aiodns.DNSResolver() as resolver:

        async def lookup(address: IPAddress) -> str:
            async with semaphore:
                try:
                    return await asyncio.wait_for(_resolve(resolver, address), timeout)
                except (DNSError, TimeoutError):
                    return str(address)

        names = await asyncio.gather(*(lookup(address) for address in unique))
    return dict(zip(unique, names))


async def _dns_resolve(resolver: aiodns.DNSResolver, address: IPAddress) -> str:
    result = await resolver.query_dns(address.reverse_pointer, "PTR")
    if not result.answer:
        return str(address)
    return result.answer[0].data.dname


# Tests replace this to avoid real DNS lookups
_resolve = _dns_resolve
