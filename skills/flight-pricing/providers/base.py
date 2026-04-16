"""Abstract base classes for flight search providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from models import FlightOffer, PriceContext


class ProviderError(Exception):
    """Non-recoverable error from a flight search provider."""


class AuthenticationError(ProviderError):
    """API credentials are missing or invalid."""


class FlightSearchProvider(ABC):
    """Base class for live flight search providers."""

    @abstractmethod
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
        """Search for flight offers.

        Args:
            origin: IATA airport code for departure.
            destination: IATA airport code for arrival.
            departure_date: Departure date YYYY-MM-DD.
            return_date: Return date YYYY-MM-DD or None for one-way.
            passengers: Number of adult passengers (1-9).
            cabin_class: ECONOMY | PREMIUM_ECONOMY | BUSINESS | FIRST.
            non_stop: If True, only return direct flights.
            max_results: Maximum number of results to return.

        Returns:
            List of FlightOffer objects.

        Raises:
            AuthenticationError: If API credentials are invalid.
            ProviderError: If the API returns a non-recoverable error.
        """
        ...


class PriceContextProvider(ABC):
    """Base class for supplemental price-context providers."""

    @abstractmethod
    def get_price_context(
        self,
        origin: str,
        destination: str,
        departure_date: str,
    ) -> PriceContext | None:
        """Get aggregate price context for a route.

        Args:
            origin: IATA airport code for departure.
            destination: IATA airport code for arrival.
            departure_date: Departure date YYYY-MM-DD.

        Returns:
            PriceContext or None if data is unavailable.
        """
        ...
