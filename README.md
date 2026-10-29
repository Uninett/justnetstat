# justnetstat

`justnetstat` shows netstat-like information from routers and other network
equipment, over SNMP: interfaces, traffic and error counters, routes,
connections and BGP sessions.

The original `justnetstat` was written in Tcl by Jarle Greipsland at UNINETT
(now Sikt). Its oldest copyright notice is from 1997. It was built on Scotty,
the Tcl extension for network management. This is a Python port of it, which
aims to keep its output while reading modern MIBs.

## Status

Work in progress. These parts of the original are ported:

- `-i`: the interface list, with its `-a`, `-d`, `-D`, `-m`, `-h` and `-k`
  variants
- `-B`: interface traffic counters, or traffic rates with `-w` and `-c`

The other modes report that they are not implemented yet, and `--help` marks the
options that have no effect yet. See [TODO](#todo).

## Usage

```sh
justnetstat -i <router>              # list interfaces
justnetstat -B -w 5 <router>         # show traffic rates every 5 seconds
justnetstat --help                   # list all options
```

By default, `justnetstat` uses the SNMP community `public` on port 161, with a 5
second timeout. `-y`, `-P` and `-t` change these. It tries SNMPv2c first, and
falls back to SNMPv1 if the agent does not answer. A router name is resolved by
the system resolver, so the DNS search path can supply its domain.

Single-letter flags can be combined, as in `-idnh`. Since `-h` means "no
headers", help is only available as `--help`.

## Scripts that parse the output

Zino 1's traffic statistics run `justnetstat -inh` and `justnetstat -idnh`
daily, and parse their output. Their parsing is a compatibility contract, which
the tests check:

- `-inh`: each line is split on whitespace. Field 0 is the ifIndex, field 2 the
  admin status, and field 4 the address. Interfaces whose admin status is `off`
  are skipped. The address becomes part of a data file name, so it must stay the
  interface's lowest IPv4 address, or `none`.
- `-idnh`: each line must match `^(\d+)\s+(\S+)\s+\S+\s+"(.*)"$`: the ifIndex, a
  one-word name, a one-word speed, and the ifAlias in double quotes.
- Interface names and speeds therefore never contain whitespace, and `-h`
  suppresses the header.
- The scripts merge stderr into stdout. Nothing but error messages may be
  written to stderr. Errors must contain `noResponse` or `Could not retrieve`,
  or exit with a non-zero status.

## How this port differs from the original

The output stays as close to the original's as possible. These are the
deliberate differences.

### Data sources

- **MIBs:** values are read using modern MIBs (IF-MIB, IANAifType-MIB, IP-MIB),
  shipped with the package. MIBs installed on the system are not used.
- **Interface names** come from `ifName`, which did not exist when the original
  was written. Devices without it get the original's abbreviated `ifDescr`, e.g.
  `GE0/1`.
- **IP addresses** come from IP-MIB's `ipAddressTable`, which also has IPv6
  addresses. When it has no IPv4 addresses, they come from `ipAddrTable`, which
  the original used. Broadcast and anycast entries are left out, since
  `ipAddrTable` only listed the interfaces' own addresses.
- **Speeds** use `ifHighSpeed` when `ifSpeed` is saturated, in every view. The
  original's `-m` showed `4.29G` for every interface of 10 Gbit/s or more.
- **Traffic counters** are the 64-bit `ifHC*` counters, so rates are right on
  fast links. Interfaces without them fall back to the 32-bit counters, and a
  32-bit counter that wraps between samples is handled. Packet counts add up
  unicast, multicast and broadcast packets, since the original's non-unicast
  counters are deprecated.
- **Status and type names** are printed with the original's abbreviations
  (`on`/`off`, `lldn`, `ether`, `propVirt`), mapped from the modern MIB names.
  Values the original had no abbreviation for, such as `notPresent`, are printed
  as their MIB names.

### Formatting

- **Columns always line up.** Each column is as wide as its widest value, with
  the original's widths as minimums. The original put some columns next to each
  other without a space, so long values ran into the next column. When all
  values fit, rows look exactly like the original's.
- **Headers line up with their columns.** Some of the original's headers were
  off by one character.
- **Rate columns never shrink** between rounds, so the display does not jump
  around.
- **IPv6 addresses** are only listed with `-a`, after the IPv4 ones. Their
  netmasks are shown as prefix lengths, e.g. `/64`, while IPv4 netmasks stay
  dotted.
- **Missing values** print as `none`, never as empty strings, so
  whitespace-separated fields cannot shift.
- **Rounding:** a value just under a unit boundary prints as e.g. `1M`, where
  the original printed `1e+03k`.
- **Durations** (for the coming BGP views) print as e.g. `1h06m` and `1d01h`,
  where the original printed `1h6` and `1d1`. Exact unit boundaries are also
  fixed: 60 seconds is `1:00`, not `0:60`.
- **Text from agents**, e.g. interface descriptions, is printed as the bytes the
  agent sent, whatever the locale's encoding. That holds even for text that is
  not valid UTF-8.
- **Trailing whitespace** is removed.

### Behaviour

- **`-B` rates** are divided by the measured time between samples, not by the
  `-w` interval. A delayed sample therefore does not make one round's rates too
  high and the next too low.
- **Gaps in `-B` samples** do not cause spikes. An interface missing from a
  sample is left out of the rates that need it. Counters that change between 32
  and 64 bits give a rate of zero for that round.
- **Errors** include their reason, e.g. why a session could not be opened.
- **Options** `-t`, `-c`, `-w` and `-P` must be whole numbers of at least 1. A
  missing router name is a usage error with exit status 2, where the original
  exited silently with status 1.
- **Ctrl-C** exits quietly, with status 130.
- **A closed pipe**, as in `justnetstat -B -w 1 <router> | head`, exits quietly,
  with status 141.

## TODO

Modes still to port from the original:

- [ ] `-r`: the routing table, with an optional route prefix and count. The
  original read `ipRouteTable`, which modern routers rarely support;
  `inetCidrRouteTable` is the modern replacement. Aligned columns mean nothing
  is printed until the whole table is fetched, which matters for large routing
  tables.
- [ ] `-C`: interface error counters, as a snapshot or as rates.
  `traffic.RateTable` shows how to keep columns steady between rounds.
- [ ] `-a` on its own: TCP connections and UDP listeners. The modern
  `tcpConnectionTable` and `udpEndpointTable` also cover IPv6.
- [ ] `-b`: the BGP summary, with `-p` (peers), `-T` (timers) and `-4` (the BGP4
  path table). Modern routers need vendor MIBs for IPv6 and 4-byte AS numbers;
  Zino 2 handles Juniper and Cisco.
- [ ] `-n`: turning off DNS lookups of addresses, which only matters for `-r`
  and `-a`.

Other open items:

- [ ] Set up CI.
- [ ] Deploy the port to the production server, where `/local/bin/snmpnetstat`
  is a symlink to `justnetstat`.
- [ ] netsnmp-cffi ignores the error status of SNMP responses. `justnetstat`
  works around the cases it can recognise. An SNMPv2c `genErr` that echoes the
  request still looks like the end of a table, so the table is silently cut
  short.

## Development

The project is managed with [uv](https://docs.astral.sh/uv/):

```sh
uv sync                    # create a virtualenv with all development tools
uv run pre-commit install  # run ruff on every commit
uv run pytest              # run the test suite
uv run justnetstat --help
```

The tests simulate SNMP agents with
[snmpsim](https://github.com/lextudio/snmpsim), using the data files in
`tests/snmp_fixtures/`. snmpsim runs through `uvx`, which comes with uv, in its
own Python 3.11 environment, since it does not work on newer Python versions.

Lines are at most 88 characters long, which ruff enforces. Long, descriptive
test names are welcome: when one makes its `def` line too long, that line gets a
`# noqa: E501` tag. Other lines may only be longer for a good reason, such as an
expected output line in a test. They get a `# noqa: E501` tag too, and a comment
or docstring says why.

Test data must never contain real router names or IP addresses. Use
documentation addresses (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 and
2001:db8::/32) and names like `gw1.example.net`.

## License

justnetstat is licensed under the Apache License 2.0. See `LICENSE` and
`NOTICE`. The original Tcl implementation was released by UNINETT under a
BSD-style license.
