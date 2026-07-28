const $ = (s) => document.querySelector(s);

const fmtTime = (iso) => {
  const [d, t] = iso.split("T");
  const [y, m, day] = d.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, day));
  const wd = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][date.getUTCDay()];
  let [hh, mm] = t.split(":").map(Number);
  const ap = hh >= 12 ? "pm" : "am";
  hh = hh % 12 === 0 ? 12 : hh % 12;
  return `${wd} ${hh}:${String(mm).padStart(2, "0")}${ap}`;
};
const dur = (mins) => `${Math.floor(mins / 60)}h ${mins % 60}m`;
const money = (n) => {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

let SCENARIO = null;

async function loadScenario() {
  SCENARIO = await (await fetch("/api/scenario")).json();
  const d = SCENARIO.disruption, o = SCENARIO.airline_offer, e = SCENARIO.entitlement;
  $("#source-note").textContent = `Data source: ${SCENARIO.data_source.name}. ${SCENARIO.data_source.note}`;
  $("#scenario-body").className = "";
  $("#scenario-body").innerHTML = `
    <p><b>${d.original_flight} ${d.origin}-${d.destination}</b> was cancelled (${d.cause}).
    You were supposed to land ${fmtTime(d.arrive_local)}. Party of ${d.party_size} across
    ${d.pnrs.length} separate record locators (${d.pnrs.join(", ")}), ${money(d.total_paid)} paid in total.</p>
    <div class="card offer">
      <div class="row">
        <div class="route">What the airline gave you<br>
          <small>${o.segments.map((s) => `${s.carrier}${s.number} ${s.origin}-${s.destination}`).join("  →  ")}</small>
        </div>
        <div class="arrive"><span>lands</span><b>${fmtTime(o.arrive_local)}</b></div>
      </div>
      <p class="legs">Leaves from ${o.origin}, not the ${d.origin} you were ticketed from. Door to door ${dur(o.door_to_door_minutes)}.</p>
    </div>
    <p class="legs"><b>Refund position:</b> ${e.basis} That is ${money(e.refund_amount)} back if you walk away.
    ${e.prompt_refund}</p>
    <p class="legs">Why the offer itself is a "significant change" under the rule: ${e.airline_offer_is_significant_change.reasons.join(" ")}</p>`;
}

function optionCard(o, best) {
  const c = o.comparison || {};
  const badges = [];
  if (o.minutes_earlier_than_offer > 0)
    badges.push(`<span class="badge win">${(o.minutes_earlier_than_offer / 60).toFixed(1)}h earlier than the airline's option</span>`);
  if (o.different_airport_than_ticketed)
    badges.push(`<span class="badge">different airport: ${o.origin} → ${o.destination}</span>`);
  if (o.self_connection)
    badges.push(`<span class="badge warn">separate tickets, bags not through-checked</span>`);
  if (o.layover_minutes)
    badges.push(`<span class="badge">${dur(o.layover_minutes)} layover</span>`);
  badges.push(`<span class="badge">${o.max_party_supported} seats available</span>`);
  if (o.rejected_reason)
    badges.push(`<span class="badge bad">${o.rejected_reason}</span>`);

  const moneyRow = o.rejected_reason ? "" : `
    <div class="money">
      <div><span>new tickets (${c.new_ticket_total !== undefined ? SCENARIO.disruption.party_size : ""} pax)</span>${money(c.new_ticket_total)}</div>
      <div><span>refund if you decline</span>${money(c.refund_if_you_decline)}</div>
      <div><span>net out of pocket</span>${money(c.net_out_of_pocket)}</div>
      ${c.net_cost_per_hour_saved ? `<div><span>per hour saved</span>${money(c.net_cost_per_hour_saved)}</div>` : ""}
    </div>
    <p class="verdict">${c.verdict || ""}</p>`;

  return `<div class="card ${best ? "best" : ""} ${o.rejected_reason ? "dead" : ""}">
    <div class="row">
      <div class="route">${o.origin} → ${o.destination}
        <small>${o.segments.map((s) => `${s.carrier}${s.number} ${s.origin} ${fmtTime(s.depart_local)} → ${s.destination} ${fmtTime(s.arrive_local)}`).join("  ·  ")}</small>
      </div>
      <div class="arrive"><span>lands</span><b>${fmtTime(o.arrive_local)}</b></div>
    </div>
    <p class="legs">Leave by ${fmtTime(o.leave_by_local)} (${o.ground_to_origin_minutes}m to ${o.origin} + 60m at the airport) ·
      door to door ${dur(o.door_to_door_minutes)} · ${money(o.price_per_person)}/person</p>
    <div class="badges">${badges.join("")}</div>
    ${moneyRow}
  </div>`;
}

async function search(ev) {
  if (ev) ev.preventDefault();
  const f = $("#search-form");
  const body = {
    origin: f.origin.value.trim(),
    destination: f.destination.value.trim(),
    party_size: Number(f.party_size.value),
    depart_after_local: f.depart_after_local.value.trim(),
    include_self_connections: f.include_self_connections.checked,
  };
  $("#results").innerHTML = `<p class="hint">searching...</p>`;
  const r = await (await fetch("/api/search", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  })).json();
  if (r.error) { $("#results").innerHTML = `<p class="hint">error: ${r.error}</p>`; return; }

  $("#searched").textContent =
    `Searched ${r.airports_searched.origins.join(", ")} → ${r.airports_searched.destinations.join(", ")} for ${r.query.party_size} passengers.`;

  $("#results").innerHTML =
    `<h2>${r.options.length} option${r.options.length === 1 ? "" : "s"} that fit all ${r.query.party_size}</h2>` +
    (r.options.length ? r.options.map((o, i) => optionCard(o, i === 0)).join("") : `<p class="hint">Nothing beats the airline's offer in this data.</p>`);

  $("#collapsed").innerHTML = r.collapsed_on_party_size.length
    ? `<h2>Died on party size</h2><p class="hint">These would work for a smaller group. This is the trap: the flight looks bookable until you ask for ${r.query.party_size} seats on every leg.</p>` +
      r.collapsed_on_party_size.map((o) => optionCard(o, false)).join("")
    : "";
}

$("#search-form").addEventListener("submit", search);
loadScenario().then(search);
