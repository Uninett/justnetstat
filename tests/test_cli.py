"""Tests for the command line interface

Expected output lines are kept whole, so they read like real output.  Their
``# noqa: E501`` tags let them exceed the line length limit.
"""

import os
import subprocess
import sys
from collections.abc import Sequence
from contextlib import asynccontextmanager

import pytest

from justnetstat import cli
from justnetstat.interfaces import Counters, Interface
from justnetstat.version import __version__


class TestMain:
    def test_when_version_is_requested_it_should_print_it_and_exit(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            cli.main(["--version"])

        assert exit_info.value.code == 0
        assert __version__ in capsys.readouterr().out

    def test_when_no_mode_flag_is_given_it_should_exit_quietly(self, capsys):
        assert cli.main(["192.0.2.1"]) == 0
        assert capsys.readouterr() == ("", "")

    def test_when_mode_is_not_implemented_it_should_say_so(self, capsys):
        assert cli.main(["-r", "192.0.2.1"]) == 1
        assert "-r is not implemented yet" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "arguments", [["-w", "0"], ["-w-5"], ["-c", "0"], ["-t", "0"], ["-w", "soon"]]
    )
    def test_when_a_number_option_is_below_1_or_not_a_number_it_should_refuse_it(
        self, arguments, capsys
    ):
        with pytest.raises(SystemExit) as exit_info:
            cli.main(["-i", *arguments, "192.0.2.1"])

        assert exit_info.value.code == 2
        assert "usage:" in capsys.readouterr().err

    def test_it_should_keep_library_log_messages_off_stderr(self):
        # Scripts merge stderr into stdout, so a log message would be parsed as data.
        # pytest installs its own log handlers, which would hide the problem.
        # So this test runs in a separate process.
        program = (
            "import logging, sys\n"
            "from justnetstat import cli\n"
            "cli.main(['-i', 'no-such-host.invalid'])\n"
            "logging.getLogger('netsnmpy.netsnmp').warning('a library warning')\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, timeout=60
        )

        assert "a library warning" not in result.stderr

    def test_it_should_print_text_from_agents_as_its_original_bytes_whatever_the_locale(  # noqa: E501
        self,
    ):
        # An arrow does not exist in latin-1, so printing one used to crash.
        # Text that is not valid UTF-8 arrives with surrogate escapes.
        program = (
            "from justnetstat import cli\n"
            "cli._set_up_output()\n"
            "print('\\u2192 \\udce6')\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            env={**os.environ, "PYTHONIOENCODING": "latin-1"},
            timeout=60,
        )

        assert result.stdout == "\u2192 ".encode() + b"\xe6\n"

    def test_when_the_reader_of_its_output_goes_away_it_should_exit_quietly(self):
        # As in `justnetstat -B -w 1 router | head`.
        # The output is too much for the pipe, so the program is still writing.
        program = (
            "from justnetstat import cli\n"
            "async def endless(options):\n"
            "    while True:\n"
            "        print('x' * 79)\n"
            "cli._MODES['-i'] = endless\n"
            "raise SystemExit(cli.main(['-i', '192.0.2.1']))\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", program],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        process.stdout.read(1)
        process.stdout.close()

        assert process.wait(timeout=60) == 141
        assert process.stderr.read() == b""
        process.stderr.close()

    def test_when_interrupted_it_should_exit_quietly(self, monkeypatch, capsys):
        async def interrupted(options):
            raise KeyboardInterrupt()

        monkeypatch.setitem(cli._MODES, "-i", interrupted)

        assert cli.main(["-i", "192.0.2.1"]) == 130
        assert capsys.readouterr() == ("", "")


class TestInterfaceList:
    def test_when_run_like_update_dbs_it_should_print_only_headerless_lines(
        self, snmp_agent, capsys
    ):
        exit_code = cli.main(["-inh", "-t", "1", *agent_args(snmp_agent)])

        assert exit_code == 0
        assert capsys.readouterr() == (
            "1   fxp0                 on down none            none                ether  1514\n"  # noqa: E501
            "6   lo0                  on   up none            none             loopback  none\n"  # noqa: E501
            "16  lo0.0                on   up 192.0.2.1       255.255.255.255  loopback  none\n"  # noqa: E501
            "500 xe-0/0/0             on   up none            none                ether  9192\n"  # noqa: E501
            "501 xe-0/0/0.0           on   up 198.51.100.1    255.255.255.252  propVirt  9178\n"  # noqa: E501
            "502 xe-0/0/1            off down none            none                ether  9192\n"  # noqa: E501
            "503 xe-0/0/1.0           on lldn none            none             propVirt  9178\n"  # noqa: E501
            "600 GE0/1                on   up none            none                ether  1500\n",  # noqa: E501
            "",
        )

    def test_when_run_for_line_names_it_should_print_headerless_lines_with_quoted_aliases(  # noqa: E501
        self, snmp_agent, capsys
    ):
        exit_code = cli.main(["-idnh", "-t", "1", *agent_args(snmp_agent)])

        assert exit_code == 0
        assert capsys.readouterr() == (
            '1   fxp0           1G ""\n'
            '6   lo0            0  ""\n'
            '16  lo0.0          0  ""\n'
            '500 xe-0/0/0      10G "core, example-line-1, 10G"\n'
            '501 xe-0/0/0.0    10G "core, example-line-1"\n'
            '502 xe-0/0/1      10G "spare"\n'
            '503 xe-0/0/1.0    10G "core, example-line-2"\n'
            '600 GE0/1          1G "(nil)"\n',
            "",
        )

    @pytest.mark.parametrize(
        "flags, expected_header",
        [
            (
                "-i",
                "Ix  Name                 as   os Address         Netmask              Type   MTU",  # noqa: E501
            ),
            (
                "-im",
                "Ix  Name                Address         Physaddr           Speed      Type   MTU",  # noqa: E501
            ),
            ("-id", "Ix  Name        Speed Descr"),
            ("-iD", "Ix  Name       Address          Speed Descr"),
            # The Tcl implementation's order of precedence: -d, then -m, then -D
            ("-idmD", "Ix  Name        Speed Descr"),
            (
                "-imD",
                "Ix  Name                Address         Physaddr           Speed      Type   MTU",  # noqa: E501
            ),
        ],
    )
    def test_it_should_select_the_variant_by_flags(
        self, snmp_agent, capsys, flags, expected_header
    ):
        assert cli.main([flags, *agent_args(snmp_agent)]) == 0
        assert capsys.readouterr().out.splitlines()[0] == expected_header

    def test_when_all_addresses_are_requested_it_should_list_each_address(
        self, snmp_agent, capsys
    ):
        assert cli.main(["-iah", *agent_args(snmp_agent)]) == 0

        listed = [
            line.split()[4]
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("501 ")
        ]
        assert listed == ["198.51.100.1", "198.51.100.5", "2001:db8:1::1", "fe80::1"]

    def test_when_kilo_is_requested_it_should_show_speeds_in_whole_thousands(
        self, snmp_agent, capsys
    ):
        assert cli.main(["-idkh", *agent_args(snmp_agent)]) == 0

        assert capsys.readouterr().out.splitlines()[0].split()[2] == "1000000"

    def test_when_host_cannot_be_resolved_it_should_fail_with_the_reason(self, capsys):
        # The Tcl implementation left out the reason
        assert cli.main(["-i", "no-such-host.invalid"]) == 1

        error = capsys.readouterr().err
        assert error.startswith(
            "Could not create SNMPv2C session handle for agent no-such-host.invalid; "
            "reason "
        )

    def test_when_agent_stops_answering_it_should_report_no_response(
        self, snmp_agent, monkeypatch, capsys
    ):
        async def time_out(*args, **kwargs):
            raise TimeoutError()

        monkeypatch.setattr("justnetstat.interfaces.walk_columns", time_out)

        assert cli.main(["-i", *agent_args(snmp_agent)]) == 1
        assert (
            capsys.readouterr().err == "Could not retrieve ifTable; reason noResponse\n"
        )


class TestTraffic:
    def test_without_interval_or_count_it_should_print_counter_totals(
        self, snmp_agent, capsys
    ):
        assert cli.main(["-Bh", *agent_args(snmp_agent)]) == 0

        out, err = capsys.readouterr()
        lines = out.splitlines()
        assert err == ""
        assert (
            lines[4]
            == "501 xe-0/0/0.0   198.51.100.1      501000000000     501023 1002000000000    1002011"  # noqa: E501
        )
        assert (
            lines[7]
            == "600 GE0/1        none                4000000000       5007    3000000000       4006"  # noqa: E501
        )

    def test_with_interval_and_count_it_should_print_that_many_rounds_of_rates(
        self, snmp_agent, capsys, monkeypatch
    ):
        cli.main(["-ih", *agent_args(snmp_agent)])
        interface_count = len(capsys.readouterr().out.splitlines())
        monkeypatch.setattr(cli, "_sleep", FakeClock().sleep)

        assert cli.main(["-B", "-w", "1", "-c", "2", *agent_args(snmp_agent)]) == 0

        lines = capsys.readouterr().out.splitlines()
        assert len(lines) == 2 * (1 + interface_count)
        assert [line for line in lines if line.startswith("Ix ")] == [
            lines[0],
            lines[1 + interface_count],
        ]
        assert "ibit/s" in lines[0]

    def test_when_a_round_is_delayed_it_should_divide_by_the_measured_time(
        self, monkeypatch, capsys
    ):
        # The Tcl implementation divided by the interval.
        # It showed 12k for the delayed round, which counted 15 s of traffic.
        clock = FakeClock(delays=[0, 5])
        fake_steady_agent(monkeypatch, clock, octets_per_second=1000)

        assert cli.main(["-Bh", "-w", "10", "-c", "2", "192.0.2.1"]) == 0

        in_rates = [line.split()[3] for line in capsys.readouterr().out.splitlines()]
        assert in_rates == ["8k", "8k"]

    def test_when_an_answer_is_delayed_it_should_time_the_sample_by_the_answer(
        self, monkeypatch, capsys
    ):
        # As when a request is lost, and the agent answers its retry
        clock = FakeClock()
        fake_steady_agent(
            monkeypatch, clock, octets_per_second=1000, answer_delays=[0, 5]
        )

        assert cli.main(["-Bh", "-w", "10", "-c", "3", "192.0.2.1"]) == 0

        in_rates = [line.split()[3] for line in capsys.readouterr().out.splitlines()]
        assert in_rates == ["8k", "8k", "8k"]


class TestTrafficSchedule:
    @pytest.mark.parametrize(
        "interval, count, expected",
        [
            (-1, -1, (15, 1)),
            (5, -1, (5, None)),
            (-1, 3, (15, 3)),
            (5, 3, (5, 3)),
            (5, 1, (5, 1)),
        ],
    )
    def test_it_should_interpret_interval_and_count_like_the_tcl(
        self, interval, count, expected
    ):
        assert cli.traffic_schedule(interval, count) == expected


class TestSelectMode:
    @pytest.mark.parametrize(
        "flags, expected",
        [
            ("-iraB", "-i"),
            ("-rBC", "-r"),
            ("-BCab", "-B"),
            ("-Cab", "-C"),
            ("-ab", "-a"),
            ("-bp", "-b"),
        ],
    )
    def test_it_should_follow_the_tcl_order_of_precedence(self, flags, expected):
        assert cli.select_mode(parse(flags)) == expected


class TestParser:
    def test_it_should_accept_combined_flags(self):
        options = parse("-idnh")

        assert (
            options.interfaces,
            options.descriptions,
            options.numeric,
            options.no_header,
        ) == (True,) * 4

    def test_it_should_tell_upper_and_lower_case_flags_apart(self):
        options = parse("-D")

        assert (options.descriptions_and_addresses, options.descriptions) == (
            True,
            False,
        )

    def test_it_should_accept_the_options_the_traffic_statistics_use(self):
        options = cli.make_parser().parse_args(
            ["-inh", "-t", "30", "-y", "secret", "-P", "1161", "gw1.example.net"]
        )

        assert (options.timeout, options.community, options.port, options.agent) == (
            30,
            "secret",
            1161,
            "gw1.example.net",
        )

    def test_it_should_default_to_the_tcl_session_parameters(self):
        options = parse("-i")

        assert (options.community, options.port, options.timeout) == ("public", 161, 5)


def agent_args(agent, community: str = "interfaces") -> list[str]:
    """Returns command line arguments that point justnetstat at a simulated agent."""
    return ["-y", community, "-P", str(agent.port), agent.host]


def parse(flags: str):
    return cli.make_parser().parse_args([flags, "gw1.example.net"])


def fake_steady_agent(
    monkeypatch, clock, octets_per_second: int, answer_delays: Sequence[float] = ()
):
    """Replaces the agent with one that has a single interface with steady traffic.

    The interface's input octets grow at a fixed rate, by the time on
    ``clock``.  Each answer can be delayed by a number of seconds, in turn.
    """
    delays = list(answer_delays)

    @asynccontextmanager
    async def session(options):
        yield None

    async def fetch_interfaces(session):
        return [Interface(index=1, name="ge-0/0/0")]

    async def fetch_counters(session, if_indexes):
        clock.now += delays.pop(0) if delays else 0
        return {1: Counters(in_octets=round(octets_per_second * clock.now))}

    monkeypatch.setattr(cli, "_clock", clock)
    monkeypatch.setattr(cli, "_sleep", clock.sleep)
    monkeypatch.setattr(cli, "_session", session)
    monkeypatch.setattr(cli, "fetch_interfaces", fetch_interfaces)
    monkeypatch.setattr(cli, "fetch_counters", fetch_counters)


class FakeClock:
    """A clock that only moves when slept on, optionally sleeping longer than asked"""

    def __init__(self, delays: list[float] = ()):
        self.now = 0.0
        self.delays = list(delays)

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float):
        self.now += seconds + (self.delays.pop(0) if self.delays else 0)
