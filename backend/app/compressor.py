"""
Periodic job: compress ping_results older than RETENTION_SECONDS into hourly
buckets stored in ping_summary, then delete the raw rows.
"""

import asyncio
import logging
import time

import aiosqlite

logger = logging.getLogger(__name__)

RETENTION_SECONDS = 7 * 24 * 3600   # 7 days of raw data
BUCKET_SIZE = 3600                   # 1-hour summary buckets


async def compress_old_data(db_path: str, cutoff: int | None = None) -> dict:
    """Compress raw ping_results older than *cutoff* into hourly ping_summary buckets.
    Returns a dict with stats: {"compressed": int, "buckets": int}.
    """
    if cutoff is None:
        cutoff = int(time.time()) - RETENTION_SECONDS
    logger.info("Compression run: cutoff=%d", cutoff)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute("SELECT id FROM targets") as cur:
            target_ids = [r["id"] for r in await cur.fetchall()]

        compressed_total = 0

        for tid in target_ids:
            async with db.execute(
                """
                SELECT
                    (ts / ?) * ?  AS bucket_ts,
                    COUNT(*)      AS cnt_total,
                    SUM(is_loss)  AS cnt_loss,
                    SUM(CASE WHEN latency_ms <   30                        THEN 1 ELSE 0 END) AS c1,
                    SUM(CASE WHEN latency_ms >=  30 AND latency_ms <   60  THEN 1 ELSE 0 END) AS c2,
                    SUM(CASE WHEN latency_ms >=  60 AND latency_ms <  100  THEN 1 ELSE 0 END) AS c3,
                    SUM(CASE WHEN latency_ms >= 100 AND latency_ms <  200  THEN 1 ELSE 0 END) AS c4,
                    SUM(CASE WHEN latency_ms >= 200 AND latency_ms <  500  THEN 1 ELSE 0 END) AS c5,
                    SUM(CASE WHEN latency_ms >= 500 AND latency_ms < 1000  THEN 1 ELSE 0 END) AS c6,
                    SUM(CASE WHEN latency_ms >= 1000                       THEN 1 ELSE 0 END) AS c7
                FROM ping_results
                WHERE target_id = ? AND ts < ?
                GROUP BY bucket_ts
                """,
                (BUCKET_SIZE, BUCKET_SIZE, tid, cutoff),
            ) as cur:
                rows = await cur.fetchall()

            if not rows:
                continue

            for r in rows:
                await db.execute(
                    """
                    INSERT INTO ping_summary
                        (target_id, bucket_ts,
                         cnt_1_30, cnt_31_60, cnt_61_100, cnt_101_200,
                         cnt_201_500, cnt_501_1000, cnt_over_1000,
                         cnt_loss, cnt_total)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(target_id, bucket_ts) DO UPDATE SET
                        cnt_1_30      = excluded.cnt_1_30,
                        cnt_31_60     = excluded.cnt_31_60,
                        cnt_61_100    = excluded.cnt_61_100,
                        cnt_101_200   = excluded.cnt_101_200,
                        cnt_201_500   = excluded.cnt_201_500,
                        cnt_501_1000  = excluded.cnt_501_1000,
                        cnt_over_1000 = excluded.cnt_over_1000,
                        cnt_loss      = excluded.cnt_loss,
                        cnt_total     = excluded.cnt_total
                    """,
                    (
                        tid, r["bucket_ts"],
                        r["c1"], r["c2"], r["c3"], r["c4"],
                        r["c5"], r["c6"], r["c7"],
                        r["cnt_loss"], r["cnt_total"],
                    ),
                )

            res = await db.execute(
                "DELETE FROM ping_results WHERE target_id=? AND ts<?",
                (tid, cutoff),
            )
            compressed_total += res.rowcount

        await db.commit()

        async with db.execute("SELECT COUNT(*) AS c FROM ping_summary") as cur:
            buckets = (await cur.fetchone())["c"]

    logger.info("Compression complete: buckets=%d", buckets)
    return {"compressed": compressed_total, "buckets": buckets}


async def run_compressor(db_path: str) -> None:
    """Run compression once per hour, starting 1 hour after boot."""
    while True:
        await asyncio.sleep(BUCKET_SIZE)
        try:
            await compress_old_data(db_path)
        except Exception as exc:
            logger.error("Compressor error: %s", exc)
