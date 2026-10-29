"""Command line interface for justnetstat"""

import argparse
import asyncio
import logging
import os
import sys
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from itertools import count

from netsnmpy.errors import SNMPError
from netsnmpy.session import SNMPSession

from justnetstat import interface_list, traffic
from justnetstat.interfaces import (
    Counters,
    RetrievalError,
    fetch_counters,
    fetch_interfaces,
)
from justnetstat.snmp import init_mibs, open_session
from justnetstat.version import __version__


def main(args: Sequence[str] | None = None) -> int:
    """Runs the justnetstat command line program.

    :param args: Command line arguments, defaulting to those of the current process
    :return: The process exit code
    """
    options = make_parser().parse_args(args)
    mode = select_mode(options)
    if mode is None:
        return 0
    show = _MODES.get(mode)
    if show is None:
        print(f"justnetstat: {mode} is not implemented yet", file=sys.stderr)
        return 1
    _set_up_output()
    init_mibs()
    try:
        asyncio.run(show(options))
    except (SessionError, RetrievalError) as error:
        print(error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        _discard_output()
        return 141  # As if killed by SIGPIPE, like other tools in a pipeline
    return 0


def select_mode(options: argparse.Namespace) -> str | None:
    """Selects what to show: the first mode flag set, in the Tcl implementation's order.

    ``-a`` lists all addresses when combined with ``-i``, and selects the
    connection list on its own.

    :return: The selected mode flag, or ``None`` if no mode flag is set
    """
    modes = [
        ("-i", options.interfaces),
        ("-r", options.routes),
        ("-B", options.traffic),
        ("-C", options.errors),
        ("-a", options.all),
        ("-b", options.bgp),
    ]
    return next((flag for flag, is_set in modes if is_set), None)


async def show_interface_list(options: argparse.Namespace) -> None:
    """Prints the interface list of ``-i`` and its variants."""
    variant = _interface_list_variant(options)
    async with _session(options) as session:
        interfaces = await fetch_interfaces(
            session, with_addresses=variant is not interface_list.Variant.DESCRIPTIONS
        )
    lines = interface_list.render(
        interfaces,
        variant,
        all_addresses=options.all,
        header=not options.no_header,
        kilo=options.kilo,
    )
    for line in lines:
        print(line)


async def show_traffic(options: argparse.Namespace) -> None:
    """Prints the traffic counters of ``-B``, or rates when given an interval or count.

    Rates are printed in rounds, one per interval, after a first sample of
    the counters.  Each round has its own header.  Rates are divided by the
    measured time between samples, where the Tcl implementation divided by
    the interval.  That keeps them right when a fetch is delayed, e.g. by a
    lost packet.
    """
    interval, rounds = traffic_schedule(options.interval, options.count)
    async with _session(options) as session:
        interfaces = await fetch_interfaces(session)
        if_indexes = [interface.index for interface in interfaces]
        previous, previous_time = await _sample_counters(session, if_indexes)
        if rounds == 1:
            for line in traffic.render_totals(
                interfaces, previous, header=not options.no_header
            ):
                print(line)
            return
        table = traffic.RateTable(header=not options.no_header, kilo=options.kilo)
        next_sample = previous_time
        for _ in count() if rounds is None else range(rounds):
            # Samples start one interval apart.
            # After a delay, the schedule restarts instead of rushing to catch up.
            next_sample = max(next_sample + interval, _clock())
            await _sleep(next_sample - _clock())
            current, current_time = await _sample_counters(session, if_indexes)
            for line in table.render(
                interfaces, previous, current, current_time - previous_time
            ):
                print(line, flush=True)
            previous_time, previous = current_time, current


def traffic_schedule(interval: int, rounds: int) -> tuple[int, int | None]:
    """Interprets the -w and -c options of -B like the Tcl implementation does.

    Without either option, a single snapshot of the counters is shown.  An
    interval alone means rounds of rates until interrupted.  A count alone
    means that many rounds, 15 seconds apart.  A count of 1 always means a
    snapshot.  The parser rejects values below 1, so -1 can only mean "not given".

    :param interval: The -w option, or -1 if not given
    :param rounds: The -c option, or -1 if not given
    :return: The interval in seconds, and the number of rounds, ``None`` meaning
        until interrupted
    """
    if interval == -1:
        return 15, 1 if rounds == -1 else rounds
    return interval, None if rounds == -1 else rounds


async def _sample_counters(
    session: SNMPSession, if_indexes: list[int]
) -> tuple[dict[int, Counters], float]:
    """Fetches traffic counters, and the time they were read.

    The time is taken when the answer arrives.  When a request is lost, the
    agent reads its counters on a retry, a timeout or more after the first
    request was sent.
    """
    counters = await fetch_counters(session, if_indexes)
    return counters, _clock()


class SessionError(Exception):
    """Raised when no SNMP session can be opened with an agent"""


@asynccontextmanager
async def _session(options: argparse.Namespace) -> AsyncIterator[SNMPSession]:
    """Opens a session with the agent named on the command line, closing it afterwards.

    :raises SessionError: If the session cannot be opened
    """
    try:
        session: SNMPSession = await open_session(
            options.agent, options.community, options.port, options.timeout
        )
    except (OSError, SNMPError) as error:
        raise SessionError(
            f"Could not create SNMPv2C session handle for agent {options.agent}; "
            f"reason {error}"
        ) from error
    try:
        yield session
    finally:
        session.close()


def _set_up_output() -> None:
    """Prepares the process's output for scripts that parse it.

    Scripts merge stderr into stdout, so log messages from netsnmp-cffi and
    Net-SNMP must not reach stderr.  The program reports its own errors.
    Text from agents is printed as the original bytes, even when it is not
    valid UTF-8.  So the locale's encoding must not get in the way.
    """
    library_logger = logging.getLogger("netsnmpy")
    if not any(
        isinstance(handler, logging.NullHandler) for handler in library_logger.handlers
    ):
        library_logger.addHandler(logging.NullHandler())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="surrogateescape")


def _discard_output() -> None:
    """Sends further output to /dev/null, after the reader of stdout went away.

    Python flushes stdout on exit, which would fail again and print a traceback.
    """
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stdout.fileno())


def _interface_list_variant(options: argparse.Namespace) -> interface_list.Variant:
    """Selects the interface list variant, in the Tcl implementation's precedence."""
    if options.descriptions:
        return interface_list.Variant.DESCRIPTIONS
    if options.media:
        return interface_list.Variant.MEDIA
    if options.descriptions_and_addresses:
        return interface_list.Variant.DESCRIPTIONS_AND_ADDRESSES
    return interface_list.Variant.BASIC


# Tests replace these to control time
_sleep = asyncio.sleep
_clock = time.monotonic

_MODES = {
    "-i": show_interface_list,
    "-B": show_traffic,
}


def make_parser() -> argparse.ArgumentParser:
    """Builds the command line argument parser.

    The flags are those of the Tcl implementation.  Single-letter flags can be
    combined, as in ``-idnh``.  ``-h`` means "no headers", so help is only
    available as ``--help``.
    """
    parser = argparse.ArgumentParser(
        prog="justnetstat",
        description="Show netstat-like information from network equipment, over SNMP",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-y", dest="community", default="public", help="community string"
    )
    parser.add_argument(
        "-t",
        dest="timeout",
        type=_positive_integer,
        default=5,
        help="timeout in seconds",
    )
    parser.add_argument(
        "-c",
        dest="count",
        type=_positive_integer,
        default=-1,
        help="number of rounds of rates for -B (1: show the counters once)",
    )
    parser.add_argument(
        "-w",
        dest="interval",
        type=_positive_integer,
        default=-1,
        help="seconds between rounds of rates for -B (default 15 with -c)",
    )
    parser.add_argument(
        "-P",
        dest="port",
        type=_positive_integer,
        default=161,
        help="port for the SNMP agent",
    )
    for flag, dest, help_text in _FLAGS:
        parser.add_argument(flag, dest=dest, action="store_true", help=help_text)
    parser.add_argument(
        "agent", help="the SNMP agent, i.e. the router's name or address"
    )
    parser.add_argument(
        "args",
        nargs="*",
        help="optional arguments, e.g. a route prefix for -r" + _NOT_IMPLEMENTED,
    )
    return parser


def _positive_integer(text: str) -> int:
    """Parses a whole number of at least 1, for options where smaller makes no sense."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1: {text}")
    return value


_NOT_IMPLEMENTED = " (not implemented yet)"

_FLAGS = [
    ("-i", "interfaces", "do the interface table"),
    ("-r", "routes", "print routing table entries" + _NOT_IMPLEMENTED),
    ("-B", "traffic", "print interface traffic counters, or rates with -w or -c"),
    ("-C", "errors", "print interface error counters" + _NOT_IMPLEMENTED),
    (
        "-a",
        "all",
        "print all addresses with -i; on its own, print active sessions"
        + _NOT_IMPLEMENTED,
    ),
    ("-b", "bgp", "print BGP summary information" + _NOT_IMPLEMENTED),
    ("-4", "bgp4_table", "print the BGP4 routing table" + _NOT_IMPLEMENTED),
    ("-p", "bgp_peers", "print BGP peer information" + _NOT_IMPLEMENTED),
    ("-T", "bgp_timers", "print BGP timing parameters" + _NOT_IMPLEMENTED),
    ("-d", "descriptions", "print ifAlias fields"),
    ("-D", "descriptions_and_addresses", "print ifAlias fields and IP addresses"),
    ("-m", "media", "print media addresses"),
    ("-n", "numeric", "don't translate IP addresses to DNS names" + _NOT_IMPLEMENTED),
    ("-h", "no_header", "don't print headers"),
    ("-k", "kilo", "show speeds and rates in whole thousands"),
]
