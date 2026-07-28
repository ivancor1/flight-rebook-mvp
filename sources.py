"""Where flight data comes from.

The engine never talks to an API directly - it asks a FlightSource. That is
the seam where a real inventory feed replaces the fixtures, and it is also
where the honest limits of this MVP live (see README, "The data problem").
"""
import json
import os
from typing import Dict, List

from models import Airport, Segment, Passenger, Disruption, Option

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_PATH = os.path.join(HERE, "fixtures.json")


class FlightSource:
    """Interface. Implement these four methods against a real feed."""

    name = "abstract"
    coverage_note = ""

    def airports(self) -> Dict[str, Airport]:
        raise NotImplementedError

    def metros(self) -> Dict[str, dict]:
        raise NotImplementedError

    def segments(self) -> List[Segment]:
        """Every marketed leg we can see, with per-leg bookable seat counts."""
        raise NotImplementedError

    def ground_minutes(self) -> dict:
        raise NotImplementedError


class FixtureSource(FlightSource):
    """Seeded dataset. Deterministic, offline, no API key, no network."""

    name = "fixtures"
    coverage_note = (
        "Seeded fixture data for 2026-07-28 Bay Area -> New York. Real flight "
        "numbers and plausible times, but the seat counts and fares are made up. "
        "Nothing here is live availability."
    )

    def __init__(self, path: str = FIXTURE_PATH):
        with open(path) as fh:
            self.raw = json.load(fh)

    def airports(self):
        return {a["code"]: Airport(**a) for a in self.raw["airports"]}

    def metros(self):
        return self.raw["metros"]

    def carriers(self):
        return self.raw["carriers"]

    def segments(self):
        names = self.raw["carriers"]
        return [
            Segment(
                carrier=s["carrier"],
                carrier_name=names.get(s["carrier"], s["carrier"]),
                number=s["number"],
                origin=s["origin"],
                destination=s["destination"],
                depart_local=s["depart_local"],
                arrive_local=s["arrive_local"],
                seats_available=s["seats_available"],
                fare_per_person=s["fare_per_person"],
                aircraft=s.get("aircraft", ""),
            )
            for s in self.raw["segments"]
        ]

    def ground_minutes(self):
        return self.raw["ground_minutes"]

    # --- demo scenario -------------------------------------------
    def disruption(self) -> Disruption:
        d = self.raw["disruption"]
        by_flight = {s.flight: s for s in self.segments()}
        offer = Option(segments=[by_flight[f] for f in d["airline_offer"]])
        offer.tags = ["airline_offer"]
        offer.notes = ["What the airline put you on without asking."]
        return Disruption(
            original_flight=d["original_flight"],
            original_origin=d["original_origin"],
            original_destination=d["original_destination"],
            original_depart_local=d["original_depart_local"],
            original_arrive_local=d["original_arrive_local"],
            cause=d["cause"],
            itinerary_type=d["itinerary_type"],
            passengers=[Passenger(**p) for p in d["passengers"]],
            airline_offer=offer,
        )


class LiveSource(FlightSource):
    """Not implemented on purpose.

    Real per-fare-class seat availability is the hard part of this product.
    The options, roughly, with what they actually cost you:

      * GDS / NDC (Amadeus, Sabre, Travelport, or airline NDC APIs) - the only
        way to see true bookable inventory at a given party size. Needs a
        commercial agreement; not self-serve.
      * Aggregator APIs (Duffel, Kiwi, Amadeus Self-Service) - self-serve and
        good enough to book, but coverage is partial and seat counts are often
        capped at "9" rather than the truth.
      * Schedule-only feeds (OAG, Cirium) - tell you what flies, never whether
        four seats exist.
      * Scraping airline sites - fragile, usually against terms of service.

    Instinct's own flight index (`tools flights`) covers American but not
    United, Delta, JetBlue or Southwest, which is exactly why this MVP ships on
    fixtures instead of pretending to be live.
    """

    name = "live"

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "No live inventory provider is wired up. Run on fixtures, or "
            "implement FlightSource against a provider you have credentials for."
        )
