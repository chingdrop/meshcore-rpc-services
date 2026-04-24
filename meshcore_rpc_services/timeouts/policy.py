"""TTL resolution policy.

The effective TTL for a request is resolved once, at the point we enter the
core pipeline, by this one module. That keeps the policy explicit and easy
to change.

Rules (in order):

1. If the request provided a ``ttl`` field, start with that.
2. Otherwise, look up the per-type default.
3. Otherwise, fall back to the global default.
4. Clamp the result to ``[min_s, max_s]``.

The returned value is always an int >= 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(frozen=True)
class TimeoutPolicy:
    default_s: int = 30
    min_s: int = 1
    max_s: int = 300
    per_type_default_s: Mapping[str, int] = field(default_factory=dict)

    def resolve(self, *, request_type: str, requested_ttl: Optional[int]) -> int:
        if requested_ttl is not None:
            base = int(requested_ttl)
        elif request_type in self.per_type_default_s:
            base = int(self.per_type_default_s[request_type])
        else:
            base = int(self.default_s)
        return max(self.min_s, min(base, self.max_s))


# ---------------------------------------------------------------------------
# Backward-compatible shim
# ---------------------------------------------------------------------------


def clamp_ttl(requested: Optional[int], default_s: int, max_s: int) -> int:
    """Older helper. Prefer :class:`TimeoutPolicy` in new code."""
    policy = TimeoutPolicy(default_s=default_s, max_s=max_s)
    return policy.resolve(request_type="_legacy", requested_ttl=requested)
