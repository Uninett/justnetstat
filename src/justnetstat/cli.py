"""Command line interface for justnetstat"""

import argparse
from typing import Optional, Sequence

from justnetstat.version import __version__


def main(args: Optional[Sequence[str]] = None) -> int:
    """Runs the justnetstat command line program.

    :param args: Command line arguments, defaulting to those of the current process
    :return: The process exit code
    """
    parser = make_parser()
    parser.parse_args(args)
    return 0


def make_parser() -> argparse.ArgumentParser:
    """Builds the command line argument parser."""
    parser = argparse.ArgumentParser(
        prog="justnetstat",
        description="Show netstat-like information from network equipment, over SNMP",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser
