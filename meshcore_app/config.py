"""Configuration loading. YAML file + env overrides, validated with pydantic."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MQTTConfig(BaseModel):
    host: str = "localhost"
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: str = "meshcore-rpc-services"

    request_topic: str = "meshcore/rpc/request"
    response_topic_prefix: str = "meshcore/rpc/response"
    gateway_status_topic: str = "meshcore/gateway/status"
    gateway_health_topic: str = "meshcore/gateway/health"

    qos: int = 1

    def response_topic(self, node_id: str) -> str:
        return f"{self.response_topic_prefix}/{node_id}"


class ServiceConfig(BaseModel):
    db_path: str = "./meshcore_rpc_services.sqlite3"
    default_ttl_s: int = 30
    max_ttl_s: int = 300
    log_level: str = "INFO"


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
        """Load config from YAML (if given) with env overriding."""
        data: dict = {}
        if path:
            p = Path(path)
            if p.exists():
                with p.open() as f:
                    data = yaml.safe_load(f) or {}
        # Instantiating BaseSettings with explicit kwargs lets env vars still
        # override via the SettingsConfigDict env_prefix.
        return cls(**data)
