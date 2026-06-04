"""Context flag for /get fast price probes (no retries, no lazy symbol lookups)."""

from __future__ import annotations

import contextvars

price_fast_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar("price_fast", default=False)
