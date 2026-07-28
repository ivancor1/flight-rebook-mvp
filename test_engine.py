"""Run: python3 -m unittest -v"""
import threading
import unittest
from datetime import date, datetime, timedelta

import app as api

from sources import DuffelSource, FixtureSource, ReferenceData
from engine import Engine
from entitlement import refund_entitlement, compare, significant_change
from models import Disruption, Option, Passenger, Segment

SCENARIO_DAY = date(2026, 7, 28)          # the day the fixtures are written for
DEPART_AFTER = datetime(2026, 7, 28, 18, 0)  # 11:00 PT in UTC

# The fixtures shift onto whatever day the app runs, so pin the day in tests.
fixtures = lambda: FixtureSource(today=SCENARIO_DAY)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.src = fixtures()
        self.eng = Engine(self.src)
        self.dis = self.src.disruption()

    def test_metro_expansion_both_ends(self):
        self.assertEqual(sorted(self.eng.expand("SJC")), ["OAK", "SFO", "SJC"])
        self.assertEqual(sorted(self.eng.expand("NYC")), ["EWR", "JFK", "LGA"])

    def test_finds_options_the_airline_never_offered(self):
        r = self.eng.search("SJC", "NYC", 4, DEPART_AFTER)
        origins = {o.origin for o in r["options"]}
        self.assertTrue({"SFO", "OAK"} & origins, "should reach beyond the ticketed airport")
        self.assertTrue(any(o.destination in ("EWR", "LGA") for o in r["options"]))

    def test_party_size_is_a_hard_filter_on_every_segment(self):
        r4 = self.eng.search("SJC", "NYC", 4, DEPART_AFTER)
        ids4 = {"-".join(s.flight for s in o.segments) for o in r4["options"]}
        self.assertNotIn("AA512-AA1188", ids4)  # PHX-EWR only has 2 seats
        collapsed = {"-".join(s.flight for s in o.segments) for o in r4["collapsed_on_party_size"]}
        self.assertIn("AA512-AA1188", collapsed)
        r2 = self.eng.search("SJC", "NYC", 2, DEPART_AFTER)
        ids2 = {"-".join(s.flight for s in o.segments) for o in r2["options"]}
        self.assertIn("AA512-AA1188", ids2)  # same itinerary is fine for 2

    def test_rejects_connections_that_do_not_connect(self):
        r = self.eng.search("SJC", "NYC", 2, DEPART_AFTER)
        ids = {"-".join(s.flight for s in o.segments) for o in r["options"]}
        self.assertNotIn("UA884-UA1902", ids)  # ORD leg departs before the SJC leg lands
        self.assertIn("UA884-UA2044", ids)

    def test_ranked_by_arrival_not_departure(self):
        r = self.eng.search("SJC", "NYC", 4, DEPART_AFTER)
        arrivals = [o.door_arrival(self.eng.airports) for o in r["options"]]
        self.assertEqual(arrivals, sorted(arrivals))

    def test_beats_the_airline_offer(self):
        r = self.eng.search("SJC", "NYC", 4, DEPART_AFTER)
        best = r["options"][0]
        saved = self.eng.better_than(best, self.dis.airline_offer)
        self.assertGreater(saved, 6 * 60, "best option should beat the redeye by hours")

    def test_cross_carrier_connection_is_flagged(self):
        r = self.eng.search("SJC", "NYC", 4, DEPART_AFTER)
        by_id = {"-".join(s.flight for s in o.segments): o for o in r["options"]}
        self.assertIn("self_connection", by_id["AS1201-B6664"].tags)


class TestFixtureClock(unittest.TestCase):
    """The seeded demo has to work on any date, not just the day it was written."""

    def _search_from_scenario_now(self, src):
        eng = Engine(src)
        now = datetime.strptime(src.now_local(), "%Y-%m-%dT%H:%M")
        after_utc = now - timedelta(minutes=eng.airports["SJC"].utc_offset_minutes)
        return eng.search("SJC", "NYC", 4, after_utc)

    def test_scenario_lands_on_the_day_you_run_it(self):
        for day in (SCENARIO_DAY, date(2026, 7, 29), date(2027, 3, 1)):
            src = FixtureSource(today=day)
            self.assertEqual(src.now_local()[:10], day.isoformat())
            self.assertEqual(src.disruption().original_depart_local[:10], day.isoformat())
            self.assertTrue(all(s.depart_local[:10] == day.isoformat()
                                for s in src.segments() if s.origin in ("SFO", "SJC", "OAK")))

    def test_demo_still_finds_options_on_a_later_date(self):
        for day in (SCENARIO_DAY, date(2026, 7, 29), date(2027, 3, 1)):
            r = self._search_from_scenario_now(FixtureSource(today=day))
            self.assertTrue(r["options"], f"no bookable options on {day}")


def _disruption(disruption_type, fare=400.0, party=2, arrive="2026-07-28T22:05"):
    return Disruption(
        original_flight="AA16", original_origin="SFO", original_destination="JFK",
        original_depart_local="2026-07-28T13:30", original_arrive_local=arrive,
        cause="mechanical", itinerary_type="domestic",
        passengers=[Passenger(name=f"Passenger {i + 1}", pnr="ABC123", fare_paid=fare)
                    for i in range(party)],
        disruption_type=disruption_type,
    )


def _connecting_offer(depart, arrive, via="PHX", origin="SFO", destination="JFK"):
    """A replacement itinerary with one connection point."""
    first = Segment(carrier="AA", carrier_name="American", number="99",
                    origin=origin, destination=via, depart_local=depart, arrive_local=depart,
                    seats_available=9, fare_per_person=0.0)
    second = Segment(carrier="AA", carrier_name="American", number="100",
                     origin=via, destination=destination, depart_local=arrive, arrive_local=arrive,
                     seats_available=9, fare_per_person=0.0)
    return Option(segments=[first, second])


def _offer(depart, arrive, origin="SFO", destination="JFK"):
    seg = Segment(carrier="AA", carrier_name="American", number="99",
                  origin=origin, destination=destination,
                  depart_local=depart, arrive_local=arrive,
                  seats_available=9, fare_per_person=0.0)
    return Option(segments=[seg])


class TestEntitlement(unittest.TestCase):
    def setUp(self):
        self.src = fixtures()
        self.eng = Engine(self.src)
        self.dis = self.src.disruption()

    def test_airline_offer_is_a_significant_change(self):
        sc = significant_change(self.dis, self.dis.airline_offer, self.eng.airports)
        self.assertTrue(sc["significant"])
        self.assertTrue(any("later" in r for r in sc["reasons"]))
        self.assertTrue(any("departure airport" in r for r in sc["reasons"]))

    def test_refund_is_the_full_party_fare(self):
        ent = refund_entitlement(self.dis, self.dis.airline_offer, self.eng.airports)
        self.assertTrue(ent["entitled"])
        self.assertAlmostEqual(ent["refund_amount"], 384.0 * 2 + 402.0 * 2)
        self.assertEqual(len({p["pnr"] for p in ent["per_passenger"]}), 3)  # three separate PNRs

    def test_comparison_nets_refund_against_new_ticket(self):
        r = self.eng.search("SJC", "NYC", 4, DEPART_AFTER)
        c = compare(r["options"][0], self.dis, self.dis.airline_offer, self.eng)
        self.assertAlmostEqual(c["net_out_of_pocket"], c["new_ticket_total"] - c["refund_if_you_decline"])
        self.assertGreater(c["hours_earlier_than_airline_offer"], 0)

    # --- the gate: entitlement is not a constant --------------------------
    def test_a_short_delay_is_not_an_automatic_refund(self):
        dis = _disruption("delayed")
        # rebooked 40 minutes later, same airports, no added stop
        ent = refund_entitlement(dis, _offer("2026-07-28T14:10", "2026-07-28T22:45"), self.eng.airports)
        self.assertFalse(ent["entitled"])
        self.assertIsNone(ent["refund_amount"])
        self.assertEqual(ent["refund_state"], "none")
        self.assertNotIn("The flight was cancelled", ent["basis"])

    def test_uncancelled_but_significantly_changed_is_still_entitled(self):
        dis = _disruption("delayed")
        # same airline, but it lands 5 hours late - over the 3h domestic threshold
        ent = refund_entitlement(dis, _offer("2026-07-28T18:30", "2026-07-29T03:05"), self.eng.airports)
        self.assertTrue(ent["entitled"])
        self.assertIn("significantly changed", ent["basis"])
        self.assertNotIn("The flight was cancelled", ent["basis"])
        self.assertAlmostEqual(ent["refund_amount"], 800.0)

    def test_cancellation_is_entitled_even_with_a_clean_rebooking(self):
        dis = _disruption("cancelled")
        ent = refund_entitlement(dis, _offer("2026-07-28T14:10", "2026-07-28T22:45"), self.eng.airports)
        self.assertTrue(ent["entitled"])
        self.assertIn("cancelled", ent["basis"])

    def test_no_offer_alone_does_not_prove_a_refund(self):
        ent = refund_entitlement(_disruption(""), None, self.eng.airports)
        self.assertFalse(ent["entitled"])
        self.assertEqual(ent["refund_state"], "conditional")
        ent_cancelled = refund_entitlement(_disruption("cancelled"), None, self.eng.airports)
        self.assertTrue(ent_cancelled["entitled"])

    def test_unknown_disruption_is_conditional_not_a_claim(self):
        dis = _disruption("")
        ent = refund_entitlement(dis, _offer("2026-07-28T14:10", "2026-07-28T22:45"), self.eng.airports)
        self.assertFalse(ent["entitled"])
        self.assertEqual(ent["refund_state"], "conditional")
        self.assertIsNone(ent["refund_amount"])
        self.assertAlmostEqual(ent["refund_if_cancelled"], 800.0)

    # --- "adds a connection" is a comparison, not a count -----------------
    def test_added_connection_needs_to_know_the_original(self):
        """A one-stop replacement adds nothing if the flight they lost was itself
        a one-stop - and if nobody said, we don't get to claim it either way."""
        dis = _disruption("delayed")            # original_connections defaults to None
        offer = _connecting_offer("2026-07-28T14:10", "2026-07-28T22:45")
        sc = significant_change(dis, offer, self.eng.airports)
        self.assertFalse(sc["significant"])
        self.assertFalse(any("connection" in r for r in sc["reasons"]))
        self.assertTrue(any("not told" in u for u in sc["unknowns"]))
        ent = refund_entitlement(dis, offer, self.eng.airports)
        self.assertFalse(ent["entitled"])
        self.assertIsNone(ent["refund_amount"])

    def test_added_connection_counts_when_the_original_was_a_nonstop(self):
        dis = _disruption("delayed")
        dis.original_connections = 0
        sc = significant_change(dis, _connecting_offer("2026-07-28T14:10", "2026-07-28T22:45"),
                                self.eng.airports)
        self.assertTrue(sc["significant"])
        self.assertTrue(any("nonstop" in r for r in sc["reasons"]))

    def test_same_number_of_connections_is_not_a_trigger(self):
        dis = _disruption("delayed")
        dis.original_connections = 1            # they had booked a one-stop already
        sc = significant_change(dis, _connecting_offer("2026-07-28T14:10", "2026-07-28T22:45"),
                                self.eng.airports)
        self.assertFalse(sc["significant"])
        self.assertEqual(sc["unknowns"], [])

    def test_unknown_fare_is_unknown_not_zero(self):
        dis = _disruption("cancelled", fare=0.0)
        offer = _offer("2026-07-28T14:10", "2026-07-28T22:45")
        ent = refund_entitlement(dis, offer, self.eng.airports)
        self.assertTrue(ent["entitled"])
        self.assertIsNone(ent["refund_amount"])
        self.assertEqual(ent["refund_state"], "unknown")
        self.assertTrue(all(p["refund"] is None for p in ent["per_passenger"]))
        r = self.eng.search("SJC", "NYC", 2, DEPART_AFTER)
        c = compare(r["options"][0], dis, offer, self.eng)
        self.assertEqual(c["net_out_of_pocket"], c["new_ticket_total"])
        self.assertNotIn("after the refund", c["verdict"])


class TestSourceSeam(unittest.TestCase):
    """Reference data is config; the demo cancellation is not."""

    def test_live_source_has_no_demo_scenario(self):
        live = DuffelSource("test-token")          # no network call in __init__
        self.assertFalse(hasattr(live, "disruption"),
                         "a live source must not carry the fixture demo cancellation")
        self.assertFalse(live.uses_demo_clock)
        self.assertEqual(live.segments(), [])      # itineraries come from built_options

    def test_live_source_still_gets_the_reference_data(self):
        live = DuffelSource("test-token")
        fixture = fixtures()
        self.assertEqual(set(live.airports()), set(fixture.airports()))
        self.assertEqual(live.metros(), fixture.metros())
        self.assertEqual(live.ground_minutes(), fixture.ground_minutes())

    def test_live_source_runs_on_the_wall_clock(self):
        self.assertEqual(DuffelSource("test-token").now_local()[:10],
                         datetime.now().strftime("%Y-%m-%d"))


class TestRequestIsolation(unittest.TestCase):
    """A request must not be able to scribble on state another request is reading."""

    def _body(self, airport_code):
        day = api.SCENARIO_NOW_LOCAL[:10]
        return {
            "origin": "SJC", "destination": "NYC", "party_size": 2,
            "depart_after_local": api.SCENARIO_NOW_LOCAL,
            "disruption": {
                "original_flight": "XX1", "original_origin": airport_code,
                "original_destination": "JFK", "disruption_type": "cancelled",
                "party_size": 2, "total_paid": 800,
                "airline_rebooking": {"final_arrive_local": f"{day}T23:00",
                                      "origin": airport_code, "destination": "JFK",
                                      "segments": []},
            },
        }

    def test_invented_airports_stay_inside_the_request(self):
        before = set(api.ENGINE.airports)
        result = api.run_search(self._body("ZZZ"))
        self.assertTrue(result["options"])
        self.assertEqual(set(api.ENGINE.airports), before,
                         "a request added a stub airport to the shared engine")

    def test_concurrent_requests_do_not_collide(self):
        before = set(api.ENGINE.airports)
        errors, results = [], []

        def go(code):
            try:
                results.append(api.run_search(self._body(code)))
            except Exception as exc:      # a dict mutated mid-iteration lands here
                errors.append(exc)

        threads = [threading.Thread(target=go, args=(f"Z{i:02d}",)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 12)
        self.assertEqual(set(api.ENGINE.airports), before)


if __name__ == "__main__":
    unittest.main()
