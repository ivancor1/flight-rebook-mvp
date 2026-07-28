#!/usr/bin/env python3
"""flight-rebook-mvp - local web app.

Run:   python3 app.py            (then open http://localhost:8000)
       python3 app.py --port 8080

Standard library only. No pip install, no network access needed.
"""
import argparse
import json
import os
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from engine import Engine
from entitlement import compare, refund_entitlement
from models import parse
from sources import FixtureSource

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = HERE  # flat layout: index.html, style.css and app.js sit next to app.py

SOURCE = FixtureSource()
ENGINE = Engine(SOURCE)
DISRUPTION = SOURCE.disruption()

# The fixture scenario is frozen at this moment: standing at SJC just after the
# gate agent handed over the redeye. Real deployments would use the wall clock.
SCENARIO_NOW_LOCAL = "2026-07-28T11:00"
SCENARIO_TZ_AIRPORT = "SJC"


def scenario_now_utc() -> datetime:
    ap = ENGINE.airports[SCENARIO_TZ_AIRPORT]
    return parse(SCENARIO_NOW_LOCAL) - timedelta(minutes=ap.utc_offset_minutes)


def scenario_payload() -> dict:
    offer = DISRUPTION.airline_offer
    return {
        "now_local": SCENARIO_NOW_LOCAL,
        "now_airport": SCENARIO_TZ_AIRPORT,
        "data_source": {"name": SOURCE.name, "note": SOURCE.coverage_note},
        "disruption": {
            "original_flight": DISRUPTION.original_flight,
            "origin": DISRUPTION.original_origin,
            "destination": DISRUPTION.original_destination,
            "depart_local": DISRUPTION.original_depart_local,
            "arrive_local": DISRUPTION.original_arrive_local,
            "cause": DISRUPTION.cause,
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
    origin = body.get("origin", "SJC")
    destination = body.get("destination", "NYC")
    party = int(body.get("party_size", DISRUPTION.party_size))
    after_local = body.get("depart_after_local") or SCENARIO_NOW_LOCAL
    tz_airport = ENGINE.expand(origin)[0]
    after_utc = parse(after_local) - timedelta(minutes=ENGINE.airports[tz_airport].utc_offset_minutes)
    include_self = bool(body.get("include_self_connections", True))

    result = ENGINE.search(origin, destination, party, after_utc, include_self_connections=include_self)
    offer = DISRUPTION.airline_offer

    options = []
    for o in result["options"]:
        d = o.to_dict(ENGINE.airports)
        d["comparison"] = compare(o, DISRUPTION, offer, ENGINE)
        d["minutes_earlier_than_offer"] = ENGINE.better_than(o, offer)
        d["different_airport_than_ticketed"] = (
            o.origin != DISRUPTION.original_origin or o.destination != DISRUPTION.original_destination
        )
        options.append(d)

    collapsed = []
    for o in result["collapsed_on_party_size"]:
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
        "collapsed_on_party_size": collapsed,
        "airline_offer": offer.to_dict(ENGINE.airports),
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
        if urlparse(self.path).path != "/api/search":
            return self._json({"error": "not found"}, 404)
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            return self._json(run_search(body))
        except Exception as exc:  # keep the demo alive, show the reason
            return self._json({"error": str(exc)}, 400)

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
