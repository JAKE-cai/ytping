"""
In-process pub/sub fan-out for SSE streaming.
All access happens in the same asyncio event loop, so no locking is needed.
"""

import asyncio
import logging
from typing import Dict, Set

logger = logging.getLogger(__name__)


class SubscriptionManager:
    def __init__(self) -> None:
        # target_id → set of per-client queues
        self._subs: Dict[int, Set[asyncio.Queue]] = {}

    def subscribe(self, target_id: int) -> asyncio.Queue:
        """Register a new SSE client. Returns its private queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=120)
        self._subs.setdefault(target_id, set()).add(q)
        logger.debug("SSE subscribe target=%d  total=%d",
                     target_id, len(self._subs[target_id]))
        return q

    def unsubscribe(self, target_id: int, q: asyncio.Queue) -> None:
        """Remove a client queue (called on disconnect)."""
        bucket = self._subs.get(target_id)
        if bucket:
            bucket.discard(q)
            if not bucket:
                del self._subs[target_id]
        logger.debug("SSE unsubscribe target=%d", target_id)

    def publish(self, target_id: int, payload: dict) -> None:
        """Fan-out a ping result to all subscribers of target_id."""
        for q in list(self._subs.get(target_id, [])):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow consumer – drop the event rather than block
                logger.debug("SSE queue full for target %d – event dropped", target_id)


# Module-level singleton shared across state.py and routers
sub_manager = SubscriptionManager()
