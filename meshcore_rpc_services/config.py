"""Configuration loading. YAML file + env overrides, validated with pydantic."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Literal, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from meshcore_rpc_services.mqtt import topics as _topics


class MQTTConfig(BaseModel):
    host: str = "localhost"
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: str = "meshcore-rpc-services"
    qos: int = 1

    def response_topic(self, node_id: str) -> str:
        """Kept for backward compatibility. Prefer :func:`topics.rpc_response_topic`."""
        return _topics.rpc_response_topic(node_id)


class TimeoutConfig(BaseModel):
    """Request TTL policy.

    Used to construct a :class:`meshcore_rpc_services.timeouts.TimeoutPolicy`.
    """

    default_s: int = 30
    min_s: int = 1
    max_s: int = 300
    # Per-request-type defaults. Useful when e.g. a type always needs
    # internet fetches and 30s is too short.
    per_type_default_s: Dict[str, int] = Field(default_factory=dict)


class RetentionConfig(BaseModel):
    days: int = 30
    # How often the sweeper runs. 1h is fine for a personal mesh.
    interval_s: float = 3600.0


class BaseLocationConfig(BaseModel):
    source: Literal["static", "gpsd", "mqtt"] = "static"
    static_lat: Optional[float] = None
    static_lon: Optional[float] = None


class ServiceConfig(BaseModel):
    db_path: str = "./data/meshcore_rpc_services.sqlite3"
    log_level: str = "INFO"
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    base: BaseLocationConfig = Field(default_factory=BaseLocationConfig)


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MESHCORE_RPC_SERVICES_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    mqtt: MQTTConfig = Field(default_factory=MQTTConfig)
    service: ServiceConfig = Field(default_factory=ServiceConfig)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppConfig":
        """Load config from YAML (if given), with env overriding."""
        data: dict = {}
        if path:
            p = Path(path)
            if p.exists():
                with p.open() as f:
                    data = yaml.safe_load(f) or {}
        return cls(**data)
