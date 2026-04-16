"""Pydantic data models for flight pricing tool inputs and outputs."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FlightSegment(BaseModel):
    """A single flight segment (one takeoff-to-landing)."""

    flight_number: str
    carrier_iata: str
    carrier_name: str
    origin: str
    destination: str
    departure_at: str
    arrival_at: str
    aircraft: Optional[str] = None


class FlightLeg(BaseModel):
    """A journey leg (outbound or return), containing one or more segments."""

    departure_at: str
    arrival_at: str
    duration_minutes: int
    stops: int
    segments: list[FlightSegment]


class FlightOffer(BaseModel):
    """A single flight offer returned by a provider."""

    offer_id: str
    total_price: float
    price_per_person: float
    currency: str
    cabin: str
    outbound: FlightLeg
    return_leg: Optional[FlightLeg] = Field(
        default=None, serialization_alias="return"
    )
    baggage_included: bool
    refundable: bool
    expires_at: Optional[str] = None
    fetched_at: str

    model_config = {"populate_by_name": True}


class SearchQuery(BaseModel):
    """Query parameters echoed back in the response."""

    origin: str
    destination: str
    departure_date: str
    return_date: Optional[str] = None
    passengers: int = 1
    cabin: str = "ECONOMY"
    non_stop: bool = False
    currency: str = "USD"
    queried_at: str


class SearchMeta(BaseModel):
    """Metadata about the search results."""

    cache_hit: bool
    data_source: str
    cache_age_minutes: Optional[int] = None
    total_results: int
    provider: str


class PriceContext(BaseModel):
    """Price context from Travelpayouts aggregate data."""

    source: str = "travelpayouts_aggregate"
    typical_minimum: float
    typical_median: float
    typical_maximum: float
    current_assessment: str  # below_median | median | above_median | unknown


class SearchResponse(BaseModel):
    """Full search response."""

    query: SearchQuery
    meta: SearchMeta
    results: list[FlightOffer]
    price_context: Optional[PriceContext] = None
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    """Error response."""

    error: bool = True
    code: str
    message: str
    query: Optional[dict] = None
