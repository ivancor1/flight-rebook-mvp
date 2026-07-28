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


def _safe_parse(ts):
    """Parse a local time string, or None if it's missing/malformed (real user
    input from the parser won't always carry a full timestamp)."""
    try:
        return parse(ts)
    except (TypeError, ValueError):
        return None

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

    orig_arr = _safe_parse(disruption.original_arrive_local)
    new_arr = _safe_parse(offer.segments[-1].arrive_local)
    if orig_arr and new_arr:
        late_hours = (new_arr - orig_arr).total_seconds() / 3600.0
        if late_hours >= threshold:
            reasons.append(f"Arrives {late_hours:.1f}h later than the flight you bought (threshold {threshold}h).")

    orig_dep = _safe_parse(disruption.original_depart_local)
    new_dep = _safe_parse(offer.segments[0].depart_local)
    if orig_dep and new_dep:
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


def _basis(disruption, cancelled, change) -> str:
    """Why the refund is (or isn't) owed - in the words the rule actually uses."""
    if cancelled:
        return (
            "The flight was cancelled. Under 14 CFR 260.6(a)(1) a passenger holding a "
            "nonrefundable ticket who declines the rebooking and declines any voucher "
            "is owed a full refund of the fare, taxes and ancillary fees."
        )
    if change["significant"]:
        return (
            "The flight was not cancelled, but the replacement itinerary is a "
            "significantly changed flight under 14 CFR 260.2 ("
            + "; ".join(r.rstrip(".") for r in change["reasons"]) +
            "). Under 14 CFR 260.6(a)(1) a passenger holding a nonrefundable ticket who "
            "declines a significantly changed flight, declines rebooking and declines any "
            "voucher is owed a full refund of the fare, taxes and ancillary fees."
        )
    if not disruption.disruption_type:
        return (
            "Nothing here says the flight was cancelled, so no refund is established yet. "
            "IF it was cancelled - or the replacement is a significantly changed flight "
            "under 14 CFR 260.2 - then 14 CFR 260.6(a)(1) owes you the full fare, taxes and "
            "ancillary fees when you decline the rebooking and any voucher."
        )
    return (
        f"No automatic refund under 14 CFR 260.6 on what we know: this is a "
        f"{disruption.disruption_type} flight, not a cancellation, and the change to the "
        f"itinerary is under the {change['threshold_hours']}-hour threshold in 14 CFR 260.2, "
        "with no different airport and no added connection. You can still ask the airline, "
        "and a refundable fare is refundable regardless."
    )


def refund_entitlement(disruption, offer, airports) -> dict:
    """What the passengers are owed if they walk away from the rebooking."""
    cancelled = disruption.is_cancellation
    change = significant_change(disruption, offer, airports)
    # "No alternative was offered" only establishes a refund when the flight actually
    # went away. With no offer to look at and no cancellation on record, we know nothing.
    changed = change["significant"] and offer is not None
    entitled = cancelled or changed
    # A fare of 0.0 means nobody told us what the ticket cost. Reporting that as a
    # $0 refund reads like "you get nothing"; it has to stay unknown.
    known = disruption.fare_known
    amount = disruption.total_paid if (entitled and known) else None
    if entitled:
        state = "known" if known else "unknown"
    elif not disruption.disruption_type:
        # We were never told what happened. Don't claim a refund - but don't hide the
        # number either; say what it would be if the flight was in fact cancelled.
        state = "conditional"
    else:
        state = "none"
    return {
        "entitled": entitled,
        "basis": _basis(disruption, cancelled, change if changed else {**change, "significant": False}),
        "citation": CITATION,
        "prompt_refund": PROMPT_REFUND,
        "refund_amount": amount,
        "refund_if_cancelled": disruption.total_paid if known else None,
        # "known" | "unknown" (owed, no fare given) | "conditional" (we weren't told
        # what happened) | "none" (told, and it isn't a refundable event)
        "refund_state": state,
        "per_passenger": [
            {"name": p.name, "pnr": p.pnr, "refund": (p.fare_paid if (entitled and known) else None)}
            for p in disruption.passengers
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
    refund = ent["refund_amount"] or 0.0
    net = round(new_cost - refund, 2)
    minutes_saved = engine.better_than(option, offer)
    hours_saved = round(minutes_saved / 60.0, 1) if minutes_saved is not None else None
    dollars_per_hour = None
    if hours_saved and hours_saved > 0 and net > 0:
        dollars_per_hour = round(net / hours_saved, 2)
    return {
        "option_id": "-".join(s.flight for s in option.segments),
        "new_ticket_total": new_cost,
        "refund_if_you_decline": ent["refund_amount"],
        "refund_state": ent["refund_state"],
        "net_out_of_pocket": net,   # before the refund when refund_state is "unknown"
        "hours_earlier_than_airline_offer": hours_saved,
        "net_cost_per_hour_saved": dollars_per_hour,
        "verdict": _verdict(net, hours_saved, ent["refund_state"]),
        "entitlement": ent,
    }


def _verdict(net: float, hours_saved, refund_state: str = "known") -> str:
    if hours_saved is None:
        return "No airline offer to compare against."
    if hours_saved <= 0:
        return "No better than what the airline already gave you."
    if refund_state == "unknown":
        return (f"Gets you there {hours_saved}h earlier for ${net:.2f} up front, before the refund "
                "you're owed - enter what you paid to see what it nets out to.")
    if refund_state == "conditional":
        return (f"Gets you there {hours_saved}h earlier for ${net:.2f} up front, before any refund "
                "you turn out to be owed.")
    if refund_state == "none":
        return f"Gets you there {hours_saved}h earlier for ${net:.2f}, with no refund owed on this one."
    if net <= 0:
        return f"Gets you there {hours_saved}h earlier and the refund covers it with ${abs(net):.2f} left over."
    return f"Gets you there {hours_saved}h earlier for ${net:.2f} out of pocket after the refund."
