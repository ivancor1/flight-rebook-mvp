# Rebook

**Find the flight the airline won't offer you.**

When an airline cancels your flight, its rebooking desk is working for the airline, not for you. It only searches its own metal, only out of the airport printed on your ticket, and it does not care that four of you are travelling together on three different record locators. That is how a 1:30pm SFO to JFK turns into a 4:25pm out of San Jose, a connection in Phoenix, and a 5:47am arrival.

Rebook does the search the airline won't: every airport in both metro areas, every carrier in live inventory, seats confirmed for your whole party on every leg, ranked by when you actually get to your door. Then it puts a dollar figure on the option the airline never mentions: take the cash refund you are owed and buy a ticket on someone else.

Paste the text message the airline sent you, and it fills in the rest.

---

## The bottom line

Under the US DOT automatic refund rule (14 CFR Part 260), a cancelled flight usually means you are owed a **full cash refund**, in your original form of payment, if you decline the rebooking. Rebook does the arithmetic the airline will never draw for you:

```
new tickets for your whole party  -  the refund you're owed  =  net out of pocket
```

Very often the refund more than covers a better flight. For example: the airline rebooks a family of four onto a 5:47am red-eye connecting through Phoenix. Rebook finds a nonstop that lands the night before, and shows that the refund covers the new tickets with money left over. Same destination, hours earlier, and it costs you nothing extra. That is the flight the airline had no reason to show you.

---

## What it does that a booking site does not

1. **Both ends are metro areas, not airports.** SFO, SJC, and OAK are one origin. JFK, EWR, and LGA are one destination. Most of the win lives in the airport the airline will never offer.
2. **Party size is a hard filter on every leg.** Airlines sell seats in fare buckets. A flight can look available to a solo searcher and vanish the instant you ask for four on one booking. Rebook searches at your real party size, and it surfaces the flights that "died on party size" so you know exactly why an option collapsed.
3. **Ranked by when you land, door to door.** Ground time to the airport, time at the airport, flight and layover, ground time from the arrival airport. A flight that leaves three hours later but lands two hours earlier is a better flight, and Rebook ranks it that way.
4. **The refund is part of every comparison** (see the bottom line above).
5. **Live, real, bookable inventory** across the carriers the provider covers, priced for your actual passengers.
6. **Paste and go.** Paste the airline's cancellation message and a language model pulls out your flight, party size, fare, and the rebooking they offered. The search then runs on your real numbers, no forms to fill.

---

## The hard part, and the moat

Party-size-accurate availability is the whole product. A search that cannot tell you whether four seats are actually sellable together is the search that already failed this traveller once.

Rebook gets it two ways: it queries live inventory at your true party size (which fails closed, so an offer existing is the guarantee that the seats are there), and it runs a second single-passenger search to catch the flights that look bookable right up until you ask for the group. That diff is the difference between arguing with a gate agent and knowing your options cold.

---

## Run it

Python 3.9+, standard library only. No pip install, no build step.

```bash
git clone https://github.com/ivancor1/flight-rebook-mvp
cd flight-rebook-mvp
python3 app.py            # then open http://localhost:8000
```

Out of the box it runs on a seeded fixture dataset: offline, deterministic, no keys. The fixtures shift onto the day you run them, so the demo works whatever date you clone it, and the four sample cancellations ship with their parsed fields, so they run with no OpenAI key either. To make it real, drop in your own credentials (both files are git-ignored and never leave your machine):

- **Live flights:** put a Duffel access token in `duffel_token.txt`. Free self-serve signup at duffel.com. Searching is free; you only pay if you book, which this app does not do.
- **Paste-to-parse:** put an OpenAI API key in `openai_key.txt`. Uses `gpt-5.4-nano`, a fraction of a cent per parse.

The keys stay server-side. Your browser only ever talks to your own local server.

Tests: `python3 -m unittest` (26 tests over the search, the fixture clock, and the refund logic).

---

## How it is built

Deliberately dependency-free: a standard-library Python server, a vanilla single-page front end, and real airline logos pulled from the live feed.

| File | What it does |
| --- | --- |
| `engine.py` | Metro expansion, connection building, per-leg party-size filter, door-to-door ranking |
| `sources.py` | `ReferenceData` (airports, metros, ground times) plus the `FlightSource` seam: `FixtureSource` (offline, owns the demo scenario) and `DuffelSource` (live, with the party-of-1 diff) |
| `entitlement.py` | US DOT refund entitlement and the refund-vs-rebook comparison, with citations inline |
| `parse.py` | Language-model extraction of a disruption from free text (OpenAI, `gpt-5.4-nano`) |
| `app.py` | Standard-library HTTP server and JSON API |
| `index.html`, `style.css`, `app.js` | Single-page UI |
| `models.py` | Airport, Segment, Option, Passenger, Disruption |
| `test_engine.py` | Tests over the six strategies above |

Swapping the data source is one file: the engine never talks to an API, it asks a `FlightSource`.

---

## Honest limits

- **Southwest** is not on any self-serve API and is not shown - not in the live source, and not in the fixtures either, because seeding a Southwest fare into the demo is exactly the faked inventory this line is promising not to do. The right answer is a deep-link handoff to southwest.com, which is not built yet.
- **Coverage** is currently the SF Bay Area to New York corridor (that is the metro reference data). Extending it is a matter of adding metro definitions.
- Rebook **finds and compares, it does not book.** It hands you to the airline to complete the purchase, which keeps it clear of ticket-agent and seller-of-travel obligations.
- Availability shown is live purchase inventory: the best proxy for what the airline's own rebook tool would offer, not a read of your existing reservation, and not a guarantee.

---

## Legal

The refund logic follows [14 CFR 260.6](https://www.law.cornell.edu/cfr/text/14/260.6) (US DOT automatic refund rule), with the citations inline in `entitlement.py`. This is not legal advice. It applies to flights to, from, or within the United States.
