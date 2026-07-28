"""Refund entitlement math under 14 CFR Part 260 (US DOT automatic refund rule).

This is what makes "just book a different airline" a real option instead of
eating a second ticket. Sources, checked 2026-07-28:
  14 CFR 260.6  https://www.law.cornell.edu/cfr/text/14/260.6
  14 CFR 260.2  https://www.law.cornell.edu/cfr/text/14/260.2  (definitions)
  DOT ticket refunds hub  https://www.transportation.gov/airconsumer/ticket-refunds

Not legal advice. Applies to flights to, from, or within the United States.
"""
from datetime import timedelta

from models import parse

DOMESTIC_HOURS = 3
INTERNATIONAL_HOURS = 6

CITATION = "14 CFR 260.6(a)(1) - https://www.law.cornell.edu/cfr/text/14/260.6"
PROMPT_REFUND = (
    "A refund owed under this rule must be paid in the original form of payment "
    "within 7 business days for credit-card purchases and 20 calendar days for "
    "other payment methods (14 CFR 260.2, definition of 'prompt refund')."
)


def significant_change(disruption, offer, airports) -> dict:
    """Does the airline's replacement itinerary itself trigger the rule?

    Per 14 CFR 260.2 a change is 'significant' if it moves departure or arrival
    by 3+ hours domestic (6+ international), changes the origin or destination
    airport, or adds connection points - among other triggers.
    """
    threshold = DOMESTIC_HOURS if disruption.itinerary_type == "domestic" else INTERNATIONAL_HOURS
    reasons = []
    if offer is None:
        return {"significant": True, "reasons": ["No alternative was offered."], "threshold_hours": threshold}

    orig_arr = parse(disruption.original_arrive_local)
    new_arr = parse(offer.segments[-1].arrive_local)
    late_hours = (new_arr - orig_arr).total_seconds() / 3600.0
    if late_hours >= threshold:
        reasons.append(f"Arrives {late_hours:.1f}h later than the flight you bought (threshold {threshold}h).")

    orig_dep = parse(disruption.original_depart_local)
    new_dep = parse(offer.segments[0].depart_local)
    early_hours = (orig_dep - new_dep).total_seconds() / 3600.0
    if early_hours >= threshold:
        reasons.append(f"Departs {early_hours:.1f}h earlier than booked (threshold {threshold}h).")

    if offer.origin != disruption.original_origin:
        reasons.append(f"Different departure airport ({disruption.original_origin} -> {offer.origin}).")
    if offer.destination != disruption.original_destination:
        reasons.append(f"Different arrival airport ({disruption.original_destination} -> {offer.destination}).")
    if len(offer.segments) - 1 > 0:
        reasons.append(f"Adds {len(offer.segments) - 1} connection point(s) the original itinerary did not have.")

    return {"significant": bool(reasons), "reasons": reasons, "threshold_hours": threshold}


def refund_entitlement(disruption, offer, airports) -> dict:
    """What the passengers are owed if they walk away from the rebooking."""
    cancelled = True  # this MVP is scoped to cancellations
    change = significant_change(disruption, offer, airports)
    entitled = cancelled or change["significant"]
    return {
        "entitled": entitled,
        "basis": (
            "The flight was cancelled. Under 14 CFR 260.6(a)(1) a passenger holding a "
            "nonrefundable ticket who declines the rebooking and declines any voucher "
            "is owed a full refund of the fare, taxes and ancillary fees."
        ),
        "citation": CITATION,
        "prompt_refund": PROMPT_REFUND,
        "refund_amount": disruption.total_paid,
        "per_passenger": [
            {"name": p.name, "pnr": p.pnr, "refund": p.fare_paid} for p in disruption.passengers
        ],
        "airline_offer_is_significant_change": change,
        "caveats": [
            "Taking the airline's rebooking, or accepting a voucher, gives up the cash refund.",
            "The refund is the fare you paid, not the cost of the replacement ticket - if the "
            "new ticket is more expensive, the difference is yours.",
            "Weather cancellations are still refundable. What weather removes is extras like "
            "hotels and meals, which US carriers owe only for cancellations within their control.",
            "Not legal advice.",
        ],
    }


def compare(option, disruption, offer, engine) -> dict:
    """Airline rebooking vs refund-and-rebook-elsewhere, side by side."""
    airports = engine.airports
    party = disruption.party_size
    ent = refund_entitlement(disruption, offer, airports)
    new_cost = round(option.price_per_person() * party, 2)
    refund = ent["refund_amount"] if ent["entitled"] else 0.0
    net = round(new_cost - refund, 2)
    minutes_saved = engine.better_than(option, offer)
    hours_saved = round(minutes_saved / 60.0, 1) if minutes_saved is not None else None
    dollars_per_hour = None
    if hours_saved and hours_saved > 0 and net > 0:
        dollars_per_hour = round(net / hours_saved, 2)
    return {
        "option_id": "-".join(s.flight for s in option.segments),
        "new_ticket_total": new_cost,
        "refund_if_you_decline": refund,
        "net_out_of_pocket": net,
        "hours_earlier_than_airline_offer": hours_saved,
        "net_cost_per_hour_saved": dollars_per_hour,
        "verdict": _verdict(net, hours_saved),
        "entitlement": ent,
    }


def _verdict(net: float, hours_saved) -> str:
    if hours_saved is None:
        return "No airline offer to compare against."
    if hours_saved <= 0:
        return "No better than what the airline already gave you."
    if net <= 0:
        return f"Gets you there {hours_saved}h earlier and the refund covers it with ${abs(net):.2f} left over."
    return f"Gets you there {hours_saved}h earlier for ${net:.2f} out of pocket after the refund."
