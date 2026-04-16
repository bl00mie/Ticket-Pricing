"""SQLite cache for flight pricing data with WAL mode and TTL support."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.expanduser(
    "~/.openclaw/workspace/skills/flight-pricing/cache.db"
)
CACHE_TTL_HOURS = 4

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS price_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    return_date TEXT,
    passengers INTEGER NOT NULL,
    cabin TEXT NOT NULL,
    currency TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    response_json TEXT NOT NULL
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_route_date
ON price_cache(origin, destination, departure_date, return_date, cabin, currency);
"""


class PriceCache:
    """SQLite-based cache for flight pricing queries.

    Uses WAL journal mode for safe concurrent reads across multiple
    agent sessions.
    """

    def __init__(self, db_path: str | None = None):
        """Initialize the cache, creating the DB file and tables if needed.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with WAL mode enabled.

        Returns:
            sqlite3.Connection with WAL mode and Row factory.
        """
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables and indexes if they don't exist."""
        try:
            conn = self._get_connection()
            conn.execute(CREATE_TABLE_SQL)
            conn.execute(CREATE_INDEX_SQL)
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            logger.warning("Failed to initialize cache database: %s", e)

    def get_cached(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None,
        cabin: str,
        currency: str,
        ignore_ttl: bool = False,
    ) -> tuple[list[dict] | None, int | None]:
        """Get the newest cached results for a route.

        Args:
            origin: Origin IATA code.
            destination: Destination IATA code.
            departure_date: Departure date string.
            return_date: Return date string or None.
            cabin: Cabin class string.
            currency: Currency code string.
            ignore_ttl: If True, return results regardless of age.

        Returns:
            Tuple of (results_list, cache_age_minutes) or (None, None).
        """
        try:
            conn = self._get_connection()
            cursor = conn.execute(
                """
                SELECT response_json, fetched_at
                FROM price_cache
                WHERE origin = ? AND destination = ? AND departure_date = ?
                    AND return_date IS ? AND cabin = ? AND currency = ?
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                (origin, destination, departure_date, return_date, cabin, currency),
            )
            row = cursor.fetchone()
            conn.close()

            if not row:
                return None, None

            fetched_at = datetime.fromisoformat(row["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            age_minutes = int((now - fetched_at).total_seconds() / 60)

            if not ignore_ttl and age_minutes > CACHE_TTL_HOURS * 60:
                return None, None

            results = json.loads(row["response_json"])
            return results, age_minutes

        except (sqlite3.Error, json.JSONDecodeError, ValueError) as e:
            logger.warning("Cache read error: %s", e)
            return None, None

    def get_history(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None,
        cabin: str,
        currency: str,
    ) -> list[dict]:
        """Get all cached results for a route, sorted chronologically.

        Args:
            origin: Origin IATA code.
            destination: Destination IATA code.
            departure_date: Departure date string.
            return_date: Return date string or None.
            cabin: Cabin class string.
            currency: Currency code string.

        Returns:
            List of history entry dicts with fetched_at, provider,
            num_offers, cheapest_price, and results.
        """
        try:
            conn = self._get_connection()
            cursor = conn.execute(
                """
                SELECT fetched_at, provider, response_json
                FROM price_cache
                WHERE origin = ? AND destination = ? AND departure_date = ?
                    AND return_date IS ? AND cabin = ? AND currency = ?
                ORDER BY fetched_at ASC
                """,
                (origin, destination, departure_date, return_date, cabin, currency),
            )
            rows = cursor.fetchall()
            conn.close()

            history = []
            for row in rows:
                results = json.loads(row["response_json"])
                prices = [
                    r.get("total_price", float("inf"))
                    for r in results
                    if isinstance(r.get("total_price"), (int, float))
                ]
                cheapest = min(prices) if prices else None
                history.append(
                    {
                        "fetched_at": row["fetched_at"],
                        "provider": row["provider"],
                        "num_offers": len(results),
                        "cheapest_price": cheapest,
                        "results": results,
                    }
                )
            return history

        except (sqlite3.Error, json.JSONDecodeError) as e:
            logger.warning("Cache history read error: %s", e)
            return []

    def write(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None,
        passengers: int,
        cabin: str,
        currency: str,
        provider: str,
        results: list[dict],
    ) -> None:
        """Write search results to cache.

        Args:
            origin: Origin IATA code.
            destination: Destination IATA code.
            departure_date: Departure date string.
            return_date: Return date string or None.
            passengers: Number of passengers.
            cabin: Cabin class string.
            currency: Currency code string.
            provider: Provider name string.
            results: List of offer dicts to cache.
        """
        try:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT INTO price_cache
                (origin, destination, departure_date, return_date, passengers,
                 cabin, currency, fetched_at, provider, response_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    origin,
                    destination,
                    departure_date,
                    return_date,
                    passengers,
                    cabin,
                    currency,
                    datetime.now(timezone.utc).isoformat(),
                    provider,
                    json.dumps(results),
                ),
            )
            conn.commit()
            conn.close()
            logger.info(
                "Cached %d results for %s->%s", len(results), origin, destination
            )
        except sqlite3.Error as e:
            logger.warning("Cache write error: %s", e)
