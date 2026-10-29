"""Command line interface for justnetstat"""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from netsnmpy.errors import SNMPError
from netsnmpy.session import SNMPSession

from justnetstat import interface_list
from justnetstat.interfaces import RetrievalError, fetch_interfaces
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


_MODES = {
    "-i": show_interface_list,
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
        "-c", dest="count", type=_positive_integer, default=-1, help="count"
    )
    parser.add_argument(
        "-w",
        dest="interval",
        type=_positive_integer,
        default=-1,
        help="interval in seconds",
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
    parser.add_argument("args", nargs="*", help="optional arguments")
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


_FLAGS = [
    ("-i", "interfaces", "do the interface table"),
    ("-r", "routes", "print routing table entries"),
    ("-B", "traffic", "print interface traffic counters"),
    ("-C", "errors", "print interface error counters"),
    ("-a", "all", "print active sessions or all interfaces"),
    ("-b", "bgp", "print BGP summary information"),
    ("-4", "bgp4_table", "print the BGP4 routing table"),
    ("-p", "bgp_peers", "print BGP peer information"),
    ("-T", "bgp_timers", "print BGP timing parameters"),
    ("-d", "descriptions", "print ifAlias fields"),
    ("-D", "descriptions_and_addresses", "print ifAlias fields and IP addresses"),
    ("-m", "media", "print media addresses"),
    ("-n", "numeric", "don't translate IP addresses to DNS names"),
    ("-h", "no_header", "don't print headers"),
    ("-k", "kilo", "print units in k"),
]
