"""The search. This is the part the airline will not do for you."""
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from models import Airport, Option, Segment, parse

MIN_LAYOVER_SAME_CARRIER = 45      # minutes
MIN_LAYOVER_SELF_CONNECT = 90      # different carriers = separate tickets, bags don't transfer
MAX_LAYOVER = 360
CHECKIN_BUFFER_MINUTES = 60        # be at the airport this long before departure


def utc(dt_local: str, airport: Airport) -> datetime:
    return parse(dt_local) - timedelta(minutes=airport.utc_offset_minutes)


class Engine:
    def __init__(self, source):
        self.source = source
        self.airports: Dict[str, Airport] = source.airports()
        self.metros = source.metros()
        self.all_segments: List[Segment] = source.segments()
        self.ground = source.ground_minutes()

    # --- strategy 1: treat both ends as metro areas ---------------
    def expand(self, code: str) -> List[str]:
        code = code.upper()
        if code in self.metros:
            return list(self.metros[code]["airports"])
        for key, m in self.metros.items():
            if code in m["airports"]:
                return list(m["airports"])
        return [code]

    def ground_to(self, airport: str) -> int:
        return int(self.ground.get("from_traveller", {}).get(airport, 60))

    def ground_from(self, airport: str) -> int:
        return int(self.ground.get("to_final_destination", {}).get(airport, 60))

    # --- candidate building ---------------------------------------
    def search(
        self,
        origin: str,
        destination: str,
        party_size: int,
        depart_after_utc: datetime,
        include_self_connections: bool = True,
        max_connections: int = 1,
    ) -> dict:
        origins = self.expand(origin)
        dests = self.expand(destination)
        options: List[Option] = []

        def bookable(seg: Segment) -> bool:
            return utc(seg.depart_local, self.airports[seg.origin]) >= depart_after_utc

        duffel_collapsed: List[Option] = []
        if hasattr(self.source, "built_options"):
            # Live source (Duffel): it returns complete, already-validated itineraries
            # priced for the whole party, plus the ones that "died on party size" (see
            # DuffelSource.built_options). We don't recombine legs - we wrap, filter by
            # earliest acceptable departure, and rank. Connection airports we've never
            # seen get a stub Airport; the door-to-door math only needs the (known)
            # origin and final destination, and layover math cancels the hub's offset.
            built = self.source.built_options(origins, dests, party_size, depart_after_utc, self.airports)
            viable_lists, died_lists = built["viable"], built["died"]
            for segs in viable_lists + died_lists:
                for s in segs:
                    for code in (s.origin, s.destination):
                        if code not in self.airports:
                            self.airports[code] = Airport(code=code, name=code, metro="", utc_offset_minutes=0)
            for segs in viable_lists:
                if bookable(segs[0]):
                    if not include_self_connections and len({s.carrier for s in segs}) > 1:
                        continue
                    options.append(self._wrap(segs))
            for segs in died_lists:
                if bookable(segs[0]):
                    o = self._wrap(segs)
                    o.max_party_supported = 1
                    o.rejected_reason = (
                        f"Bookable for 1 passenger but not for all {party_size} on one booking - "
                        f"the airline is selling fewer than {party_size} seats in this fare bucket. "
                        f"It looks available until you ask for the whole party."
                    )
                    duffel_collapsed.append(o)
        else:
            # Static source (fixtures): the engine builds the itineraries itself.
            # nonstops
            for s in self.all_segments:
                if s.origin in origins and s.destination in dests and bookable(s):
                    options.append(self._wrap([s]))

            # strategy 2: one-stop connections that actually connect
            if max_connections >= 1:
                firsts = [s for s in self.all_segments if s.origin in origins and s.destination not in dests and bookable(s)]
                for a in firsts:
                    arr = utc(a.arrive_local, self.airports[a.destination])
                    for b in self.all_segments:
                        if b.origin != a.destination or b.destination not in dests:
                            continue
                        dep = utc(b.depart_local, self.airports[b.origin])
                        gap = int((dep - arr).total_seconds() // 60)
                        if gap <= 0:
                            continue  # departs before the first leg lands
                        cross = a.carrier != b.carrier
                        if cross and not include_self_connections:
                            continue
                        floor = MIN_LAYOVER_SELF_CONNECT if cross else MIN_LAYOVER_SAME_CARRIER
                        if gap < floor or gap > MAX_LAYOVER:
                            continue
                        options.append(self._wrap([a, b]))

        # strategy 3: party size is a hard filter, applied per segment
        viable, collapsed = [], []
        for o in options:
            o.max_party_supported = min(s.seats_available for s in o.segments)
            if o.max_party_supported >= party_size:
                viable.append(o)
            else:
                tight = min(o.segments, key=lambda s: s.seats_available)
                o.rejected_reason = (
                    f"{tight.flight} {tight.origin}-{tight.destination} has "
                    f"{tight.seats_available} seat(s); you need {party_size}."
                )
                collapsed.append(o)

        # died-on-party-size itineraries from a live source (already have their reason)
        collapsed.extend(duffel_collapsed)

        # strategy 5: rank by when you actually get there, not when you leave
        viable.sort(key=lambda o: (o.door_arrival(self.airports), o.door_to_door_minutes(self.airports)))
        collapsed.sort(key=lambda o: o.door_arrival(self.airports))

        return {
            "origins_searched": origins,
            "destinations_searched": dests,
            "party_size": party_size,
            "options": viable,
            "collapsed_on_party_size": collapsed,
        }

    def _wrap(self, segs: List[Segment]) -> Option:
        o = Option(segments=list(segs))
        o.ground_to_origin_minutes = self.ground_to(o.origin)
        o.ground_from_destination_minutes = self.ground_from(o.destination)
        if o.is_self_connection:
            o.tags.append("self_connection")
            o.notes.append(
                "Different airlines on separate tickets: your bags will not be "
                "checked through and a delay on leg 1 is your problem, not theirs."
            )
        return o

    # --- comparison helpers ---------------------------------------
    def better_than(self, option: Option, baseline: Option) -> Optional[int]:
        """Minutes earlier this option gets you to the door vs the baseline."""
        if baseline is None:
            return None
        delta = baseline.door_arrival(self.airports) - option.door_arrival(self.airports)
        return int(delta.total_seconds() // 60)
