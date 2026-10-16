"""Tests for SNMP access"""

import asyncio

import pytest
from netsnmpy.errors import SNMPError
from netsnmpy.netsnmp import SNMPVariable, symbol_to_oid
from netsnmpy.oids import OID

from justnetstat import snmp


class TestOpenSession:
    def test_when_agent_answers_snmpv2c_it_should_use_snmpv2c(self, snmp_agent):
        session = asyncio.run(
            snmp.open_session(snmp_agent.host, "walk", snmp_agent.port, timeout=1)
        )
        session.close()

        assert session.version == "2c"

    def test_when_agent_does_not_answer_snmpv2c_it_should_fall_back_to_snmpv1(
        self, monkeypatch
    ):
        monkeypatch.setattr(snmp, "create_session", FakeSnmpV1Agent.create_session)

        session = asyncio.run(snmp.open_session("192.0.2.1"))

        assert session.version == "1"

    def test_when_cancelled_during_the_probe_it_should_close_the_session(
        self, monkeypatch
    ):
        probe = FakeCancelledProbe()
        monkeypatch.setattr(snmp, "create_session", lambda *args, **kwargs: probe)

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(snmp.open_session("192.0.2.1"))

        assert probe.closed


class TestWalkColumns:
    def test_it_should_return_rows_in_index_order(self, query_agent):
        table = query_agent("walk", walking("IF-MIB::ifDescr"))

        assert table == {
            OID(".1"): {"IF-MIB::ifDescr": "fxp0"},
            OID(".2"): {"IF-MIB::ifDescr": "lo0"},
            OID(".3"): {"IF-MIB::ifDescr": "lo0.0"},
        }

    @pytest.mark.parametrize("version", ["1", "2c"])
    def test_when_columns_have_gaps_or_end_early_it_should_only_include_existing_cells(
        self, query_agent, version
    ):
        table = query_agent(
            "walk",
            walking("IF-MIB::ifDescr", "IF-MIB::ifType", "IF-MIB::ifMtu"),
            version=version,
        )

        assert table == {
            OID(".1"): {
                "IF-MIB::ifDescr": "fxp0",
                "IF-MIB::ifType": "ethernetCsmacd",
                "IF-MIB::ifMtu": 1514,
            },
            OID(".2"): {"IF-MIB::ifDescr": "lo0", "IF-MIB::ifType": "softwareLoopback"},
            OID(".3"): {"IF-MIB::ifDescr": "lo0.0", "IF-MIB::ifMtu": 1500},
        }

    def test_when_table_spans_several_bulk_requests_it_should_fetch_all_rows(
        self, query_agent
    ):
        table = query_agent("many-rows", walking("IF-MIB::ifDescr", "IF-MIB::ifMtu"))

        assert len(table) == 25
        assert table[OID(".25")] == {"IF-MIB::ifDescr": "ge-0/0/25"}
        assert table[OID(".24")] == {
            "IF-MIB::ifDescr": "ge-0/0/24",
            "IF-MIB::ifMtu": 9192,
        }

    def test_when_snmpv1_agent_has_no_more_data_for_one_column_it_should_keep_walking_the_others(  # noqa: E501
        self, query_agent
    ):
        # ifMtu is the last object in the simulated agent's data, and ends at row 24
        table = query_agent(
            "many-rows", walking("IF-MIB::ifDescr", "IF-MIB::ifMtu"), version="1"
        )

        assert len(table) == 25
        assert table[OID(".25")] == {"IF-MIB::ifDescr": "ge-0/0/25"}

    def test_when_agent_has_no_such_column_it_should_return_an_empty_table(
        self, query_agent
    ):
        assert query_agent("walk", walking("IF-MIB::ifAlias")) == {}

    def test_when_bulk_answer_ends_in_the_middle_of_a_row_it_should_continue_from_there(
        self,
    ):
        agent = FakeScriptedAgent(
            "2c",
            [
                [
                    variable("IF-MIB::ifDescr.1", b"fxp0"),
                    variable("IF-MIB::ifType.1", 6),
                    variable("IF-MIB::ifDescr.2", b"lo0"),
                ],
                [variable("IF-MIB::ifType.1", 6), variable("IF-MIB::ifType.2", 24)],
                [variable("IF-MIB::ifMtu.1", 1514)],
            ],
        )

        table = asyncio.run(
            snmp.walk_columns(agent, "IF-MIB::ifDescr", "IF-MIB::ifType")
        )

        assert table == {
            OID(".1"): {"IF-MIB::ifDescr": "fxp0", "IF-MIB::ifType": "ethernetCsmacd"},
            OID(".2"): {"IF-MIB::ifDescr": "lo0", "IF-MIB::ifType": "softwareLoopback"},
        }

    def test_when_bulk_answers_are_empty_it_should_halve_the_rows_asked_for_until_they_fit(  # noqa: E501
        self,
    ):
        agent = FakeTooBigAgent(max_repetitions_that_fit=5)

        table = asyncio.run(snmp.walk_columns(agent, "IF-MIB::ifDescr"))

        assert table == {OID(".1"): {"IF-MIB::ifDescr": "fits"}}
        assert agent.requested_repetitions == [10, 5, 5]

    def test_when_even_a_single_row_gets_an_empty_answer_it_should_raise(self):
        agent = FakeTooBigAgent(max_repetitions_that_fit=0)

        with pytest.raises(SNMPError):
            asyncio.run(snmp.walk_columns(agent, "IF-MIB::ifDescr"))

        assert agent.requested_repetitions == [10, 5, 2, 1]

    def test_when_snmpv1_answer_is_empty_it_should_raise(self):
        agent = FakeScriptedAgent("1", [[]])

        with pytest.raises(SNMPError):
            asyncio.run(
                asyncio.wait_for(snmp.walk_columns(agent, "IF-MIB::ifDescr"), timeout=1)
            )

    def test_when_snmpv1_answer_to_a_single_column_is_empty_it_should_raise(self):
        echo = [variable("IF-MIB::ifDescr", None), variable("IF-MIB::ifType", None)]
        agent = FakeScriptedAgent(
            "1", [echo, [variable("IF-MIB::ifDescr.1", b"fxp0")], []]
        )

        with pytest.raises(SNMPError):
            asyncio.run(
                asyncio.wait_for(
                    snmp.walk_columns(agent, "IF-MIB::ifDescr", "IF-MIB::ifType"),
                    timeout=1,
                )
            )

    def test_when_agent_repeats_an_oid_it_should_stop_walking(self):
        table = asyncio.run(snmp.walk_columns(FakeRepeatingAgent(), "IF-MIB::ifDescr"))

        assert table == {OID(".1"): {"IF-MIB::ifDescr": "repeated"}}


class TestConvertValue:
    def test_when_value_is_enumerated_it_should_return_its_name(self):
        assert snmp.convert_value(variable("IF-MIB::ifAdminStatus.1", 1)) == "up"

    def test_when_enumerated_value_has_no_name_it_should_return_the_number_as_text(
        self,
    ):
        assert snmp.convert_value(variable("IF-MIB::ifAdminStatus.1", 99)) == "99"

    def test_when_value_is_a_display_string_it_should_return_text(self):
        assert snmp.convert_value(variable("IF-MIB::ifDescr.1", b"fxp0")) == "fxp0"

    def test_when_text_is_not_utf8_it_should_preserve_the_original_bytes(self):
        latin1 = "Bodø uplink".encode("latin-1")

        text = snmp.convert_value(variable("IF-MIB::ifDescr.1", latin1))

        assert text.encode("utf-8", errors="surrogateescape") == latin1

    def test_when_value_is_a_binary_string_it_should_return_bytes(self):
        mac = b"\x02\x00\x00\x00\x00\x01"

        assert snmp.convert_value(variable("IF-MIB::ifPhysAddress.1", mac)) == mac

    def test_when_value_is_a_plain_number_it_should_return_it(self):
        assert snmp.convert_value(variable("IF-MIB::ifMtu.1", 1500)) == 1500


def walking(*columns: str):
    return lambda session: snmp.walk_columns(session, *columns)


def variable(symbol: str, value) -> SNMPVariable:
    return SNMPVariable(symbol_to_oid(symbol), value)


class FakeSnmpV1Agent:
    """A session with an agent that only answers SNMPv1 requests"""

    def __init__(self, version: str):
        self.version = version

    @classmethod
    def create_session(cls, host, community, port, timeout, version):
        return cls(version)

    async def aget(self, *oids):
        if self.version != "1":
            raise TimeoutError()
        return []

    def close(self):
        pass


class FakeCancelledProbe:
    """A session whose first request is cancelled, as by Ctrl-C"""

    version = "2c"
    closed = False

    async def aget(self, *oids):
        raise asyncio.CancelledError()

    def close(self):
        self.closed = True


class FakeScriptedAgent:
    """A session with an agent that answers from a fixed script, whatever it is asked"""

    def __init__(self, version: str, answers: list[list[SNMPVariable]]):
        self.version = version
        self.answers = list(answers)

    async def agetbulk(self, *oids, max_repetitions):
        return await self._answer()

    async def agetnext(self, *oids):
        return await self._answer()

    async def _answer(self):
        await asyncio.sleep(0)  # Yield, so a test's timeout can stop an endless walk
        return self.answers.pop(0)


class FakeRepeatingAgent:
    """A session with a broken agent that returns the same variable every time"""

    version = "2c"

    async def agetbulk(self, *oids, max_repetitions):
        return [variable("IF-MIB::ifDescr.1", b"repeated")]


class FakeTooBigAgent:
    """A session with an agent that answers requests for too many rows with nothing"""

    version = "2c"

    def __init__(self, max_repetitions_that_fit: int):
        self.max_repetitions_that_fit = max_repetitions_that_fit
        self.requested_repetitions = []
        self.answered = False

    async def agetbulk(self, *oids, max_repetitions):
        self.requested_repetitions.append(max_repetitions)
        if max_repetitions > self.max_repetitions_that_fit:
            return []
        if self.answered:
            return [variable("IF-MIB::ifType.1", 6)]
        self.answered = True
        return [variable("IF-MIB::ifDescr.1", b"fits")]
