#!/usr/bin/env python3
"""Flight pricing lookup tool for OpenClaw agents.

Fetches current flight prices from Duffel API and maintains a local
SQLite cache for historical price tracking. Optionally enriches results
with aggregate price context from Travelpayouts.

Usage:
    python flight_pricing.py --origin LAX --destination JFK --date 2026-05-15
    python flight_pricing.py --origin LAX --destination JFK --date 2026-05-15 --history
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from cache import PriceCache
from models import (
    ErrorResponse,
    PriceContext,
    SearchMeta,
    SearchQuery,
    SearchResponse,
)
from providers.base import AuthenticationError, ProviderError
from providers.duffel import DuffelProvider
from providers.travelpayouts import TravelpayoutsProvider

# ---------------------------------------------------------------------------
# .env loading (optional — only if python-dotenv is installed)
# ---------------------------------------------------------------------------
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        pass

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_log_path = Path(__file__).parent / "flight_pricing.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_log_path),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def validate_iata_code(code: str) -> str | None:
    """Return the upper-cased code if it is exactly 3 letters, else None.

    Args:
        code: Raw user input.

    Returns:
        Normalized IATA code or None.
    """
    code = code.strip().upper()
    return code if re.match(r"^[A-Z]{3}$", code) else None


def build_error(code: str, message: str, query_dict: dict | None = None) -> str:
    """Serialize an ErrorResponse to a JSON string.

    Args:
        code: Machine-readable error code (SCREAMING_SNAKE_CASE).
        message: Human-readable explanation.
        query_dict: Optional echo of the query parameters.

    Returns:
        Pretty-printed JSON string.
    """
    return json.dumps(
        ErrorResponse(code=code, message=message, query=query_dict).model_dump(),
        indent=2,
    )


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Parsed Namespace.
    """
    p = argparse.ArgumentParser(
        description="Flight pricing lookup for OpenClaw agents",
    )
    p.add_argument("--origin", required=True, help="Origin IATA code (e.g. LAX)")
    p.add_argument("--destination", required=True, help="Destination IATA code (e.g. JFK)")
    p.add_argument("--date", required=True, help="Departure date YYYY-MM-DD")
    p.add_argument("--return-date", default=None, help="Return date YYYY-MM-DD")
    p.add_argument("--passengers", type=int, default=1, help="Passengers (1-9, default 1)")
    p.add_argument(
        "--cabin", default="ECONOMY",
        choices=["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"],
        help="Cabin class (default ECONOMY)",
    )
    p.add_argument("--non-stop", action="store_true", help="Non-stop flights only")
    p.add_argument("--max-results", type=int, default=10, help="Max results (default 10)")
    p.add_argument(
        "--sort-by", default="price",
        choices=["price", "duration", "departure_time"],
        help="Sort order (default price)",
    )
    p.add_argument("--currency", default="USD", help="ISO 4217 currency (default USD)")
    p.add_argument("--force-refresh", action="store_true", help="Bypass cache")
    p.add_argument("--history", action="store_true", help="Show cached price history")
    p.add_argument("--cache-db", default=None, help="Override cache DB path (testing)")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_inputs(args: argparse.Namespace) -> str | None:
    """Validate parsed CLI args.

    Args:
        args: Parsed Namespace (modified in-place to normalize codes).

    Returns:
        JSON error string if invalid, else None.
    """
    query_dict = {
        "origin": args.origin,
        "destination": args.destination,
        "departure_date": args.date,
        "return_date": args.return_date,
    }

    origin = validate_iata_code(args.origin)
    if not origin:
        return build_error(
            "INVALID_AIRPORT_CODE",
            f"{args.origin} is not a valid IATA airport code.",
            query_dict,
        )
    args.origin = origin

    destination = validate_iata_code(args.destination)
    if not destination:
        return build_error(
            "INVALID_AIRPORT_CODE",
            f"{args.destination} is not a valid IATA airport code.",
            query_dict,
        )
    args.destination = destination

    # Departure date format
    try:
        dep_date = date.fromisoformat(args.date)
    except ValueError:
        return build_error(
            "INVALID_DATE_FORMAT",
            f"{args.date} is not a valid date. Use YYYY-MM-DD.",
            query_dict,
        )

    # Past-date check (only for live queries, not --history)
    if not args.history and dep_date < date.today():
        return build_error(
            "PAST_DATE_ERROR",
            f"Departure date {args.date} is in the past.",
            query_dict,
        )

    # Return date
    if args.return_date:
        try:
            ret_date = date.fromisoformat(args.return_date)
        except ValueError:
            return build_error(
                "INVALID_DATE_FORMAT",
                f"{args.return_date} is not a valid date. Use YYYY-MM-DD.",
                query_dict,
            )
        if ret_date <= dep_date:
            return build_error(
                "INVALID_DATE_RANGE",
                f"Return date {args.return_date} must be after "
                f"departure date {args.date}.",
                query_dict,
            )

    # Passengers
    if not 1 <= args.passengers <= 9:
        return build_error(
            "INVALID_PASSENGERS",
            f"Passengers must be 1-9, got {args.passengers}.",
            query_dict,
        )

    return None


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def sort_results(results: list[dict], sort_by: str) -> list[dict]:
    """Sort a list of offer dicts.

    Args:
        results: Offer dicts.
        sort_by: 'price', 'duration', or 'departure_time'.

    Returns:
        New sorted list.
    """
    if sort_by == "price":
        key = lambda r: r.get("total_price", float("inf"))
    elif sort_by == "duration":
        key = lambda r: (r.get("outbound") or {}).get(
            "duration_minutes", float("inf")
        )
    elif sort_by == "departure_time":
        key = lambda r: (r.get("outbound") or {}).get("departure_at", "")
    else:
        return results
    return sorted(results, key=key)


# ---------------------------------------------------------------------------
# Price assessment
# ---------------------------------------------------------------------------

def assess_price(cheapest: float, ctx: PriceContext) -> str:
    """Classify the cheapest offer relative to historical context.

    Args:
        cheapest: Cheapest offer price.
        ctx: PriceContext with min/median/max.

    Returns:
        One of 'below_median', 'median', 'above_median'.
    """
    if cheapest <= ctx.typical_median:
        return "below_median"
    if cheapest <= ctx.typical_median * 1.15:
        return "median"
    return "above_median"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 success, 1 error.
    """
    args = parse_args(argv)

    error = validate_inputs(args)
    if error:
        print(error)
        return 1

    now = datetime.now(timezone.utc).isoformat()
    query = SearchQuery(
        origin=args.origin,
        destination=args.destination,
        departure_date=args.date,
        return_date=args.return_date,
        passengers=args.passengers,
        cabin=args.cabin,
        non_stop=args.non_stop,
        currency=args.currency,
        queried_at=now,
    )
    query_dict = query.model_dump()
    cache = PriceCache(db_path=args.cache_db)

    # ── History mode ──────────────────────────────────────────────────
    if args.history:
        history = cache.get_history(
            args.origin, args.destination, args.date,
            args.return_date, args.cabin, args.currency,
        )
        output: dict = {
            "query": query_dict,
            "meta": {
                "cache_hit": True,
                "data_source": "cache",
                "cache_age_minutes": None,
                "total_results": len(history),
                "provider": "cache",
            },
            "history": history,
            "price_context": None,
        }
        if not history:
            output["message"] = (
                "No cached price history for this route. "
                "Run a live query first to start accumulating data."
            )
        print(json.dumps(output, indent=2))
        return 0

    # ── Cache check (skip if --force-refresh) ─────────────────────────
    if not args.force_refresh:
        cached, age = cache.get_cached(
            args.origin, args.destination, args.date,
            args.return_date, args.cabin, args.currency,
        )
        if cached is not None:
            logger.info(
                "Cache hit for %s->%s (%d min old)",
                args.origin, args.destination, age,
            )
            trimmed = sort_results(cached, args.sort_by)[: args.max_results]
            resp = SearchResponse(
                query=query,
                meta=SearchMeta(
                    cache_hit=True, data_source="cache",
                    cache_age_minutes=age,
                    total_results=len(trimmed), provider="cache",
                ),
                results=[],
            )
            out = resp.model_dump(by_alias=True)
            out["results"] = trimmed
            print(json.dumps(out, indent=2))
            return 0

    # ── Live fetch ────────────────────────────────────────────────────
    try:
        provider = DuffelProvider()
        offers = provider.search(
            origin=args.origin,
            destination=args.destination,
            departure_date=args.date,
            return_date=args.return_date,
            passengers=args.passengers,
            cabin_class=args.cabin,
            non_stop=args.non_stop,
            max_results=args.max_results,
        )
        results_dicts = [o.model_dump(by_alias=True) for o in offers]
        data_source = "duffel_live"
        provider_name = "duffel"

    except AuthenticationError as exc:
        logger.error("Authentication error: %s", exc)
        print(build_error("AUTHENTICATION_ERROR", str(exc), query_dict))
        return 1

    except ProviderError as exc:
        logger.warning("Provider error, trying cache fallback: %s", exc)
        cached, age = cache.get_cached(
            args.origin, args.destination, args.date,
            args.return_date, args.cabin, args.currency,
            ignore_ttl=True,
        )
        if cached is not None:
            trimmed = sort_results(cached, args.sort_by)[: args.max_results]
            resp = SearchResponse(
                query=query,
                meta=SearchMeta(
                    cache_hit=True, data_source="cache",
                    cache_age_minutes=age,
                    total_results=len(trimmed), provider="cache",
                ),
                results=[],
                message=f"Live API unavailable ({exc}). Showing cached data.",
            )
            out = resp.model_dump(by_alias=True)
            out["results"] = trimmed
            print(json.dumps(out, indent=2))
            return 0

        print(build_error(
            "API_UNAVAILABLE",
            f"Live API unavailable and no cached data exists: {exc}",
            query_dict,
        ))
        return 1

    # ── Cache the fresh results ───────────────────────────────────────
    cache.write(
        origin=args.origin, destination=args.destination,
        departure_date=args.date, return_date=args.return_date,
        passengers=args.passengers, cabin=args.cabin,
        currency=args.currency, provider=provider_name,
        results=results_dicts,
    )

    # ── Sort & limit ──────────────────────────────────────────────────
    sorted_results = sort_results(results_dicts, args.sort_by)[: args.max_results]

    # ── Price context (optional) ──────────────────────────────────────
    price_context: PriceContext | None = None
    try:
        tp = TravelpayoutsProvider()
        price_context = tp.get_price_context(
            args.origin, args.destination, args.date
        )
        if price_context and sorted_results:
            cheapest = sorted_results[0].get("total_price", 0)
            price_context.current_assessment = assess_price(
                cheapest, price_context
            )
    except Exception as exc:
        logger.warning("Price context failed: %s", exc)

    # ── Build & emit response ─────────────────────────────────────────
    resp = SearchResponse(
        query=query,
        meta=SearchMeta(
            cache_hit=False, data_source=data_source,
            cache_age_minutes=None,
            total_results=len(sorted_results), provider=provider_name,
        ),
        results=[],
        price_context=price_context,
    )
    out = resp.model_dump(by_alias=True)
    out["results"] = sorted_results

    if not sorted_results:
        out["message"] = (
            "No flights found for this route and date. "
            "Try different dates or remove the non-stop filter."
        )

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
