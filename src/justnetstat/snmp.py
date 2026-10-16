"""SNMP access to network equipment, built on netsnmp-cffi"""

import os
from functools import cache
from pathlib import Path
from typing import Any

from netsnmpy import netsnmp
from netsnmpy.errors import SNMPError
from netsnmpy.netsnmp import EndOfMibView, SNMPVariable, VarBindList
from netsnmpy.oids import OID
from netsnmpy.session import SNMPSession

Row = dict[str, Any]
Table = dict[OID, Row]


@cache
def init_mibs() -> None:
    """Loads the MIB modules shipped with this package.

    Net-SNMP is pointed only at the shipped MIB files, so the output does not
    depend on which MIBs happen to be installed on the system.  Only the first
    call does anything.
    """
    os.environ["MIBDIRS"] = str(_MIB_DIRECTORY)
    os.environ["MIBS"] = "ALL"
    netsnmp.register_log_callback()
    netsnmp.load_mibs()


async def open_session(
    host: str, community: str = "public", port: int = 161, timeout: float = 5.0
) -> SNMPSession:
    """Opens an SNMP session with an agent, preferring SNMPv2c.

    Like the original Tcl implementation, this asks for sysObjectID.0 using
    SNMPv2c, and falls back to SNMPv1 if the agent does not answer.

    :param host: The agent's host name or IP address
    :param community: The SNMP community
    :param port: The agent's UDP port
    :param timeout: Seconds to wait for each response
    :return: An open session
    :raises OSError: If the session cannot be opened, e.g. for an unresolvable host name
    :raises SNMPError: If Net-SNMP fails to open the session for other reasons
    """
    session = create_session(host, community, port, timeout, version="2c")
    try:
        await session.aget(netsnmp.symbol_to_oid("SNMPv2-MIB::sysObjectID.0"))
    except (TimeoutError, SNMPError):
        session.close()
        return create_session(host, community, port, timeout, version="1")
    except BaseException:
        # E.g. cancellation by Ctrl-C during the probe
        session.close()
        raise
    return session


def create_session(
    host: str, community: str, port: int, timeout: float, version: str
) -> SNMPSession:
    """Opens an SNMP session using a specific SNMP version.

    :param version: Either ``"1"`` or ``"2c"``
    """
    session = SNMPSession(
        host,
        port=port,
        community=community,
        version=version,
        timeout=timeout,
        retries=_RETRIES,
    )
    session.open()
    return session


async def walk_columns(session: SNMPSession, *columns: str) -> Table:
    """Fetches one or more columns of an SNMP table.

    Columns may have missing cells, and may end at different rows.  A row
    only has entries for the columns that have a value in it.

    :param session: An open session
    :param columns: Column names as MIB symbols, e.g. ``"IF-MIB::ifName"``
    :return: The table rows in index order.  Each maps column names to values,
        converted by `convert_value`
    :raises TimeoutError: If the agent stops answering
    :raises SNMPError: If the agent's answers make no sense, e.g. because they are empty
    """
    roots = {column: netsnmp.symbol_to_oid(column) for column in columns}
    positions = dict(
        roots
    )  # The last OID fetched for each column that is still being walked
    fetch_next = _NextFetcher(session)
    table: Table = {}
    while positions:
        walking = list(positions)
        variables = await fetch_next([positions[column] for column in walking])
        finished = set()
        for position, variable in enumerate(variables):
            column = walking[position % len(walking)]
            if column in finished:
                continue
            if not _continues_column(roots[column], positions[column], variable):
                finished.add(column)
                continue
            positions[column] = variable.oid
            table.setdefault(variable.oid.strip_prefix(roots[column]), {})[column] = (
                convert_value(variable)
            )
        for column in finished:
            del positions[column]
    return dict(sorted(table.items()))


class _NextFetcher:
    """Fetches the variables after given OIDs, adapting to the agent during one walk.

    netsnmp-cffi does not report the error status of responses, so errors
    have to be recognised by the variables an agent answers with.
    """

    def __init__(self, session: SNMPSession):
        self.session = session
        self.max_repetitions = _MAX_REPETITIONS

    async def __call__(self, oids: list[OID]) -> VarBindList:
        """Fetches the variables following each of the given OIDs.

        SNMPv1 has no GETBULK operation, so SNMPv1 sessions get one variable per OID.
        SNMPv2c sessions get several, ordered row by row.

        :raises SNMPError: If the answer has no variables, or the wrong number of them
        """
        if self.session.version == "1":
            return await self._getnext(oids)
        return await self._getbulk(oids)

    async def _getbulk(self, oids: list[OID]) -> VarBindList:
        """Fetches several rows following the given OIDs using GETBULK.

        An agent whose response would be too big answers with an error and no
        variables.  Each empty answer halves the number of rows asked for, for
        the rest of the walk.  An empty answer to a single row means something
        else is wrong.
        """
        while True:
            variables = await self.session.agetbulk(
                *oids, max_repetitions=self.max_repetitions
            )
            if variables:
                return variables
            if self.max_repetitions == 1:
                raise SNMPError("Agent answered with no variables")
            self.max_repetitions //= 2

    async def _getnext(self, oids: list[OID]) -> VarBindList:
        variables = await self.session.agetnext(*oids)
        if len(oids) > 1 and [variable.oid for variable in variables] == oids:
            # An SNMPv1 agent rejects the whole request when any OID has no successor,
            # echoing the request.
            # Asking for each OID separately tells which ones have successors.
            return [await self._getnext_one(oid) for oid in oids]
        if len(variables) != len(oids):
            raise SNMPError(
                f"Agent answered {len(oids)} requested variables with {len(variables)}"
            )
        return variables

    async def _getnext_one(self, oid: OID) -> SNMPVariable:
        variables = await self.session.agetnext(oid)
        if len(variables) != 1:
            raise SNMPError(
                f"Agent answered 1 requested variable with {len(variables)}"
            )
        return variables[0]


def _continues_column(root: OID, previous: OID, variable: SNMPVariable) -> bool:
    """Tells whether a fetched variable is the next cell of the column at root.

    The OID must also increase.  An SNMPv1 agent that has reached the end of
    its MIB answers with an error and echoes the request, and netsnmp-cffi
    does not report the error.
    """
    return (
        root.is_a_prefix_of(variable.oid)
        and variable.oid > previous
        and not isinstance(variable.value, EndOfMibView)
    )


def convert_value(variable: SNMPVariable) -> Any:
    """Converts a variable's value into the most useful Python type.

    Enumerated values become their MIB names, or their numbers as text when
    the MIB has no name for them.  Text strings become ``str``.  Other values,
    such as IP addresses, OIDs and binary strings, are left as they are.

    Text that is not valid UTF-8 keeps its undecodable bytes as surrogate
    characters.  Printing to a stream that uses the ``surrogateescape`` error
    handler writes those bytes out unchanged.
    """
    enums = netsnmp.get_enums_for_object(variable.oid)
    if enums:
        return enums.get(variable.value, str(variable.value))
    if (
        isinstance(variable.value, bytes)
        and variable.textual_convention in _TEXT_CONVENTIONS
    ):
        return variable.value.decode("utf-8", errors="surrogateescape")
    return variable.value


_MIB_DIRECTORY = Path(__file__).parent / "mibs"

_TEXT_CONVENTIONS = {"DisplayString", "SnmpAdminString"}

# How many rows to ask for in each GETBULK request, at most
_MAX_REPETITIONS = 10

# netsnmp-cffi's default, made explicit.
# A request can take (retries + 1) * timeout before failing.
_RETRIES = 3
