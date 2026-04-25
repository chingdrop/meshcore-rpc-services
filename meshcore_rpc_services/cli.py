"""Click CLI."""

from __future__ import annotations

import asyncio
import logging
import signal
import sqlite3
import sys
from typing import Any, Coroutine, Optional

import click
import yaml
from pydantic import ValidationError

from meshcore_rpc_services.config import AppConfig
from meshcore_rpc_services.persistence import Store
from meshcore_rpc_services.retention import RetentionSweeper
from meshcore_rpc_services.transport import Service


class _ContextFilter(logging.Filter):
    """Fills in request-context fields so the format string never has KeyError."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = "-"  # type: ignore[attr-defined]
        if not hasattr(record, "node_id"):
            record.node_id = "-"  # type: ignore[attr-defined]
        if not hasattr(record, "rpc_type"):
            record.rpc_type = "-"  # type: ignore[attr-defined]
        return True


def _configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.addFilter(_ContextFilter())
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s"
            " [%(request_id)s %(node_id)s %(rpc_type)s] %(message)s"
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
    except Exception as e:
        click.echo(f"Error loading config: {e}", err=True)
    raise SystemExit(1)


def _open_store(db_path: str) -> Store:
    """Open the Store, printing an actionable error and exiting on failure."""
    try:
        return Store(db_path)
    except (OSError, sqlite3.OperationalError) as e:
        click.echo(f"Error: could not open database at {db_path!r}: {e}", err=True)
    except Exception as e:
        click.echo(f"Error: database error: {e}", err=True)
    raise SystemExit(1)


def _run_async(coro: "Coroutine[Any, Any, None]") -> None:
    """Run a coroutine, using SelectorEventLoop on Windows.

    ProactorEventLoop (the Windows default since Python 3.8) does not implement
    add_reader/add_writer, which paho-mqtt requires. SelectorEventLoop does.
    Creating it explicitly avoids the deprecated WindowsSelectorEventLoopPolicy.
    """
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()
    else:
        asyncio.run(coro)


@click.group()
def main() -> None:
    """meshcore-rpc-services: application-layer RPC services."""


@main.command()
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default=None)
def initdb(config_path: Optional[str]) -> None:
    """Create the SQLite schema."""
    cfg = _load_config(config_path)
    _configure_logging(cfg.service.log_level)
    store = _open_store(cfg.service.db_path)
    store.close()
    click.echo(f"Initialized SQLite DB at {cfg.service.db_path}")


@main.command()
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default=None)
def run(config_path: Optional[str]) -> None:
    """Run the RPC service."""
    cfg = _load_config(config_path)
    _configure_logging(cfg.service.log_level)
    service = Service(cfg)

    async def _amain() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass  # Windows

        service_task = asyncio.create_task(service.run())
        stop_task = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            {service_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done and not service_task.done():
            service_task.cancel()
            try:
                await service_task
            except asyncio.CancelledError:
                pass
        elif service_task in done:
            service_task.result()

    _run_async(_amain())


@main.command()
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default=None)
@click.option("--days", type=int, default=None, help="Override retention days")
def purge(config_path: Optional[str], days: Optional[int]) -> None:
    """Run a one-shot retention sweep and exit."""
    cfg = _load_config(config_path)
    _configure_logging(cfg.service.log_level)
    effective_days = days if days is not None else cfg.service.retention.days

    async def _amain() -> None:
        store = _open_store(cfg.service.db_path)
        try:
            sweeper = RetentionSweeper(
                store, days=effective_days, interval_s=3600.0
            )
            deleted = await sweeper.run_once()
            click.echo(f"Purged {deleted} request rows older than {effective_days}d.")
        finally:
            store.close()

    _run_async(_amain())


if __name__ == "__main__":
    main()
