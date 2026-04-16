"""Travelpayouts API for aggregate price-trend context."""

from __future__ import annotations

import logging
import os
import statistics

import requests

from models import PriceContext
from providers.base import PriceContextProvider

logger = logging.getLogger(__name__)

TRAVELPAYOUTS_URL = (
    "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
)


class TravelpayoutsProvider(PriceContextProvider):
    """Travelpayouts implementation — returns aggregate price context.

    This provider does NOT return bookable offers. It returns statistical
    price data derived from historical aggregate search data, useful for
    answering "is this price typical?"
    """

    def __init__(self) -> None:
        self.token = os.environ.get("TRAVELPAYOUTS_TOKEN")

    def get_price_context(
        self,
        origin: str,
        destination: str,
        departure_date: str,
    ) -> PriceContext | None:
        """Get aggregate price context for a route and month.

        Args:
            origin: IATA airport code for departure.
            destination: IATA airport code for arrival.
            departure_date: Departure date YYYY-MM-DD (month is used).

        Returns:
            PriceContext with min/median/max or None if unavailable.
        """
        if not self.token:
            logger.debug(
                "TRAVELPAYOUTS_TOKEN not set — skipping price context"
            )
            return None

        departure_month = departure_date[:7]  # YYYY-MM

        try:
            resp = requests.get(
                TRAVELPAYOUTS_URL,
                params={
                    "origin": origin,
                    "destination": destination,
                    "departure_at": departure_month,
                    "token": self.token,
                    "sorting": "price",
                    "limit": 30,
                },
                timeout=10,
            )
            resp.raise_for_status()
            body = resp.json()

            if not body.get("success") or not body.get("data"):
                logger.debug(
                    "No Travelpayouts data for %s->%s", origin, destination
                )
                return None

            prices = sorted(
                entry["price"]
                for entry in body["data"]
                if isinstance(entry.get("price"), (int, float))
            )
            if not prices:
                return None

            return PriceContext(
                source="travelpayouts_aggregate",
                typical_minimum=float(min(prices)),
                typical_median=round(statistics.median(prices), 2),
                typical_maximum=float(max(prices)),
                current_assessment="unknown",
            )

        except Exception as exc:
            logger.warning("Travelpayouts API error: %s", exc)
            return None
