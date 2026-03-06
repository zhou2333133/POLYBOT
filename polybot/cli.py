from __future__ import annotations

import typer

from polybot.core.loader import load_config
from polybot.core.runtime import run_loop

app = typer.Typer(add_completion=False)


@app.command()
def run(
    config: str = typer.Option("config.yaml", "--config", "-c"),
) -> None:
    cfg = load_config(config)
    run_loop(cfg)


if __name__ == "__main__":
    app()
