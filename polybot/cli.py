from __future__ import annotations

import sys

import click
import typer
from dotenv import load_dotenv

from polybot.core.loader import load_config
from polybot.core.runtime import run_loop


def _patch_click_metavar() -> None:
    param_cls = getattr(click, "Parameter", None)
    if not param_cls:
        return
    original = param_cls.make_metavar

    def _patched(self, ctx=None):
        if ctx is None:
            ctx = click.get_current_context(silent=True)
        return original(self, ctx)

    param_cls.make_metavar = _patched


_patch_click_metavar()

app = typer.Typer(add_completion=False, invoke_without_command=True)


@app.callback()
def main(
    config: str = typer.Option("config.yaml", "--config", "-c"),
) -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        sys.argv.pop(1)
    try:
        load_dotenv()
        cfg = load_config(config)
        run_loop(cfg)
    except Exception as exc:
        typer.echo(f"启动失败: {exc}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
