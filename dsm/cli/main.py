"""CLI entry point."""

import typer

from dsm.cli.commands import match

app = typer.Typer(no_args_is_help=True)
app.command("match")(match)


@app.command("version", hidden=True)
def _version() -> None:
    """Print version."""
    typer.echo("0.1.0")


if __name__ == "__main__":
    app()
