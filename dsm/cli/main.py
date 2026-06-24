"""CLI entry point."""

import os

# AD-108: tolerate the duplicate OpenMP runtime on macOS. Milvus Lite (via faiss) and the PII NER
# stack (spaCy/Presidio → torch + scikit-learn) each ship their own statically-linked
# ``libomp.dylib`` in their pip wheels; the moment a single ``dsm match`` reaches scoring it loads
# both and the second OpenMP init ``abort()``s the process (OMP: Error #15). Set here — at the
# entrypoint, **before** any OpenMP-linked library is imported — because OpenMP reads this at
# library-init time. ``setdefault`` so an explicit override (e.g. ``=FALSE`` to surface conflicts
# while debugging) still wins. See ``docs/tech.md`` and AD-108 for the full rationale.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import typer  # noqa: E402  — must follow the KMP_DUPLICATE_LIB_OK guard above

from dsm.cli.commands import explain, index, ingest, match  # noqa: E402

app = typer.Typer(no_args_is_help=True)
app.command("match")(match)
app.command("explain")(explain)
app.command("ingest")(ingest)
app.command("index")(index)


@app.command("version", hidden=True)
def _version() -> None:
    """Print version."""
    typer.echo("0.1.0")


if __name__ == "__main__":
    app()
