# Rebook

When an airline cancels your flight, its rebooking engine is working for the airline, not for you.
It only searches its own metal, only out of the airport printed on your ticket, and it does not care
that four of you are travelling together on three different record locators. That is how a 1:30pm
SFO-JFK turns into a 4:25pm out of San Jose, a connection in Phoenix, and a 5:47am arrival.

This app does the search the airline won't: every airport in both metro areas, every carrier in the
data, seats confirmed for the whole party on every leg, ranked by when you actually get to your
door - and it puts a dollar figure on the option the airline never mentions, which is taking the
cash refund you are owed and buying a ticket on someone else.

## Run it

Python 3.9+. Standard library only - no pip install, no node_modules, no API keys, no network.

```bash
git clone <your repo url>
cd flight-rebook-mvp
python3 app.py
# open http://localhost:8000
```

Options: `python3 app.py --port 8080`. Tests: `python3 -m unittest -v`.

## What it does

The six things that actually matter, each implemented and each testable:

1. **Both ends are metro areas, not airports.** SJC, SFO and OAK are one origin; JFK, EWR and LGA
   are one destination. Most of the win lives in the airport the airline will never offer you.
   (`engine.expand`)
2. **Connections have to actually connect.** Onward legs are built from the arrival time of the
   first leg, with a minimum layover of 45 minutes on one carrier and 90 across two, and a 6-hour
   ceiling. A leg that departs before you land is rejected instead of quietly shown.
3. **Party size is a hard filter on every segment.** Not seat 1 - all of them. An itinerary that
   has 6 seats on the first leg and 2 on the second does not exist for a party of 4. Those show up
   in their own "died on party size" section, because knowing *why* an option collapsed is the
   difference between arguing with an agent and moving on.
4. **Separate PNRs are first-class.** The party is a list of passengers each with their own record
   locator, so the app can show that the airline is free to rebook them independently - which is
   exactly how groups get split across two days.
5. **Ranked by arrival, not departure.** Every option carries a door-to-door number: ground time to
   the origin airport, 60 minutes at the airport, flight and layover time, then ground time from the
   arrival airport. A flight that leaves 3 hours later and lands 2 hours earlier is a better flight.
6. **The refund is part of the comparison.** See below.

## The refund math

Under the US DOT automatic refund rule, a cancelled flight means a passenger holding a nonrefundable
ticket who declines the rebooking - and declines any voucher - is owed a full cash refund of the
fare, taxes and ancillary fees, in the original form of payment.

> "A covered carrier that is the merchant of record must provide a full and prompt refund of the
> airfare, including any taxes and ancillary fees ... to a consumer that holds a nonrefundable ticket
> on a scheduled flight to, from, or within the United States for any cancelled flight ... where the
> consumer chooses not to: (i) Fly on the significantly delayed or changed flight or accept rebooking
> on an alternative flight; or (ii) Accept any voucher, credit, or other form of compensation"
> - [14 CFR 260.6(a)(1)](https://www.law.cornell.edu/cfr/text/14/260.6)

"Prompt" is defined in [14 CFR 260.2](https://www.law.cornell.edu/cfr/text/14/260.2) as 7 business
days for credit-card purchases and 20 calendar days for other payment methods. The same section
defines a "significantly delayed or changed flight": 3+ hours domestic (6+ international) on either
end, a different origin or destination airport, or added connection points - which is why the
Phoenix redeye in the demo scenario trips three separate triggers at once.

So every option gets the comparison the airline will never draw for you:

```
new tickets (party)  -  refund you're owed  =  net out of pocket, against hours saved
```

Sometimes the refund covers the replacement outright. `entitlement.py` holds all of this, with the
citations inline. It is not legal advice, and it only covers flights to, from, or within the US.

Caveats the code states out loud: weather cancellations are still refundable (what weather removes
is hotel and meal coverage, which US carriers owe only for cancellations within their control),
accepting the rebooking or a voucher gives up the refund, and the refund is what you paid - not what
the replacement costs.

## The data problem

This is the honest part, and it is the real moat question for the product.

**This MVP runs on a seeded fixture dataset** (`fixtures.json`): 20 real-looking segments for
2026-07-28 out of the Bay Area to New York, modelled on an actual American cancellation. Times and
flight numbers are plausible; **seat counts and fares are invented**. Nothing in here is live
availability, and the app never claims otherwise - the source and its limits are printed in the
footer of the UI and on the console at startup.

Everything the engine needs comes through the `FlightSource` interface in `sources.py`, so a real
feed is a drop-in replacement. What that costs:

| Source | Gives you | Problem |
| --- | --- | --- |
| GDS / airline NDC (Amadeus, Sabre, Travelport) | True bookable inventory at a given party size | Commercial agreement, not self-serve |
| Aggregator APIs (Duffel, Amadeus Self-Service, Kiwi) | Bookable offers, self-serve signup | Partial carrier coverage; seat counts often capped at "9" instead of the truth |
| Schedule feeds (OAG, Cirium) | What flies where and when | Never tells you whether 4 seats exist |
| Scraping airline sites | Everything | Fragile, and generally against terms of service |

Party-size-accurate availability is the whole product. Search that can't tell you whether the leg
has four seats is the thing that already failed this traveller once.

`LiveSource` in `sources.py` is a deliberate `NotImplementedError` with those notes attached. There
is no fake API key anywhere in this repo.

## Layout

```
app.py           stdlib HTTP server + JSON API
engine.py        metro expansion, connection building, party-size filter, ranking
entitlement.py   DOT refund entitlement and the refund-vs-rebook comparison
models.py        Airport, Segment, Option, Passenger, Disruption
sources.py       FlightSource interface, FixtureSource, LiveSource stub
fixtures.json    the seeded dataset and the demo cancellation
index.html       single-page UI
style.css
app.js
test_engine.py   10 tests over the six strategies above
```

API, if you want to drive it directly:

```bash
curl localhost:8000/api/scenario
curl -X POST localhost:8000/api/search \
  -d '{"origin":"SJC","destination":"NYC","party_size":4,"depart_after_local":"2026-07-28T11:00"}'
```

## What's next

- Real inventory behind `FlightSource`, starting with one aggregator to prove the flow end to end.
- Split-party handling: when no single itinerary fits everyone, find the best 2+2 rather than giving up.
- Read the cancellation from the airline email or the PNR instead of a hand-entered scenario.
- Ground truth for airport transfer times (traffic, transit) instead of the constants in `fixtures.json`.
- Baggage and elite-status effects on whether the switch is actually worth it.
- Carrier-specific refund request flows, since being owed a refund and getting one are different things.
