"""Run: python3 -m unittest -v"""
import unittest
from datetime import datetime, timedelta

from sources import FixtureSource
from engine import Engine
from entitlement import refund_entitlement, compare, significant_change

DEPART_AFTER = datetime(2026, 7, 28, 18, 0)  # 11:00 PT in UTC


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.src = FixtureSource()
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


class TestEntitlement(unittest.TestCase):
    def setUp(self):
        self.src = FixtureSource()
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


if __name__ == "__main__":
    unittest.main()
