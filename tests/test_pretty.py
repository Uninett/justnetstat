"""Tests for the value formatters.

Unless noted otherwise, the expected values were captured by running the same
inputs through the original Tcl implementation (``justPretty.tcl``).
"""

from ipaddress import ip_address

import pytest

from justnetstat import pretty


class TestIfDescr:
    @pytest.mark.parametrize(
        "descr, expected",
        [
            ("FastEthernet0/1", "FE0/1"),
            ("GigabitEthernet1/0/1", "GE1/0/1"),
            ("TenGigE0/0/0/1", "TE0/0/0/1"),
            ("Ethernet0", "Ether0"),
            ("Dialer1-B-Channel-1", "Dialer1-B-Ch-1"),
            ("ATM1/0-aal5 layer", "ATM1/0-aal5"),
            ("GigabitEthernet0/1.100 - 802.1q vlan subinterface", "GE0/1.100"),
            ("FastEthernet0/1.10-802.1Q vLAN subif", "FE0/1.10"),
            ("multi mode fiber fddi", "Fddi/MM"),
            ("10/100 utp ethernet (cat 3/5)", "10/100UTP"),
            ("Virtual-Access1", "V-Acc1"),
            ("Virtual-Template1", "V-Templ1"),
        ],
    )
    def test_when_descr_contains_known_phrases_it_should_abbreviate_them(
        self, descr, expected
    ):
        assert pretty.if_descr(descr) == expected

    @pytest.mark.parametrize(
        "descr, expected",
        [
            ("short wave fiber gigabit ethernet", "GBIC/SX"),
            ("gigabit ethernet without GBIC installed", "No-GBIC"),
            ("no gbic present", "No-GBIC"),
            ("long haul fiber gigabit ethernet", "GBIC/LH"),
            ("unsupported gbic gigabit ethernet", "GBIC/??"),
            ("multi mode fiber fast ethernet", "FaEth/MM"),
            ("utp ethernet", "Ether/UTP"),
            ("utp fast ethernet 10/100", "FaEth/UTP"),
        ],
    )
    def test_when_descr_is_a_legacy_catalyst_description_it_should_replace_it(
        self, descr, expected
    ):
        assert pretty.if_descr(descr) == expected

    def test_when_descr_has_repeated_phrases_it_should_abbreviate_only_the_first(self):
        assert (
            pretty.if_descr("GigabitEthernet0/0 GigabitEthernet0/1")
            == "GE0/0-GigabitEther0/1"
        )

    def test_when_descr_contains_spaces_it_should_replace_them_with_dashes(self):
        assert pretty.if_descr("Ethernet0 Ethernet1") == "Ether0-Ethernet1"

    @pytest.mark.parametrize("descr", ["xe-0/0/0", "lo0.0", "fxp0"])
    def test_when_descr_has_nothing_to_abbreviate_it_should_return_it_unchanged(
        self, descr
    ):
        assert pretty.if_descr(descr) == descr

    def test_when_descr_is_empty_it_should_return_none(self):
        assert pretty.if_descr("") == "none"

    def test_when_utp_fast_ethernet_has_no_suffix_it_should_not_treat_it_as_legacy(
        self,
    ):
        assert pretty.if_descr("utp fast ethernet") == "utp-fast-ethernet"

    def test_when_layer_is_followed_by_a_newline_it_should_not_strip_it(self):
        assert pretty.if_descr("ATM1/0-aal5 layer\n") == "ATM1/0-aal5-layer\n"

    def test_when_wildcard_pattern_meets_a_newline_it_should_match_across_it(self):
        assert pretty.if_descr("10/100 utp ethernet x\ny") == "10/100UTP"


class TestIfType:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("ethernetCsmacd", "ether"),
            ("propPointToPointSerial", "prop-ser"),
            ("softwareLoopback", "loopback"),
            ("bgppolicyaccounting", "bgpacc"),
            ("atmSubInterface", "atm-sub"),
            ("propVirtual", "propVirt"),
            ("gigabitEthernet", "GiEth"),
            ("mplsTunnel", "mplsTun"),
            ("ieee8023adLag", "LinkAgg"),
        ],
    )
    def test_when_type_has_an_abbreviation_it_should_return_it(self, name, expected):
        assert pretty.if_type(name) == expected

    @pytest.mark.parametrize("name", ["other", "tunnel", "frameRelay", "rfc877x25"])
    def test_when_type_has_no_abbreviation_it_should_return_it_unchanged(self, name):
        assert pretty.if_type(name) == name


class TestAdminStatus:
    @pytest.mark.parametrize("name, expected", [("up", "on"), ("down", "off")])
    def test_when_status_has_an_abbreviation_it_should_return_it(self, name, expected):
        assert pretty.admin_status(name) == expected

    def test_when_status_has_no_abbreviation_it_should_return_it_unchanged(self):
        assert pretty.admin_status("testing") == "testing"


class TestOperStatus:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("testing", "test"),
            ("dormant", "dorm"),
            ("lowerLayerDown", "lldn"),
        ],
    )
    def test_when_status_has_an_abbreviation_it_should_return_it(self, name, expected):
        assert pretty.oper_status(name) == expected

    @pytest.mark.parametrize("name", ["up", "down", "unknown", "notPresent"])
    def test_when_status_has_no_abbreviation_it_should_return_it_unchanged(self, name):
        assert pretty.oper_status(name) == name


class TestMtu:
    @pytest.mark.parametrize("value, expected", [(1500, "1500"), (0, "0")])
    def test_it_should_format_the_value(self, value, expected):
        assert pretty.mtu(value) == expected

    @pytest.mark.parametrize("value", [None, 2147483647])
    def test_when_mtu_is_missing_or_unlimited_it_should_return_none(self, value):
        assert pretty.mtu(value) == "none"


class TestSpeed:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (0, "0 "),
            (0.0005, "0 "),
            (0.5, " 0.5 "),
            (1, "   1 "),
            (999, " 999 "),
            (1000, "   1k"),
            (1500, " 1.5k"),
            (64000, "  64k"),
            (1000000, "   1M"),
            (1544000, "1.54M"),
            (100000000, " 100M"),
            (1000000000, "   1G"),
            (10000000000, "  10G"),
            (4294967295, "4.29G"),
            (400000000000, " 400G"),
        ],
    )
    def test_it_should_pick_a_suitable_unit(self, value, expected):
        assert pretty.speed(value) == expected

    @pytest.mark.parametrize(
        "value, expected",
        [
            (999.4999, " 999 "),
            (999.5, "   1k"),
            (999499.9, " 999k"),
            (999500, "   1M"),
            (999999, "   1M"),
            (999999999, "   1G"),
        ],
    )
    def test_when_value_rounds_up_to_the_next_unit_it_should_use_that_unit(
        self, value, expected
    ):
        # The Tcl implementation showed values that round up in exponent notation:
        # "1e+03 ", "1e+03k" and "1e+03M".
        assert pretty.speed(value) == expected

    @pytest.mark.parametrize(
        "value, expected",
        [
            (999, "0"),
            (1500, "1"),
            (64000, "64"),
            (10000000000, "10000000"),
        ],
    )
    def test_when_kilo_is_requested_it_should_show_whole_thousands(
        self, value, expected
    ):
        assert pretty.speed(value, kilo=True) == expected


class TestLongTime:
    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (0, "0:00"),
            (5, "0:05"),
            (59, "0:59"),
            (61, "1:01"),
            (100, "1:40"),
            (3599, "59:59"),
            (604801, "1w0d"),
            (700000, "1w1d"),
            (10000000, "16w3d"),
        ],
    )
    def test_it_should_show_the_two_most_significant_units(self, seconds, expected):
        assert pretty.long_time(seconds) == expected

    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (3601, "1h00m"),
            (4000, "1h06m"),
            (86399, "23h59m"),
            (86401, "1d00h"),
            (90000, "1d01h"),
            (604799, "6d23h"),
        ],
    )
    def test_when_duration_is_in_hours_or_days_it_should_suffix_both_units(
        self, seconds, expected
    ):
        # The Tcl implementation left out the second suffix and its zero padding.
        # It showed e.g. "1h6" and "1d1".
        assert pretty.long_time(seconds) == expected

    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (60, "1:00"),
            (3600, "1h00m"),
            (86400, "1d00h"),
            (604800, "1w0d"),
        ],
    )
    def test_when_duration_is_exactly_one_unit_it_should_use_that_unit(
        self, seconds, expected
    ):
        # The Tcl implementation showed these as "0:60", "60:00", "24h0" and "7d0"
        assert pretty.long_time(seconds) == expected


class TestNetmask:
    @pytest.mark.parametrize(
        "ip, prefix_length, expected",
        [
            ("192.0.2.1", 32, "255.255.255.255"),
            ("198.51.100.1", 30, "255.255.255.252"),
            ("203.0.113.1", 0, "0.0.0.0"),
        ],
    )
    def test_when_address_is_ipv4_it_should_show_a_dotted_netmask(
        self, ip, prefix_length, expected
    ):
        assert pretty.netmask(ip_address(ip), prefix_length) == expected

    def test_when_address_is_ipv6_it_should_show_the_prefix_length(self):
        assert pretty.netmask(ip_address("2001:db8::1"), 64) == "/64"

    def test_when_prefix_length_is_unknown_it_should_return_none(self):
        assert pretty.netmask(ip_address("192.0.2.1"), None) == "none"


class TestPhysAddress:
    def test_it_should_show_colon_separated_lowercase_hex(self):
        assert pretty.phys_address(bytes.fromhex("02AB00000001")) == "02:ab:00:00:00:01"

    @pytest.mark.parametrize("address", [b"", None])
    def test_when_address_is_missing_it_should_return_null(self, address):
        assert pretty.phys_address(address) == "(null)"
