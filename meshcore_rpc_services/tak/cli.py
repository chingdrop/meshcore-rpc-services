"""CLI entry point for the meshcore-tak-bridge process.

Lives in this package so it can share `AppConfig`, the topic constants,
and the same logging conventions as the RPC service. Runs as a separate
process via its own console_script entry point in pyproject.toml.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any, Coroutine, Optional

import click
import yaml
from pydantic import ValidationError

from meshcore_rpc_services.config import AppConfig

from .bridge import Bridge


def _configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))
        root.addHandler(handler)


def _load_config(config_path: Optional[str]) -> AppConfig:
    """Load AppConfig, printing actionable errors and exiting on failure."""
    try:
        return AppConfig.load(config_path)
    except yaml.YAMLError as e:
        click.echo(f"Error: invalid YAML in {config_path}:\n  {e}", err=True)
    except ValidationError as e:
        lines = [
            f"  {'->'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in e.errors()
        ]
        click.echo("Error: invalid configuration:\n" + "\n".join(lines), err=True)
    except Exception as e:  # noqa: BLE001
        click.echo(f"Error loading config: {e}", err=True)
    raise SystemExit(1)


def _run_async(coro: "Coroutine[Any, Any, None]") -> None:
    """Run a coroutine, using SelectorEventLoop on Windows for paho compat.

    aiomqtt itself doesn't need this, but matching the service CLI's behavior
    keeps both processes' loop selection consistent on the off chance someone
    runs the bridge alongside the service in a single Python process for dev.
    """
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()
    else:
        asyncio.run(coro)


@click.command()
@click.option(
    "--config", "config_path",
    type=click.Path(dir_okay=False), default=None,
    help="Path to YAML config file (same shape as the RPC service).",
)
@click.option("--log-level", default=None, help="Override config log_level.")
def main(config_path: Optional[str], log_level: Optional[str]) -> None:
    """Run the meshcore → TAK bridge.

    Reads `mc/node/+/{location,state}` and `mc/base/location` from MQTT,
    emits CoT XML over TCP to the configured TAK Server.
    """
    cfg = _load_config(config_path)
    _configure_logging(log_level or cfg.service.log_level)
    bridge = Bridge(cfg)

    async def _amain() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass  # Windows

        bridge_task = asyncio.create_task(bridge.run())
        stop_task = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            {bridge_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done and not bridge_task.done():
            bridge.stop()
            try:
                await asyncio.wait_for(bridge_task, timeout=5.0)
            except asyncio.TimeoutError:
                bridge_task.cancel()
                try:
                    await bridge_task
                except asyncio.CancelledError:
                    pass
        elif bridge_task in done:
            bridge_task.result()

    _run_async(_amain())


if __name__ == "__main__":
    main()
