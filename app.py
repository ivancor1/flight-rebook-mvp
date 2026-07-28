#!/usr/bin/env python3
"""flight-rebook-mvp - local web app.

Run:   python3 app.py            (then open http://localhost:8000)
       python3 app.py --port 8080

Standard library only. No pip install, no network access needed.
"""
import argparse
import json
import os
import re
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from engine import Engine
from entitlement import compare, refund_entitlement
from models import Airport, Disruption, Option, Passenger, Segment, parse
from parse import parse_disruption
from sources import DuffelSource, FixtureSource

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = HERE  # flat layout: index.html, style.css and app.js sit next to app.py


def make_source():
    """Use live Duffel inventory if a token is available, else the seeded fixtures.

    Token lookup: $DUFFEL_ACCESS_TOKEN, then a git-ignored duffel_token.txt next
    to this file. No token -> the offline fixture demo, exactly as before.
    """
    token = os.environ.get("DUFFEL_ACCESS_TOKEN", "").strip()
    if not token:
        token_file = os.path.join(HERE, "duffel_token.txt")
        if os.path.isfile(token_file):
            with open(token_file) as fh:
                token = fh.read().strip()
    if token:
        try:
            return DuffelSource(token)
        except Exception as exc:  # never let a bad token take the app down
            print(f"Duffel source unavailable ({exc}); falling back to fixtures.")
    return FixtureSource()


def openai_key():
    """$OPENAI_API_KEY, then a git-ignored openai_key.txt. Empty string if absent."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        key_file = os.path.join(HERE, "openai_key.txt")
        if os.path.isfile(key_file):
            with open(key_file) as fh:
                key = fh.read().strip()
    return key


OPENAI_KEY = openai_key()

SOURCE = make_source()
ENGINE = Engine(SOURCE)
DISRUPTION = SOURCE.disruption()


# --- turning parsed/entered fields into a real Disruption ----------------
def _split_flight(flight: str):
    m = re.match(r"^\s*([A-Za-z]{1,3})\s*0*(\d+)\s*$", flight or "")
    return (m.group(1).upper(), m.group(2)) if m else ((flight or "").strip(), "")


def _ensure_airport(code: str):
    """A stub keeps the door-to-door math from crashing on an airport we don't
    have a timezone for; offsets only matter within the searched metros (known)."""
    if code and code not in ENGINE.airports:
        ENGINE.airports[code] = Airport(code=code, name=code, metro="", utc_offset_minutes=0)


def build_airline_offer(reb: dict, d: dict):
    """The itinerary the airline already rebooked them onto - the baseline every
    option is compared against. None if the message didn't mention one."""
    if not reb:
        return None
    try:
        final_arr = reb.get("final_arrive_local")
        dest = reb.get("destination") or d.get("original_destination")
        orig = reb.get("origin") or d.get("original_origin")
        built = []
        segs = reb.get("segments") or []
        for i, s in enumerate(segs):
            arr = s.get("arrive_local") or (final_arr if i == len(segs) - 1 else None)
            dep = s.get("depart_local")
            if not (arr and dep and s.get("origin") and s.get("destination")):
                built = []
                break
            carrier, number = _split_flight(s.get("flight", ""))
            built.append(Segment(carrier=carrier, carrier_name=carrier or "airline", number=number,
                                  origin=s["origin"], destination=s["destination"],
                                  depart_local=dep[:16], arrive_local=arr[:16],
                                  seats_available=9, fare_per_person=0.0))
        if not built:  # only a final arrival is known - one synthetic leg
            if not (final_arr and dest):
                return None
            dep = d.get("original_depart_local") or final_arr
            built = [Segment(carrier="", carrier_name="airline", number="",
                             origin=orig or dest, destination=dest,
                             depart_local=dep[:16], arrive_local=final_arr[:16],
                             seats_available=9, fare_per_person=0.0)]
        for s in built:
            _ensure_airport(s.origin)
            _ensure_airport(s.destination)
        offer = ENGINE._wrap(built)
        offer.tags = ["airline_offer"]
        offer.notes = ["What the airline put you on without asking."]
        return offer
    except Exception as exc:
        print(f"airline_offer build skipped: {exc}")
        return None


def build_disruption(d):
    """Build a Disruption from the user's own (parsed or entered) fields, or None
    when nothing was supplied - the app starts on a clean slate, no demo flight."""
    if not d:
        return None
    try:
        party = int(d.get("party_size") or DISRUPTION.party_size)
        dtype = str(d.get("disruption_type") or "").strip().lower()
        if dtype not in ("cancelled", "delayed", "changed"):
            dtype = ""  # never assume a cancellation we weren't told about
        total = d.get("total_paid")
        pnrs = d.get("pnrs") or []
        passengers = []
        for i in range(max(party, 1)):
            fare = round(float(total) / party, 2) if total else 0.0
            passengers.append(Passenger(name=f"Passenger {i + 1}",
                                        pnr=pnrs[i % len(pnrs)] if pnrs else "-",
                                        fare_paid=fare))
        return Disruption(
            original_flight=d.get("original_flight") or "your cancelled flight",
            original_origin=d.get("original_origin") or "",
            original_destination=d.get("original_destination") or "",
            original_depart_local=d.get("original_depart_local") or "",
            original_arrive_local=d.get("original_arrive_local") or "",
            cause=d.get("cause") or "",
            disruption_type=dtype,
            itinerary_type="domestic",
            passengers=passengers,
            airline_offer=build_airline_offer(d.get("airline_rebooking"), d),
        )
    except Exception as exc:
        print(f"disruption build failed ({exc}); using fixture demo.")
        return DISRUPTION

# The fixture scenario is frozen at one moment: standing at SJC just after the
# gate agent handed over the redeye. FixtureSource shifts that moment onto today,
# so the seeded flights are always still ahead of the clock. A live source uses
# the real wall clock instead (see uses_demo_clock, which the UI reads).
SCENARIO_NOW_LOCAL = SOURCE.now_local()
SCENARIO_TZ_AIRPORT = "SJC"


def scenario_now_utc() -> datetime:
    ap = ENGINE.airports[SCENARIO_TZ_AIRPORT]
    return parse(SCENARIO_NOW_LOCAL) - timedelta(minutes=ap.utc_offset_minutes)


def scenario_payload() -> dict:
    offer = DISRUPTION.airline_offer
    return {
        "now_local": SCENARIO_NOW_LOCAL,
        "now_airport": SCENARIO_TZ_AIRPORT,
        "uses_demo_clock": getattr(SOURCE, "uses_demo_clock", False),
        "data_source": {"name": SOURCE.name, "note": SOURCE.coverage_note},
        "disruption": {
            "original_flight": DISRUPTION.original_flight,
            "origin": DISRUPTION.original_origin,
            "destination": DISRUPTION.original_destination,
            "depart_local": DISRUPTION.original_depart_local,
            "arrive_local": DISRUPTION.original_arrive_local,
            "cause": DISRUPTION.cause,
            "disruption_type": DISRUPTION.disruption_type,
            "party_size": DISRUPTION.party_size,
            "pnrs": DISRUPTION.pnrs,
            "total_paid": DISRUPTION.total_paid,
            "passengers": [
                {"name": p.name, "pnr": p.pnr, "fare_paid": p.fare_paid}
                for p in DISRUPTION.passengers
            ],
        },
        "airline_offer": offer.to_dict(ENGINE.airports),
        "entitlement": refund_entitlement(DISRUPTION, offer, ENGINE.airports),
    }


def run_search(body: dict) -> dict:
    disruption = build_disruption(body.get("disruption"))
    origin = body.get("origin") or "SFO"
    destination = body.get("destination") or "NYC"
    party = int(body.get("party_size") or (disruption.party_size if disruption else 1))
    after_local = body.get("depart_after_local") or SCENARIO_NOW_LOCAL
    tz_airport = ENGINE.expand(origin)[0]
    after_utc = parse(after_local) - timedelta(minutes=ENGINE.airports[tz_airport].utc_offset_minutes)
    include_self = bool(body.get("include_self_connections", True))

    result = ENGINE.search(origin, destination, party, after_utc, include_self_connections=include_self)
    offer = disruption.airline_offer if disruption else None

    MAX_SHOWN = 20  # options are ranked by door-to-door arrival; show the best of them
    total_found = len(result["options"])

    options = []
    for o in result["options"][:MAX_SHOWN]:
        d = o.to_dict(ENGINE.airports)
        d["minutes_earlier_than_offer"] = ENGINE.better_than(o, offer)
        if disruption:
            d["comparison"] = compare(o, disruption, offer, ENGINE)
            d["different_airport_than_ticketed"] = (
                o.origin != disruption.original_origin or o.destination != disruption.original_destination
            )
        options.append(d)

    MAX_COLLAPSED = 8  # a live search can surface dozens; show the best-timed ones
    total_collapsed = len(result["collapsed_on_party_size"])
    collapsed = []
    for o in result["collapsed_on_party_size"][:MAX_COLLAPSED]:
        d = o.to_dict(ENGINE.airports)
        d["minutes_earlier_than_offer"] = ENGINE.better_than(o, offer)
        collapsed.append(d)

    return {
        "query": {
            "origin": origin,
            "destination": destination,
            "party_size": party,
            "depart_after_local": after_local,
            "include_self_connections": include_self,
        },
        "airports_searched": {
            "origins": result["origins_searched"],
            "destinations": result["destinations_searched"],
        },
        "options": options,
        "total_options_found": total_found,
        "collapsed_on_party_size": collapsed,
        "total_collapsed_found": total_collapsed,
        "data_source": {"name": SOURCE.name, "note": SOURCE.coverage_note},
        "airline_offer": offer.to_dict(ENGINE.airports) if offer else None,
        "disruption": {
            "original_flight": disruption.original_flight,
            "origin": disruption.original_origin,
            "destination": disruption.original_destination,
            "arrive_local": disruption.original_arrive_local,
            "cause": disruption.cause,
            "disruption_type": disruption.disruption_type,
            "party_size": disruption.party_size,
            "pnrs": disruption.pnrs,
            "total_paid": disruption.total_paid,
        } if disruption else None,
        "entitlement": refund_entitlement(disruption, offer, ENGINE.airports) if disruption else None,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "flight-rebook-mvp"

    def log_message(self, fmt, *args):  # quieter console
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, indent=2).encode(), "application/json")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._file("index.html", "text/html; charset=utf-8")
        if path == "/api/scenario":
            return self._json(scenario_payload())
        if path in ("/style.css", "/app.js"):
            name = os.path.basename(path)
            ctype = "text/css" if name.endswith(".css") else "application/javascript"
            return self._file(name, ctype)
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/search", "/api/parse"):
            return self._json({"error": "not found"}, 404)
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)
        if path == "/api/parse":
            return self._parse(body)
        try:
            return self._json(run_search(body))
        except Exception as exc:  # keep the demo alive, show the reason
            return self._json({"error": str(exc)}, 400)

    def _parse(self, body):
        text = (body.get("text") or "").strip()
        if not text:
            return self._json({"error": "Paste the message the airline sent you."}, 400)
        if not OPENAI_KEY:
            return self._json({"error": "No OpenAI key configured (openai_key.txt)."}, 400)
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            fields = parse_disruption(text, OPENAI_KEY, today=today)
            return self._json({"fields": fields})
        except Exception as exc:
            return self._json({"error": str(exc)}, 502)

    def _file(self, name, ctype):
        p = os.path.join(STATIC, name)
        if not os.path.isfile(p):
            return self._json({"error": "not found"}, 404)
        with open(p, "rb") as fh:
            self._send(200, fh.read(), ctype)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"flight-rebook-mvp running on http://{args.host}:{args.port}")
    print(f"data source: {SOURCE.name} - {SOURCE.coverage_note}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
