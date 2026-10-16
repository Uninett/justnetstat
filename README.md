# justnetstat

`justnetstat` shows netstat-like information from routers and other network
equipment, over SNMP: interfaces, traffic and error counters, routes,
connections and BGP sessions.

This is a Python port of the original Tcl/Scotty tool of the same name, written
at UNINETT in the late 1990s.

## Status

Early development. The command line program does nothing useful yet.

## Development

The project is managed with [uv](https://docs.astral.sh/uv/):

```sh
uv sync                    # create a virtualenv with all development tools
uv run pre-commit install  # run ruff on every commit
uv run pytest              # run the test suite
uv run justnetstat --help
```
