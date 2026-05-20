"""
Ping manager: launches one asyncio task per enabled target, collects results
and batch-inserts them into the database for high throughput.
"""

import asyncio
import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import aiosqlite

logger = logging.getLogger(__name__)

BATCH_SIZE = 500       # max rows per INSERT
BATCH_INTERVAL = 1.0   # seconds between flushes


class PingManager:
    def __init__(self, db_path: str, on_result=None) -> None:
        self.db_path = db_path
        self._on_result = on_result   # callable(target_id, ts, latency_ms, is_loss)
        self._tasks: Dict[int, asyncio.Task] = {}
        self._queue: asyncio.Queue[Tuple] = asyncio.Queue(maxsize=20000)
        self._writer_task: Optional[asyncio.Task] = None
        self._running = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        self._running = True
        self._writer_task = asyncio.create_task(
            self._batch_writer(), name="ping-batch-writer"
        )
        await self._reload_targets()
        logger.info("PingManager started")

    async def stop(self) -> None:
        self._running = False
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        if self._writer_task:
            self._writer_task.cancel()
        logger.info("PingManager stopped")

    # ------------------------------------------------------------------ #
    # Target management (called from API routers)
    # ------------------------------------------------------------------ #

    async def _reload_targets(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, address, interval_ms FROM targets WHERE enabled=1"
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

        active_ids = {r["id"] for r in rows}
        for tid in list(self._tasks):
            if tid not in active_ids:
                self._tasks[tid].cancel()
                del self._tasks[tid]

        for r in rows:
            if r["id"] not in self._tasks:
                self._launch(r["id"], r["address"], r["interval_ms"])

    def add_target(self, target_id: int, address: str, interval_ms: int = 1000) -> None:
        if target_id in self._tasks:
            self._tasks[target_id].cancel()
        self._launch(target_id, address, interval_ms)

    def remove_target(self, target_id: int) -> None:
        if target_id in self._tasks:
            self._tasks[target_id].cancel()
            del self._tasks[target_id]

    def _launch(self, target_id: int, address: str, interval_ms: int) -> None:
        task = asyncio.create_task(
            self._ping_loop(target_id, address, interval_ms),
            name=f"ping-{target_id}",
        )
        self._tasks[target_id] = task

    # ------------------------------------------------------------------ #
    # Ping loop per target
    # ------------------------------------------------------------------ #

    async def _ping_loop(self, target_id: int, address: str, interval_ms: int) -> None:
        interval = interval_ms / 1000.0
        while True:
            t0 = time.monotonic()
            try:
                latency = await self._ping_once(address, timeout=min(interval, 2.0))
                ts = int(time.time())
                is_loss = 1 if latency is None else 0

                # Notify SSE subscribers immediately (lowest possible latency)
                if self._on_result is not None:
                    try:
                        self._on_result(target_id, ts, latency, is_loss)
                    except Exception as cb_exc:
                        logger.debug("on_result callback error: %s", cb_exc)

                try:
                    self._queue.put_nowait((target_id, ts, latency, is_loss))
                except asyncio.QueueFull:
                    logger.warning("Ping queue full – dropping result for target %d", target_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Ping loop error target=%d: %s", target_id, exc)

            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0.0, interval - elapsed))

    @staticmethod
    async def _ping_once(address: str, timeout: float = 1.0) -> Optional[float]:
        """Return round-trip latency in ms, or None on loss/error."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", "1", "-W", str(max(1, int(timeout))),
                address,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout + 1.5
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return None

            if proc.returncode == 0:
                m = re.search(r"time[<=](\d+\.?\d*)\s*ms", stdout.decode())
                if m:
                    return float(m.group(1))
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Batch writer
    # ------------------------------------------------------------------ #

    async def _batch_writer(self) -> None:
        while self._running or not self._queue.empty():
            batch: List[Tuple] = []
            deadline = time.monotonic() + BATCH_INTERVAL

            while time.monotonic() < deadline and len(batch) < BATCH_SIZE:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                    batch.append(item)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    break

            if batch:
                try:
                    async with aiosqlite.connect(self.db_path) as db:
                        await db.executemany(
                            "INSERT INTO ping_results (target_id, ts, latency_ms, is_loss) "
                            "VALUES (?, ?, ?, ?)",
                            batch,
                        )
                        await db.commit()
                except Exception as exc:
                    logger.error("Batch DB write failed: %s", exc)
            else:
                await asyncio.sleep(0.05)
