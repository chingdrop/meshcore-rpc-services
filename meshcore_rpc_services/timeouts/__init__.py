"""Timeout ownership for the request pipeline.

``policy`` owns "how long should this request get?".
``tracker`` owns "enforce that budget on this coroutine".
"""

from __future__ import annotations

from meshcore_rpc_services.timeouts.policy import TimeoutPolicy
from meshcore_rpc_services.timeouts.tracker import PendingTracker

# Backward-compatible shim for older callers.
from meshcore_rpc_services.timeouts.policy import clamp_ttl

__all__ = ["TimeoutPolicy", "PendingTracker", "clamp_ttl"]
