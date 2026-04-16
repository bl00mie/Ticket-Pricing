"""Tests for the SQLite price cache."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from cache import PriceCache

SAMPLE_RESULTS = [
    {"offer_id": "off_1", "total_price": 250.00, "currency": "USD"},
    {"offer_id": "off_2", "total_price": 310.00, "currency": "USD"},
]


@pytest.fixture()
def cache(tmp_path):
    """Create a PriceCache backed by a temp directory."""
    return PriceCache(db_path=str(tmp_path / "test_cache.db"))


class TestWALMode:
    """p) SQLite WAL mode: verify PRAGMA journal_mode returns 'wal'."""

    def test_wal_enabled(self, cache: PriceCache):
        conn = cache._get_connection()
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"
        conn.close()


class TestCacheWriteRead:
    """Basic write/read cycle."""

    def test_write_then_read(self, cache: PriceCache):
        cache.write(
            origin="LAX", destination="JFK",
            departure_date="2026-05-15", return_date=None,
            passengers=1, cabin="ECONOMY", currency="USD",
            provider="duffel", results=SAMPLE_RESULTS,
        )
        results, age = cache.get_cached(
            "LAX", "JFK", "2026-05-15", None, "ECONOMY", "USD",
        )
        assert results is not None
        assert len(results) == 2
        assert results[0]["offer_id"] == "off_1"
        assert age is not None and age >= 0

    def test_cache_miss_empty(self, cache: PriceCache):
        results, age = cache.get_cached(
            "SFO", "ORD", "2026-06-01", None, "ECONOMY", "USD",
        )
        assert results is None
        assert age is None


class TestCacheTTL:
    """c/d) Cache hit within TTL, miss after TTL."""

    def test_fresh_cache_hit(self, cache: PriceCache):
        cache.write(
            origin="LAX", destination="JFK",
            departure_date="2026-05-15", return_date=None,
            passengers=1, cabin="ECONOMY", currency="USD",
            provider="duffel", results=SAMPLE_RESULTS,
        )
        results, _ = cache.get_cached(
            "LAX", "JFK", "2026-05-15", None, "ECONOMY", "USD",
        )
        assert results is not None

    def test_expired_cache_miss(self, cache: PriceCache):
        # Manually insert a row with old timestamp
        old_time = (
            datetime.now(timezone.utc) - timedelta(hours=5)
        ).isoformat()
        conn = cache._get_connection()
        conn.execute(
            """INSERT INTO price_cache
            (origin,destination,departure_date,return_date,passengers,
             cabin,currency,fetched_at,provider,response_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("LAX", "JFK", "2026-05-15", None, 1, "ECONOMY", "USD",
             old_time, "duffel", json.dumps(SAMPLE_RESULTS)),
        )
        conn.commit()
        conn.close()

        results, _ = cache.get_cached(
            "LAX", "JFK", "2026-05-15", None, "ECONOMY", "USD",
        )
        assert results is None  # expired

    def test_expired_cache_returned_with_ignore_ttl(self, cache: PriceCache):
        old_time = (
            datetime.now(timezone.utc) - timedelta(hours=5)
        ).isoformat()
        conn = cache._get_connection()
        conn.execute(
            """INSERT INTO price_cache
            (origin,destination,departure_date,return_date,passengers,
             cabin,currency,fetched_at,provider,response_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("LAX", "JFK", "2026-05-15", None, 1, "ECONOMY", "USD",
             old_time, "duffel", json.dumps(SAMPLE_RESULTS)),
        )
        conn.commit()
        conn.close()

        results, age = cache.get_cached(
            "LAX", "JFK", "2026-05-15", None, "ECONOMY", "USD",
            ignore_ttl=True,
        )
        assert results is not None
        assert age > 240  # older than 4 hours


class TestCacheHistory:
    """f) --history: returns all cached rows sorted by date."""

    def test_history_returns_all(self, cache: PriceCache):
        for i in range(3):
            ts = (
                datetime.now(timezone.utc) - timedelta(hours=10 - i)
            ).isoformat()
            conn = cache._get_connection()
            conn.execute(
                """INSERT INTO price_cache
                (origin,destination,departure_date,return_date,passengers,
                 cabin,currency,fetched_at,provider,response_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                ("LAX", "JFK", "2026-05-15", None, 1, "ECONOMY", "USD",
                 ts, "duffel",
                 json.dumps([{"offer_id": f"off_{i}", "total_price": 200 + i * 50}])),
            )
            conn.commit()
            conn.close()

        history = cache.get_history(
            "LAX", "JFK", "2026-05-15", None, "ECONOMY", "USD",
        )
        assert len(history) == 3
        # Sorted ascending by fetched_at
        assert history[0]["fetched_at"] < history[1]["fetched_at"]
        assert history[1]["fetched_at"] < history[2]["fetched_at"]
        assert history[0]["cheapest_price"] == 200
        assert history[2]["cheapest_price"] == 300


class TestConcurrentWrites:
    """q) Concurrent cache writes: two simultaneous writes don't corrupt."""

    def test_concurrent_writes(self, tmp_path):
        db_path = str(tmp_path / "concurrent.db")
        errors: list[Exception] = []

        def write_entry(idx: int) -> None:
            try:
                c = PriceCache(db_path=db_path)
                c.write(
                    origin="LAX", destination="JFK",
                    departure_date="2026-05-15", return_date=None,
                    passengers=1, cabin="ECONOMY", currency="USD",
                    provider="duffel",
                    results=[{"offer_id": f"off_{idx}", "total_price": 100 + idx}],
                )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=write_entry, args=(i,))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors during concurrent writes: {errors}"

        c = PriceCache(db_path=db_path)
        history = c.get_history(
            "LAX", "JFK", "2026-05-15", None, "ECONOMY", "USD",
        )
        assert len(history) == 10
