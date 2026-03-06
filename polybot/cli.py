from __future__ import annotations

import argparse

from dotenv import load_dotenv

from polybot.core.loader import load_config
from polybot.core.runtime import run_loop


def main() -> None:
    parser = argparse.ArgumentParser(description="POLYBOT 命令行")
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    args = parser.parse_args()
    try:
        load_dotenv()
        cfg = load_config(args.config)
        run_loop(cfg)
    except Exception as exc:
        raise SystemExit(f"启动失败: {exc}")


if __name__ == "__main__":
    main()
