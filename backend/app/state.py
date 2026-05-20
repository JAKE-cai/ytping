"""Shared application state – avoids circular imports between main and routers."""

from .database import DB_PATH
from .pinger import PingManager
from .pubsub import sub_manager


def _on_ping_result(target_id: int, ts: int, latency_ms, is_loss: int) -> None:
    """Called by PingManager immediately after each probe; fan-out to SSE clients."""
    sub_manager.publish(target_id, {
        "ts":         ts,
        "latency_ms": latency_ms,
        "is_loss":    is_loss,
    })


ping_manager = PingManager(DB_PATH, on_result=_on_ping_result)
