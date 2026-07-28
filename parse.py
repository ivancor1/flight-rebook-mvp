"""Turn the message the airline sent you into structured disruption fields.

The magic step: a stranded traveler pastes the cancellation text/email and we
extract the flight, airports, party size, what they paid, and the rebooking the
airline put them on - so they don't have to hand-type any of it.

Dependency-free on purpose (urllib, not the openai SDK) so `python3 app.py`
still runs with no pip install. Model: gpt-5.4-nano at low reasoning effort -
the fastest, cheapest tier that reliably extracts the fields AND leaves the fare
null when it isn't stated (it must never invent a refund basis).
"""
import json
import time
import urllib.error
import urllib.request

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-5.4-nano"

SYSTEM = (
    "You extract structured data from an airline flight-disruption message "
    "(a cancellation text, email, or the traveler describing it). Return ONLY JSON. "
    "Rules: use IATA airport codes (San Jose -> SJC, Newark -> EWR, etc.). "
    "Use null for anything not stated and not confidently inferable. "
    "party_size: count the travelers ('me and my wife' = 2, 'all 4 of you' = 4). "
    "total_paid: the TOTAL fare for the whole party in USD; if a per-person amount is "
    "given, multiply by party_size. NEVER invent a fare - if no amount is stated, "
    "total_paid MUST be null. Times are local wall-clock 'YYYY-MM-DDTHH:MM'. "
    "airline_rebooking is what the airline already put them on (null if none mentioned). "
    "wants_destination is where they actually want to end up (a city or metro is fine). "
    'Shape: {"original_flight":str|null,"original_origin":str|null,"original_destination":str|null,'
    '"original_depart_local":str|null,"original_arrive_local":str|null,"cause":str|null,'
    '"party_size":int|null,"pnrs":[str],"total_paid":number|null,'
    '"airline_rebooking":{"final_arrive_local":str|null,"origin":str|null,"destination":str|null,'
    '"segments":[{"flight":str,"origin":str,"destination":str,"depart_local":str,"arrive_local":str}]}|null,'
    '"wants_destination":str|null}'
)


def parse_disruption(text: str, key: str, model: str = MODEL, today: str = None) -> dict:
    """Call OpenAI to extract disruption fields from free text. Raises on hard errors."""
    system = SYSTEM
    if today:
        system += (f" Today's date is {today}. Resolve relative words like 'today', 'tonight', "
                   f"'this morning', and 'tomorrow' to absolute YYYY-MM-DD dates.")
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "response_format": {"type": "json_object"},
        "reasoning_effort": "low",
    }).encode()
    req = urllib.request.Request(OPENAI_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key.strip()}",
        "Content-Type": "application/json",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            return json.loads(data["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2.0 ** attempt)
                continue
            raise RuntimeError(f"OpenAI {exc.code}: {exc.read().decode()[:300]}")
    return {}
