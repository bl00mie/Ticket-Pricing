"""Duffel API flight search provider."""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone

import requests

from models import FlightLeg, FlightOffer, FlightSegment
from providers.base import AuthenticationError, FlightSearchProvider, ProviderError

logger = logging.getLogger(__name__)

DUFFEL_API_URL = "https://api.duffel.com"
DUFFEL_VERSION = "v2"

CABIN_MAP = {
    "ECONOMY": "economy",
    "PREMIUM_ECONOMY": "premium_economy",
    "BUSINESS": "business",
    "FIRST": "first",
}


class DuffelProvider(FlightSearchProvider):
    """Duffel API implementation for live flight searches."""

    def __init__(self) -> None:
        self.access_token = os.environ.get("DUFFEL_ACCESS_TOKEN")
        if not self.access_token:
            raise AuthenticationError(
                "DUFFEL_ACCESS_TOKEN environment variable is not set. "
                "Get your token at https://app.duffel.com/tokens"
            )

    def _headers(self) -> dict:
        """Build request headers.

        Returns:
            Dict of HTTP headers.
        """
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Duffel-Version": DUFFEL_VERSION,
        }

    @staticmethod
    def _parse_iso8601_duration(duration: str | None) -> int:
        """Parse an ISO 8601 duration string to total minutes.

        Args:
            duration: String like 'PT5H30M', 'PT2H', or 'PT45M'.

        Returns:
            Duration in minutes.
        """
        if not duration:
            return 0
        match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", duration)
        if not match:
            return 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        return hours * 60 + minutes

    def _build_request_body(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None,
        passengers: int,
        cabin_class: str,
        non_stop: bool,
    ) -> dict:
        """Build the Duffel offer-request payload.

        Args:
            origin: Origin IATA code.
            destination: Destination IATA code.
            departure_date: Departure date YYYY-MM-DD.
            return_date: Return date YYYY-MM-DD or None.
            passengers: Number of adult passengers.
            cabin_class: Cabin class key (e.g. ECONOMY).
            non_stop: If True, restrict to direct flights.

        Returns:
            Dict ready for JSON serialization.
        """
        slices = [
            {"origin": origin, "destination": destination,
             "departure_date": departure_date}
        ]
        if return_date:
            slices.append(
                {"origin": destination, "destination": origin,
                 "departure_date": return_date}
            )

        body: dict = {
            "data": {
                "slices": slices,
                "passengers": [{"type": "adult"} for _ in range(passengers)],
                "cabin_class": CABIN_MAP.get(cabin_class, "economy"),
            }
        }
        if non_stop:
            body["data"]["max_connections"] = 0
        return body

    def _parse_slice(self, slice_data: dict) -> FlightLeg:
        """Parse a Duffel slice into a FlightLeg.

        Args:
            slice_data: A single slice dict from the Duffel response.

        Returns:
            FlightLeg model instance.
        """
        segments: list[FlightSegment] = []
        for seg in slice_data.get("segments", []):
            origin_info = seg.get("origin", {})
            dest_info = seg.get("destination", {})
            carrier = seg.get("marketing_carrier", {})
            aircraft = seg.get("aircraft", {})

            iata = carrier.get("iata_code", "")
            flight_num = seg.get("marketing_carrier_flight_number", "")
            segments.append(
                FlightSegment(
                    flight_number=f"{iata}{flight_num}",
                    carrier_iata=iata,
                    carrier_name=carrier.get("name", ""),
                    origin=origin_info.get("iata_code", ""),
                    destination=dest_info.get("iata_code", ""),
                    departure_at=seg.get("departing_at", ""),
                    arrival_at=seg.get("arriving_at", ""),
                    aircraft=aircraft.get("name") if aircraft else None,
                )
            )

        # Duration: prefer slice-level, else compute from segment span
        duration_str = slice_data.get("duration")
        if duration_str:
            duration_minutes = self._parse_iso8601_duration(duration_str)
        elif len(segments) >= 2:
            try:
                dep = datetime.fromisoformat(segments[0].departure_at)
                arr = datetime.fromisoformat(segments[-1].arrival_at)
                duration_minutes = int((arr - dep).total_seconds() / 60)
            except (ValueError, IndexError):
                duration_minutes = 0
        elif segments:
            seg0 = slice_data["segments"][0]
            duration_minutes = self._parse_iso8601_duration(
                seg0.get("duration")
            )
        else:
            duration_minutes = 0

        stops = max(0, len(segments) - 1)
        return FlightLeg(
            departure_at=segments[0].departure_at if segments else "",
            arrival_at=segments[-1].arrival_at if segments else "",
            duration_minutes=duration_minutes,
            stops=stops,
            segments=segments,
        )

    def _offer_matches_route(
        self,
        offer: dict,
        origin: str,
        destination: str,
        return_date: str | None,
    ) -> bool:
        """Return True only if the offer slices match the requested route exactly."""
        slices = offer.get("slices", [])
        if not slices:
            return False

        outbound_segments = slices[0].get("segments", [])
        if not outbound_segments:
            return False
        if outbound_segments[0].get("origin", {}).get("iata_code") != origin:
            return False
        if outbound_segments[-1].get("destination", {}).get("iata_code") != destination:
            return False

        if return_date:
            if len(slices) < 2:
                return False
            return_segments = slices[1].get("segments", [])
            if not return_segments:
                return False
            if return_segments[0].get("origin", {}).get("iata_code") != destination:
                return False
            if return_segments[-1].get("destination", {}).get("iata_code") != origin:
                return False

        return True

    def _parse_offer(
        self, offer: dict, passengers: int, cabin_class: str
    ) -> FlightOffer:
        """Parse a Duffel offer dict into a FlightOffer model.

        Args:
            offer: A single offer dict from the Duffel response.
            passengers: Number of passengers for per-person calculation.
            cabin_class: Cabin class string to echo in the result.

        Returns:
            FlightOffer model instance.
        """
        now = datetime.now(timezone.utc).isoformat()
        total = float(offer.get("total_amount", 0))
        currency = offer.get("total_currency", "USD")
        per_person = round(total / max(passengers, 1), 2)

        slices = offer.get("slices", [])
        outbound = self._parse_slice(slices[0]) if slices else None
        return_leg = self._parse_slice(slices[1]) if len(slices) > 1 else None

        # Refundability
        conditions = offer.get("conditions", {})
        refund_info = conditions.get("refund_before_departure")
        refundable = (
            refund_info.get("allowed", False) if isinstance(refund_info, dict)
            else False
        )

        # Checked baggage included?
        baggage_included = False
        for pax in offer.get("passengers", []):
            for bag in pax.get("baggages", []):
                if bag.get("type") == "checked" and bag.get("quantity", 0) > 0:
                    baggage_included = True
                    break

        # Offer expiry
        payment_req = offer.get("payment_requirements", {})
        expires_at = payment_req.get("price_guarantee_expires_at") if payment_req else None
        if expires_at:
            try:
                exp_dt = datetime.fromisoformat(
                    expires_at.replace("Z", "+00:00")
                )
                if (exp_dt - datetime.now(timezone.utc)).total_seconds() < 1800:
                    logger.warning(
                        "Offer %s expires in <30 min (at %s)",
                        offer.get("id", "?"), expires_at,
                    )
            except (ValueError, TypeError):
                pass

        return FlightOffer(
            offer_id=offer.get("id", ""),
            total_price=total,
            price_per_person=per_person,
            currency=currency,
            cabin=cabin_class,
            outbound=outbound,
            return_leg=return_leg,
            baggage_included=baggage_included,
            refundable=refundable,
            expires_at=expires_at,
            fetched_at=now,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None,
        passengers: int,
        cabin_class: str,
        non_stop: bool,
        max_results: int,
    ) -> list[FlightOffer]:
        """Search Duffel for flight offers.

        Args:
            origin: Origin IATA code.
            destination: Destination IATA code.
            departure_date: Departure date YYYY-MM-DD.
            return_date: Return date YYYY-MM-DD or None.
            passengers: Number of passengers.
            cabin_class: Cabin class string.
            non_stop: Only non-stop flights.
            max_results: Max offers to return.

        Returns:
            List of FlightOffer models.

        Raises:
            AuthenticationError: On 401 from Duffel.
            ProviderError: On repeated failures.
        """
        body = self._build_request_body(
            origin, destination, departure_date, return_date,
            passengers, cabin_class, non_stop,
        )
        logger.debug("Duffel request body: %s", body)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{DUFFEL_API_URL}/air/offer_requests",
                    json=body,
                    headers=self._headers(),
                    params={"return_offers": "true"},
                    timeout=15,
                )

                if resp.status_code == 401:
                    raise AuthenticationError(
                        "Duffel API authentication failed. "
                        "Check your DUFFEL_ACCESS_TOKEN."
                    )

                if resp.status_code == 429:
                    retry_after = int(
                        resp.headers.get("Retry-After", 5)
                    )
                    logger.warning(
                        "Rate-limited by Duffel, waiting %ds", retry_after
                    )
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json().get("data", {})
                offers = data.get("offers", [])

                logger.info(
                    "Duffel returned %d offers for %s->%s on %s",
                    len(offers), origin, destination, departure_date,
                )

                results: list[FlightOffer] = []
                for raw in offers:
                    if not self._offer_matches_route(raw, origin, destination, return_date):
                        logger.warning(
                            "Skipping off-route offer %s for requested %s->%s",
                            raw.get("id", "?"), origin, destination,
                        )
                        continue
                    try:
                        results.append(
                            self._parse_offer(raw, passengers, cabin_class)
                        )
                    except Exception as exc:
                        logger.warning(
                            "Skipping unparseable offer %s: %s",
                            raw.get("id", "?"), exc,
                        )
                    if len(results) >= max_results:
                        break
                return results

            except AuthenticationError:
                raise
            except requests.exceptions.Timeout:
                last_error = ProviderError(
                    f"Duffel API timeout (attempt {attempt + 1}/3)"
                )
                logger.warning("Duffel timeout, attempt %d/3", attempt + 1)
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
            except requests.exceptions.RequestException as exc:
                last_error = ProviderError(f"Duffel API error: {exc}")
                logger.warning(
                    "Duffel request error, attempt %d/3: %s", attempt + 1, exc
                )
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))

        raise last_error or ProviderError("Duffel API failed after 3 attempts")
