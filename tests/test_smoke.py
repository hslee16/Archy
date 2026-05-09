from archy import __version__


def test_version_is_a_string() -> None:
    assert isinstance(__version__, str)
