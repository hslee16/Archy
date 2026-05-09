import click

from archy import __version__


@click.group()
@click.version_option(__version__)
def main() -> None:
    """archy — architectural sensor for Python codebases."""


@main.command()
def check() -> None:
    """Run rule checks against the current codebase. (not implemented)"""
    raise click.ClickException("not implemented yet")


@main.command()
def score() -> None:
    """Compute the architecture score for the current commit. (not implemented)"""
    raise click.ClickException("not implemented yet")


@main.command()
def trend() -> None:
    """Show the score trend over recorded history. (not implemented)"""
    raise click.ClickException("not implemented yet")
