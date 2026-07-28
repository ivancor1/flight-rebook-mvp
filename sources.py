"""Where flight data comes from.

The engine never talks to an API directly - it asks a FlightSource. That is
the seam where a real inventory feed replaces the fixtures, and it is also
where the honest limits of this MVP live (see README, "The data problem").
"""
import concurrent.futures as cf
import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
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
        "Seeded fixture data, Bay Area -> New York, date-shifted to the day you run "
        "it. Real flight numbers and plausible times, but the seat counts and fares "
        "are made up. Nothing here is live availability."
    )
    uses_demo_clock = True   # the scenario clock, not the wall clock, drives the demo

    def __init__(self, path: str = FIXTURE_PATH, today: date = None):
        with open(path) as fh:
            self.raw = json.load(fh)
        self.scenario_now_local = self.raw.get("scenario_now_local", "2026-07-28T11:00")
        # The fixtures were written for one specific day. Shift every timestamp by
        # whole days so the scenario always lands on the day the app is run -
        # otherwise the seeded flights are in the past and a search finds nothing.
        base = datetime.strptime(self.scenario_now_local[:10], "%Y-%m-%d").date()
        self.day_shift = timedelta(days=((today or date.today()) - base).days)

    def shift(self, ts: str) -> str:
        """Move a fixture wall-clock stamp onto the current day, keeping the time."""
        if not ts or not self.day_shift:
            return ts
        return (datetime.strptime(ts[:16], "%Y-%m-%dT%H:%M") + self.day_shift).strftime("%Y-%m-%dT%H:%M")

    def now_local(self) -> str:
        """The moment the scenario is frozen at, on today's date."""
        return self.shift(self.scenario_now_local)

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
                depart_local=self.shift(s["depart_local"]),
                arrive_local=self.shift(s["arrive_local"]),
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
            original_depart_local=self.shift(d["original_depart_local"]),
            original_arrive_local=self.shift(d["original_arrive_local"]),
            cause=d["cause"],
            disruption_type=d.get("disruption_type", "cancelled"),
            itinerary_type=d["itinerary_type"],
            passengers=[Passenger(**p) for p in d["passengers"]],
            airline_offer=offer,
        )


DUFFEL_URL = "https://api.duffel.com/air/offer_requests?return_offers=true"


class DuffelSource(FixtureSource):
    """Live, real, bookable inventory from Duffel - the drop-in for FixtureSource.

    Reference data (airports, timezones, metro groupings, ground-transfer times,
    and the demo cancellation) is inherited from the fixtures: it's config, not
    inventory. Only the flights themselves come from Duffel, live.

    How Duffel differs from a static feed, and how we bridge it:
      * It's a QUERY api. You ask for one route + date + the real passenger list
        and it returns whole priced itineraries. So we don't hand the engine a
        pile of segments to recombine; we hand it complete options Duffel already
        built and validated (see engine.search's `built_options` branch).
      * It fails closed on party size. An offer exists only if every leg can seat
        the whole party, so `seats_available` is set to the party size - the offer
        being here IS the guarantee. (This is why the "died on party size" bucket
        is empty on live data; surfacing those needs a second party-of-1 search.)
      * Search is free - money only moves when you create an Order, which this app
        never does. A live token doing offer requests costs nothing.
    """

    name = "duffel-live"
    uses_demo_clock = False  # live inventory follows the real wall clock
    coverage_note = (
        "Live Duffel inventory: real bookable offers, priced for your whole party, "
        "across the carriers Duffel covers. Southwest is not on any self-serve API "
        "and is not shown here - book it directly at southwest.com. Fares in USD."
    )

    def __init__(self, token: str, path: str = FIXTURE_PATH, cabin: str = "economy"):
        super().__init__(path)
        self.token = token.strip()
        self.cabin = cabin

    # --- interface the engine uses for a live source --------------
    def built_options(self, origins, dests, party, depart_after_utc, airports) -> Dict[str, List[List[Segment]]]:
        """Return live itineraries split into what the whole party can book vs. what
        "died on party size."

        The core trick (the single thing that makes this better than searching on
        your own phone at the gate): we search every route TWICE - once for the real
        party, once for a single passenger. Airlines sell seats in fare buckets, so a
        flight can show as available to a solo searcher and then vanish the instant
        you ask for 4 on one booking. Anything that comes back for 1 but not for the
        party is surfaced as a trap, not hidden.

        Honest caveat (stated in the UI too): this is live *purchase* inventory - the
        best proxy for what the airline's own rebooking tool would offer, not a read
        of your existing reservation, and not a guarantee.

        Returns {"viable": [...], "died": [...]}, each a list of segment-lists. The
        engine wraps these into Options; it does NOT recombine legs, because Duffel
        already guarantees each itinerary is bookable as a unit.
        """
        date = _local_date(depart_after_utc, airports, origins[0])
        targets = self._dest_targets(dests)
        # A cancellation strands you into tomorrow too - search today and the next
        # day so red-eyes and next-morning options are on the table.
        dates = [date, _add_days(date, 1)]

        # Fan out both passenger counts in one pool so they overlap. Keep concurrency
        # modest so we stay under Duffel's burst rate limit (429s are retried anyway).
        counts = [party] + ([1] if party > 1 else [])
        jobs = [(o, d, dt, pc) for pc in counts for o in origins for d in targets for dt in dates]
        by_count: Dict[int, List[dict]] = {pc: [] for pc in counts}
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(self._offer_request, o, d, dt, pc): (o, d, pc) for o, d, dt, pc in jobs}
            for fut in cf.as_completed(futures):
                o, d, pc = futures[fut]
                try:
                    by_count[pc].extend(fut.result())
                except Exception as exc:  # one route failing shouldn't sink the search
                    print(f"  duffel warning ({o}->{d} x{pc}): {exc}")

        party_best = self._distinct(by_count[party], dests)
        died = {}
        if party > 1:
            solo_best = self._distinct(by_count[1], dests)
            died = {k: offer for k, offer in solo_best.items() if k not in party_best}

        return {
            "viable": [self._segments_from_offer(offer, party) for offer in party_best.values()],
            "died": [self._segments_from_offer(offer, 1) for offer in died.values()],
        }

    def _distinct(self, offers: List[dict], dests) -> Dict[tuple, dict]:
        """Dedup offers to distinct itineraries (nonstop + one-stop, landing in the
        requested metro), keeping the cheapest fare for each. Keyed by flight numbers."""
        best: Dict[tuple, tuple] = {}
        for offer in offers:
            segs = offer["slices"][0]["segments"]
            if not (1 <= len(segs) <= 2):
                continue
            if segs[-1]["destination"]["iata_code"] not in dests:
                continue
            key = tuple(f'{s["marketing_carrier"]["iata_code"]}{s["marketing_carrier_flight_number"]}' for s in segs)
            amount = float(offer["total_amount"])
            if key not in best or amount < best[key][0]:
                best[key] = (amount, offer)
        return {k: v[1] for k, v in best.items()}

    # --- helpers --------------------------------------------------
    def _dest_targets(self, dests) -> List[str]:
        """One city code if the destination metro has one, else the airport list."""
        for m in self.raw["metros"].values():
            if m.get("duffel_city") and set(dests) <= set(m["airports"]):
                return [m["duffel_city"]]
        return list(dests)

    def _offer_request(self, origin: str, destination: str, date: str, party: int) -> List[dict]:
        body = json.dumps({"data": {
            "slices": [{"origin": origin, "destination": destination, "departure_date": date}],
            "passengers": [{"type": "adult"}] * party,
            "cabin_class": self.cabin,
        }}).encode()
        req = urllib.request.Request(DUFFEL_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.token}",
            "Duffel-Version": "v2",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=40) as resp:
                    return json.loads(resp.read())["data"]["offers"]
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 3:
                    retry_after = exc.headers.get("retry-after")
                    try:
                        wait = min(float(retry_after), 6.0)
                    except (TypeError, ValueError):
                        wait = 2.0 ** attempt  # 1s, 2s, 4s
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"Duffel {exc.code}: {exc.read().decode()[:300]}")
        return []

    def _segments_from_offer(self, offer: dict, party: int) -> List[Segment]:
        segs = offer["slices"][0]["segments"]
        per_person = round(float(offer["total_amount"]) / party, 2)
        out = []
        for i, s in enumerate(segs):
            mc = s["marketing_carrier"]
            number = str(s["marketing_carrier_flight_number"]).lstrip("0") or "0"
            aircraft = (s.get("aircraft") or {}).get("name", "") if s.get("aircraft") else ""
            out.append(Segment(
                carrier=mc["iata_code"],
                carrier_name=mc.get("name", mc["iata_code"]),
                number=number,
                origin=s["origin"]["iata_code"],
                destination=s["destination"]["iata_code"],
                depart_local=s["departing_at"][:16],   # trim seconds -> "YYYY-MM-DDTHH:MM"
                arrive_local=s["arriving_at"][:16],
                seats_available=party,                  # Duffel fails closed: offer exists => party fits
                fare_per_person=per_person if i == 0 else 0.0,  # whole-itinerary fare on the first leg
                aircraft=aircraft,
                logo=mc.get("logo_symbol_url", ""),
            ))
        return out


def _local_date(depart_after_utc: datetime, airports: Dict[str, Airport], origin: str) -> str:
    offset = airports[origin].utc_offset_minutes
    return (depart_after_utc + timedelta(minutes=offset)).strftime("%Y-%m-%d")


def _add_days(date_str: str, n: int) -> str:
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


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
