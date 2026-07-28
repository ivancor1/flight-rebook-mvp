"""Core data types.

Times are naive local wall-clock strings in ISO form ("2026-07-28T16:25"),
paired with the airport's timezone offset in minutes from UTC so we can
compare across time zones without pulling in a tz database.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Optional


def parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M")


@dataclass(frozen=True)
class Airport:
    code: str
    name: str
    metro: str
    utc_offset_minutes: int


@dataclass
class Segment:
    """One marketed flight leg with real, party-size-aware inventory."""
    carrier: str            # "AA"
    carrier_name: str       # "American"
    number: str             # "1642"
    origin: str             # "SJC"
    destination: str        # "PHX"
    depart_local: str
    arrive_local: str
    seats_available: int    # bookable seats in the cheapest available bucket
    fare_per_person: float  # USD, one-way, this leg
    aircraft: str = ""
    logo: str = ""          # carrier logo URL (from a live source; blank on fixtures)

    @property
    def flight(self) -> str:
        return f"{self.carrier}{self.number}"


@dataclass
class Option:
    """A candidate way to get there: one or two segments."""
    segments: List[Segment]
    ground_to_origin_minutes: int = 0
    ground_from_destination_minutes: int = 0
    notes: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    # filled in by the engine
    max_party_supported: int = 0
    rejected_reason: Optional[str] = None

    # --- identity -------------------------------------------------
    @property
    def origin(self) -> str:
        return self.segments[0].origin

    @property
    def destination(self) -> str:
        return self.segments[-1].destination

    @property
    def carriers(self) -> List[str]:
        out = []
        for s in self.segments:
            if s.carrier not in out:
                out.append(s.carrier)
        return out

    @property
    def is_self_connection(self) -> bool:
        return len(self.carriers) > 1

    # --- timing ---------------------------------------------------
    def depart(self, airports) -> datetime:
        s = self.segments[0]
        return parse(s.depart_local) - timedelta(minutes=airports[s.origin].utc_offset_minutes)

    def arrive(self, airports) -> datetime:
        s = self.segments[-1]
        return parse(s.arrive_local) - timedelta(minutes=airports[s.destination].utc_offset_minutes)

    def door_arrival(self, airports) -> datetime:
        return self.arrive(airports) + timedelta(minutes=self.ground_from_destination_minutes)

    def leave_now_by(self, airports) -> datetime:
        """UTC instant the traveller must leave their current location."""
        return self.depart(airports) - timedelta(minutes=self.ground_to_origin_minutes + 60)

    def leave_by_local(self, airports) -> str:
        """Wall-clock time at the origin airport's timezone to walk out the door."""
        ap = airports[self.origin]
        local = self.leave_now_by(airports) + timedelta(minutes=ap.utc_offset_minutes)
        return local.strftime("%Y-%m-%dT%H:%M")

    def door_to_door_minutes(self, airports) -> int:
        return int((self.door_arrival(airports) - self.leave_now_by(airports)).total_seconds() // 60)

    def layover_minutes(self, airports) -> Optional[int]:
        if len(self.segments) < 2:
            return None
        a, b = self.segments[0], self.segments[1]
        arr = parse(a.arrive_local) - timedelta(minutes=airports[a.destination].utc_offset_minutes)
        dep = parse(b.depart_local) - timedelta(minutes=airports[b.origin].utc_offset_minutes)
        return int((dep - arr).total_seconds() // 60)

    def price_per_person(self) -> float:
        return round(sum(s.fare_per_person for s in self.segments), 2)

    def to_dict(self, airports):
        return {
            "id": "-".join(s.flight for s in self.segments),
            "segments": [asdict(s) for s in self.segments],
            "origin": self.origin,
            "destination": self.destination,
            "carriers": self.carriers,
            "self_connection": self.is_self_connection,
            "depart_local": self.segments[0].depart_local,
            "arrive_local": self.segments[-1].arrive_local,
            "arrive_utc": self.arrive(airports).isoformat(),
            "door_arrival_utc": self.door_arrival(airports).isoformat(),
            "leave_by_utc": self.leave_now_by(airports).isoformat(),
            "leave_by_local": self.leave_by_local(airports),
            "door_to_door_minutes": self.door_to_door_minutes(airports),
            "layover_minutes": self.layover_minutes(airports),
            "ground_to_origin_minutes": self.ground_to_origin_minutes,
            "ground_from_destination_minutes": self.ground_from_destination_minutes,
            "price_per_person": self.price_per_person(),
            "max_party_supported": self.max_party_supported,
            "notes": self.notes,
            "tags": self.tags,
            "rejected_reason": self.rejected_reason,
        }


@dataclass
class Passenger:
    name: str
    pnr: str            # record locator - the party may be split across several
    fare_paid: float    # what this passenger paid for the cancelled itinerary
    ticket_refundable_basis: str = "cancelled_by_carrier"


@dataclass
class Disruption:
    """The thing that went wrong, plus what the airline offered instead."""
    original_flight: str
    original_origin: str
    original_destination: str
    original_depart_local: str
    original_arrive_local: str
    cause: str                      # "weather", "mechanical", ...
    itinerary_type: str             # "domestic" | "international"
    passengers: List[Passenger]
    airline_offer: Optional[Option] = None
    # What actually happened, as stated by the airline: "cancelled", "delayed",
    # "changed", or "" when the message never said. Empty means unknown - the
    # refund rules key off a real cancellation, so we never assume one.
    disruption_type: str = ""
    # Connection points on the itinerary they actually bought: 0 for a nonstop,
    # 1 for one stop, None when nobody told us. None means unknown, not zero -
    # "the replacement adds a connection" is only true if we know what the
    # original had.
    original_connections: Optional[int] = None

    @property
    def is_cancellation(self) -> bool:
        return self.disruption_type == "cancelled"

    @property
    def fare_known(self) -> bool:
        """False when nobody told us what the ticket cost - a zero total is
        'we don't know', not 'you paid nothing'."""
        return any(p.fare_paid for p in self.passengers)

    @property
    def party_size(self) -> int:
        return len(self.passengers)

    @property
    def pnrs(self) -> List[str]:
        out = []
        for p in self.passengers:
            if p.pnr not in out:
                out.append(p.pnr)
        return out

    @property
    def total_paid(self) -> float:
        return round(sum(p.fare_paid for p in self.passengers), 2)
