from __future__ import annotations

import typer
from dotenv import load_dotenv

from polybot.core.loader import load_config
from polybot.core.runtime import run_loop

app = typer.Typer(add_completion=False)


@app.command()
def run(
    config: str = typer.Option("config.yaml", "--config", "-c"),
) -> None:
    try:
        load_dotenv()
        cfg = load_config(config)
        run_loop(cfg)
    except Exception as exc:
        typer.echo(f"启动失败: {exc}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
