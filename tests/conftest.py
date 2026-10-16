"""Shared test fixtures"""

import asyncio
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from shutil import which

import pytest
from netsnmpy.oids import OID
from netsnmpy.session import SNMPSession

from justnetstat.snmp import create_session, init_mibs


@pytest.fixture(scope="session", autouse=True)
def mibs():
    init_mibs()


@pytest.fixture(scope="session")
def snmp_agent():
    """Runs snmpsim to simulate SNMP agents, one per file in snmp_fixtures/.

    The SNMP community selects the file: community ``walk`` serves ``walk.snmprec``.

    snmpsim fails to start on Python 3.14 and is slow on 3.13, so it runs in
    its own Python 3.11 environment through uvx.
    """
    if not which("uvx"):
        pytest.fail("uvx is needed to run snmpsim")
    agent = SimulatedAgent(port=_free_udp_port())
    command = [
        "uvx",
        "--python=3.11",
        "--from=snmpsim>=1.0,!=1.1.6,<1.2",
        "snmpsim-command-responder",
        f"--data-dir={SNMP_FIXTURE_DIRECTORY}",
        "--log-level=error",
        f"--agent-udpv4-endpoint={agent.host}:{agent.port}",
    ]
    # uvx runs snmpsim as a grandchild.
    # A separate process group lets the whole tree be killed.
    process = subprocess.Popen(command, start_new_session=True)
    try:
        _wait_for_agent(agent, process)
        yield agent
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait()


@pytest.fixture
def query_agent(snmp_agent):
    """Returns a function running an async operation on a simulated agent's session.

    The function takes the community selecting the agent, the operation (an
    async function taking the session) and optionally the SNMP version.  It
    returns the operation's result, and closes the session afterwards.
    """

    def query(community: str, operation, version: str = "2c"):
        async def run():
            session = create_session(
                snmp_agent.host, community, snmp_agent.port, timeout=1, version=version
            )
            try:
                return await operation(session)
            finally:
                session.close()

        return asyncio.run(run())

    return query


@dataclass(frozen=True)
class SimulatedAgent:
    port: int
    host: str = "127.0.0.1"


SNMP_FIXTURE_DIRECTORY = Path(__file__).parent / "snmp_fixtures"


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_agent(
    agent: SimulatedAgent, process: subprocess.Popen, seconds: float = 60
) -> None:
    """Waits for snmpsim to answer, allowing time for uvx to install it on first use."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(
                f"snmpsim exited prematurely with exit code {process.returncode}"
            )
        if asyncio.run(_agent_responds(agent)):
            return
    pytest.fail(f"snmpsim did not answer within {seconds} seconds")


async def _agent_responds(agent: SimulatedAgent) -> bool:
    session = SNMPSession(
        agent.host,
        port=agent.port,
        community="walk",
        version="2c",
        timeout=0.5,
        retries=0,
    )
    session.open()
    try:
        await session.aget(OID(".1.3.6.1.2.1.1.2.0"))
        return True
    except TimeoutError:
        return False
    finally:
        session.close()
