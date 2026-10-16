import pytest

from justnetstat.cli import main
from justnetstat.version import __version__


def test_when_version_is_requested_it_should_print_it_and_exit(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out
