"""Click CLI."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import Optional

import click

from meshcore_rpc_services.config import AppConfig
from meshcore_rpc_services.persistence import SqliteRequestRepository, SqliteStore
from meshcore_rpc_services.retention import RetentionSweeper
from meshcore_rpc_services.transport import Service


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


@click.group()
def main() -> None:
    """meshcore-rpc-services: application-layer RPC services."""


@main.command()
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default=None)
def initdb(config_path: Optional[str]) -> None:
    """Create the SQLite schema."""
    cfg = AppConfig.load(config_path)
    _configure_logging(cfg.service.log_level)
    store = SqliteStore(cfg.service.db_path)
    store.close()
    click.echo(f"Initialized SQLite DB at {cfg.service.db_path}")


@main.command()
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default=None)
def run(config_path: Optional[str]) -> None:
    """Run the RPC service."""
    cfg = AppConfig.load(config_path)
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

    asyncio.run(_amain())


@main.command()
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default=None)
@click.option("--days", type=int, default=None, help="Override retention days")
def purge(config_path: Optional[str], days: Optional[int]) -> None:
    """Run a one-shot retention sweep and exit."""
    cfg = AppConfig.load(config_path)
    _configure_logging(cfg.service.log_level)
    effective_days = days if days is not None else cfg.service.retention.days

    async def _amain() -> None:
        store = SqliteStore(cfg.service.db_path)
        try:
            repo = SqliteRequestRepository(store)
            sweeper = RetentionSweeper(
                repo, days=effective_days, interval_s=3600.0
            )
            deleted = await sweeper.run_once()
            click.echo(f"Purged {deleted} request rows older than {effective_days}d.")
        finally:
            store.close()

    asyncio.run(_amain())


if __name__ == "__main__":
    main()
