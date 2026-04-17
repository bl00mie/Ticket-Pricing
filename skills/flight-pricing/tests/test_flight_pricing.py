"""Comprehensive tests for the flight pricing CLI.

Covers all spec-required test cases (a–q).  Uses unittest.mock to
intercept HTTP calls so no live APIs are ever contacted.
"""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flight_pricing import (
    assess_price,
    main,
    parse_args,
    sort_results,
    validate_iata_code,
    validate_inputs,
)
from models import PriceContext

FIXTURES = Path(__file__).parent / "fixtures"
FUTURE = (date.today() + timedelta(days=30)).isoformat()
FUTURE_RET = (date.today() + timedelta(days=37)).isoformat()
DUFFEL_ENV = {"DUFFEL_ACCESS_TOKEN": "duffel_test_tok"}


# ── helpers ──────────────────────────────────────────────────────────

def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _duffel_ok(fixture: str = "duffel_response.json", status: int = 200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = _fixture(fixture)
    m.raise_for_status.return_value = None
    m.headers = {}
    return m


def _tp_ok(prices: list[int] | None = None):
    m = MagicMock()
    m.status_code = 200
    if prices:
        m.json.return_value = {
            "success": True,
            "data": [{"price": p} for p in prices],
        }
    else:
        m.json.return_value = {"success": True, "data": []}
    m.raise_for_status.return_value = None
    return m


def _run(args: list[str], db: str, env: dict | None = None) -> tuple[int, dict]:
    """Run main() and return (exit_code, parsed_json)."""
    full = list(args) + ["--cache-db", db]
    combined_env = {**DUFFEL_ENV, **(env or {})}
    with patch.dict(os.environ, combined_env, clear=False):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(full)
        return code, json.loads(buf.getvalue())


# ── a) One-way flight ────────────────────────────────────────────────

class TestOneWay:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post", return_value=_duffel_ok())
    def test_valid_one_way(self, mock_post, mock_get, tmp_path):
        code, data = _run(
            ["--origin", "LAX", "--destination", "JFK", "--date", FUTURE],
            str(tmp_path / "a.db"),
        )
        assert code == 0
        assert data["query"]["origin"] == "LAX"
        assert data["query"]["destination"] == "JFK"
        assert data["query"]["return_date"] is None
        assert data["meta"]["data_source"] == "duffel_live"
        assert data["meta"]["cache_hit"] is False
        assert len(data["results"]) > 0
        r0 = data["results"][0]
        for key in ("offer_id", "total_price", "price_per_person",
                     "currency", "cabin", "outbound", "fetched_at"):
            assert key in r0
        assert r0["return"] is None


# ── b) Round-trip flight ─────────────────────────────────────────────

class TestRoundTrip:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post")
    def test_round_trip_has_return(self, mock_post, mock_get, tmp_path):
        # Build a round-trip fixture by adding a return slice
        fix = _fixture("duffel_response.json")
        for offer in fix["data"]["offers"]:
            offer["slices"].append({
                "id": "sli_ret",
                "duration": "PT5H45M",
                "segments": [{
                    "origin": {"iata_code": "JFK"},
                    "destination": {"iata_code": "LAX"},
                    "departing_at": "2026-05-20T09:00:00",
                    "arriving_at": "2026-05-20T12:45:00",
                    "duration": "PT5H45M",
                    "marketing_carrier": {"iata_code": "UA", "name": "United Airlines"},
                    "marketing_carrier_flight_number": "456",
                    "aircraft": {"name": "Boeing 787-9"},
                }],
            })
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = fix
        resp.raise_for_status.return_value = None
        resp.headers = {}
        mock_post.return_value = resp

        code, data = _run(
            ["--origin", "LAX", "--destination", "JFK",
             "--date", FUTURE, "--return-date", FUTURE_RET],
            str(tmp_path / "b.db"),
        )
        assert code == 0
        r0 = data["results"][0]
        assert r0["return"] is not None
        assert r0["return"]["segments"][0]["origin"] == "JFK"
        assert r0["return"]["segments"][0]["destination"] == "LAX"

    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post")
    def test_off_route_offers_are_filtered_out(self, mock_post, mock_get, tmp_path):
        fix = _fixture("duffel_response.json")
        for offer in fix["data"]["offers"]:
            offer["slices"].append({
                "id": "sli_ret",
                "duration": "PT5H45M",
                "segments": [{
                    "origin": {"iata_code": "JFK"},
                    "destination": {"iata_code": "LAX"},
                    "departing_at": "2026-05-20T09:00:00",
                    "arriving_at": "2026-05-20T12:45:00",
                    "duration": "PT5H45M",
                    "marketing_carrier": {"iata_code": "UA", "name": "United Airlines"},
                    "marketing_carrier_flight_number": "456",
                    "aircraft": {"name": "Boeing 787-9"},
                }],
            })

        bad_offer = json.loads(json.dumps(fix["data"]["offers"][0]))
        bad_offer["id"] = "off_wrong_airport"
        bad_offer["slices"][0]["segments"][0]["destination"]["iata_code"] = "EWR"
        fix["data"]["offers"].insert(0, bad_offer)

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = fix
        resp.raise_for_status.return_value = None
        resp.headers = {}
        mock_post.return_value = resp

        code, data = _run(
            ["--origin", "LAX", "--destination", "JFK",
             "--date", FUTURE, "--return-date", FUTURE_RET],
            str(tmp_path / "b2.db"),
        )
        assert code == 0
        assert len(data["results"]) > 0
        assert all(
            r["outbound"]["segments"][-1]["destination"] == "JFK"
            for r in data["results"]
        )


# ── c) Cache hit ─────────────────────────────────────────────────────

class TestCacheHit:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post", return_value=_duffel_ok())
    def test_second_query_uses_cache(self, mock_post, mock_get, tmp_path):
        db = str(tmp_path / "c.db")
        args = ["--origin", "LAX", "--destination", "JFK", "--date", FUTURE]

        # First call — live
        code1, d1 = _run(args, db)
        assert code1 == 0
        assert d1["meta"]["cache_hit"] is False

        # Second call — should be cache hit (no new POST)
        mock_post.reset_mock()
        code2, d2 = _run(args, db)
        assert code2 == 0
        assert d2["meta"]["cache_hit"] is True
        assert d2["meta"]["data_source"] == "cache"
        mock_post.assert_not_called()


# ── d) Cache miss after TTL ──────────────────────────────────────────

class TestCacheMiss:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post", return_value=_duffel_ok())
    def test_expired_cache_fetches_live(self, mock_post, mock_get, tmp_path):
        db = str(tmp_path / "d.db")

        # Insert old cached data directly
        from cache import PriceCache
        c = PriceCache(db_path=db)
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        conn = c._get_connection()
        conn.execute(
            """INSERT INTO price_cache
            (origin,destination,departure_date,return_date,passengers,
             cabin,currency,fetched_at,provider,response_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("LAX", "JFK", FUTURE, None, 1, "ECONOMY", "USD",
             old, "duffel", json.dumps([{"offer_id": "old"}])),
        )
        conn.commit()
        conn.close()

        code, data = _run(
            ["--origin", "LAX", "--destination", "JFK", "--date", FUTURE],
            db,
        )
        assert code == 0
        assert data["meta"]["cache_hit"] is False
        assert data["meta"]["data_source"] == "duffel_live"


# ── e) --force-refresh ───────────────────────────────────────────────

class TestForceRefresh:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post", return_value=_duffel_ok())
    def test_force_refresh_bypasses_cache(self, mock_post, mock_get, tmp_path):
        db = str(tmp_path / "e.db")
        args = ["--origin", "LAX", "--destination", "JFK", "--date", FUTURE]
        _run(args, db)  # populate cache

        mock_post.reset_mock()
        code, data = _run(args + ["--force-refresh"], db)
        assert code == 0
        assert data["meta"]["cache_hit"] is False
        mock_post.assert_called_once()


# ── f) --history ─────────────────────────────────────────────────────

class TestHistory:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post", return_value=_duffel_ok())
    def test_history_returns_all_cached(self, mock_post, mock_get, tmp_path):
        db = str(tmp_path / "f.db")
        args = ["--origin", "LAX", "--destination", "JFK", "--date", FUTURE]

        # Populate cache twice (force-refresh to create two entries)
        _run(args, db)
        _run(args + ["--force-refresh"], db)

        mock_post.reset_mock()
        code, data = _run(args + ["--history"], db)
        assert code == 0
        assert "history" in data
        assert len(data["history"]) == 2
        assert data["history"][0]["fetched_at"] <= data["history"][1]["fetched_at"]
        mock_post.assert_not_called()

    def test_empty_history(self, tmp_path):
        db = str(tmp_path / "f2.db")
        code, data = _run(
            ["--origin", "LAX", "--destination", "JFK",
             "--date", FUTURE, "--history"],
            db,
        )
        assert code == 0
        assert data["history"] == []
        assert "message" in data


# ── g) Invalid IATA code ────────────────────────────────────────────

class TestInvalidIATA:
    def test_invalid_origin(self, tmp_path):
        code, data = _run(
            ["--origin", "LAX1", "--destination", "JFK", "--date", FUTURE],
            str(tmp_path / "g.db"),
        )
        assert code == 1
        assert data["error"] is True
        assert data["code"] == "INVALID_AIRPORT_CODE"

    def test_invalid_destination(self, tmp_path):
        code, data = _run(
            ["--origin", "LAX", "--destination", "J", "--date", FUTURE],
            str(tmp_path / "g2.db"),
        )
        assert code == 1
        assert data["code"] == "INVALID_AIRPORT_CODE"


# ── h) Past departure date ──────────────────────────────────────────

class TestPastDate:
    def test_past_date_error(self, tmp_path):
        past = (date.today() - timedelta(days=1)).isoformat()
        code, data = _run(
            ["--origin", "LAX", "--destination", "JFK", "--date", past],
            str(tmp_path / "h.db"),
        )
        assert code == 1
        assert data["code"] == "PAST_DATE_ERROR"


# ── i) Return date before departure ─────────────────────────────────

class TestInvalidDateRange:
    def test_return_before_departure(self, tmp_path):
        code, data = _run(
            ["--origin", "LAX", "--destination", "JFK",
             "--date", FUTURE, "--return-date", FUTURE],
            str(tmp_path / "i.db"),
        )
        assert code == 1
        assert data["code"] == "INVALID_DATE_RANGE"


# ── j) API error falls back to cache ────────────────────────────────

class TestAPIFallback:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post", return_value=_duffel_ok())
    def test_fallback_to_cache(self, mock_post, mock_get, tmp_path):
        db = str(tmp_path / "j.db")
        args = ["--origin", "LAX", "--destination", "JFK", "--date", FUTURE]
        _run(args, db)  # populate cache

        # Now make post raise
        import requests as req
        mock_post.side_effect = req.exceptions.ConnectionError("offline")

        code, data = _run(args + ["--force-refresh"], db)
        assert code == 0
        assert data["meta"]["cache_hit"] is True
        assert "unavailable" in data.get("message", "").lower() or \
               "cached" in data.get("message", "").lower()


# ── k) API error with no cache ──────────────────────────────────────

class TestAPIErrorNoCache:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post")
    def test_no_cache_returns_error(self, mock_post, mock_get, tmp_path):
        import requests as req
        mock_post.side_effect = req.exceptions.ConnectionError("offline")

        code, data = _run(
            ["--origin", "LAX", "--destination", "JFK", "--date", FUTURE],
            str(tmp_path / "k.db"),
        )
        assert code == 1
        assert data["error"] is True
        assert data["code"] == "API_UNAVAILABLE"


# ── l) API returns zero results ──────────────────────────────────────

class TestZeroResults:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post", return_value=_duffel_ok("empty_response.json"))
    def test_empty_results(self, mock_post, mock_get, tmp_path):
        code, data = _run(
            ["--origin", "LAX", "--destination", "JFK", "--date", FUTURE],
            str(tmp_path / "l.db"),
        )
        assert code == 0
        assert data["results"] == []
        assert "message" in data


# ── m) Non-stop filter ───────────────────────────────────────────────

class TestNonStop:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post", return_value=_duffel_ok())
    def test_non_stop_passes_param(self, mock_post, mock_get, tmp_path):
        _run(
            ["--origin", "LAX", "--destination", "JFK",
             "--date", FUTURE, "--non-stop"],
            str(tmp_path / "m.db"),
        )
        call_body = mock_post.call_args[1]["json"]
        assert call_body["data"].get("max_connections") == 0


# ── n) Cabin class ───────────────────────────────────────────────────

class TestCabinClass:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post", return_value=_duffel_ok())
    def test_business_cabin(self, mock_post, mock_get, tmp_path):
        _run(
            ["--origin", "LAX", "--destination", "JFK",
             "--date", FUTURE, "--cabin", "BUSINESS"],
            str(tmp_path / "n.db"),
        )
        call_body = mock_post.call_args[1]["json"]
        assert call_body["data"]["cabin_class"] == "business"


# ── o) Multi-passenger ──────────────────────────────────────────────

class TestMultiPassenger:
    @patch("requests.get", return_value=_tp_ok())
    @patch("requests.post", return_value=_duffel_ok())
    def test_three_passengers(self, mock_post, mock_get, tmp_path):
        code, data = _run(
            ["--origin", "LAX", "--destination", "JFK",
             "--date", FUTURE, "--passengers", "3"],
            str(tmp_path / "o.db"),
        )
        assert code == 0
        # Duffel body should have 3 passenger entries
        call_body = mock_post.call_args[1]["json"]
        assert len(call_body["data"]["passengers"]) == 3

        # price_per_person = total / 3
        for r in data["results"]:
            expected_pp = round(r["total_price"] / 3, 2)
            assert r["price_per_person"] == expected_pp


# ── Sorting ──────────────────────────────────────────────────────────

class TestSorting:
    def test_sort_by_price(self):
        items = [
            {"total_price": 500, "outbound": {"duration_minutes": 120}},
            {"total_price": 200, "outbound": {"duration_minutes": 300}},
            {"total_price": 350, "outbound": {"duration_minutes": 200}},
        ]
        s = sort_results(items, "price")
        assert [x["total_price"] for x in s] == [200, 350, 500]

    def test_sort_by_duration(self):
        items = [
            {"total_price": 200, "outbound": {"duration_minutes": 300}},
            {"total_price": 500, "outbound": {"duration_minutes": 120}},
        ]
        s = sort_results(items, "duration")
        assert s[0]["outbound"]["duration_minutes"] == 120


# ── Price assessment ─────────────────────────────────────────────────

class TestAssessPrice:
    def test_below_median(self):
        ctx = PriceContext(
            typical_minimum=100, typical_median=300, typical_maximum=600,
            current_assessment="unknown",
        )
        assert assess_price(200, ctx) == "below_median"

    def test_median_range(self):
        ctx = PriceContext(
            typical_minimum=100, typical_median=300, typical_maximum=600,
            current_assessment="unknown",
        )
        assert assess_price(320, ctx) == "median"

    def test_above_median(self):
        ctx = PriceContext(
            typical_minimum=100, typical_median=300, typical_maximum=600,
            current_assessment="unknown",
        )
        assert assess_price(500, ctx) == "above_median"


# ── IATA validation ─────────────────────────────────────────────────

class TestIATAValidation:
    @pytest.mark.parametrize("code,expected", [
        ("LAX", "LAX"), ("jfk", "JFK"), (" lax ", "LAX"),
        ("LA", None), ("LAXJ", None), ("LA1", None),
        ("", None), ("L", None), ("123", None),
    ])
    def test_iata(self, code, expected):
        assert validate_iata_code(code) == expected


# ── Authentication error ─────────────────────────────────────────────

class TestAuthError:
    def test_missing_token(self, tmp_path):
        """No DUFFEL_ACCESS_TOKEN → AUTHENTICATION_ERROR."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove the token if set
            os.environ.pop("DUFFEL_ACCESS_TOKEN", None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main([
                    "--origin", "LAX", "--destination", "JFK",
                    "--date", FUTURE,
                    "--cache-db", str(tmp_path / "auth.db"),
                ])
            data = json.loads(buf.getvalue())
            assert code == 1
            assert data["code"] == "AUTHENTICATION_ERROR"
