const $ = (s) => document.querySelector(s);

const clock = (iso) => {
  if (!iso || !iso.includes("T")) return "-";
  let [hh, mm] = iso.split("T")[1].split(":").map(Number);
  const ap = hh >= 12 ? "pm" : "am";
  hh = hh % 12 === 0 ? 12 : hh % 12;
  return `${hh}:${String(mm).padStart(2, "0")}${ap}`;
};
const dayOffset = (dep, arr) => {
  if (!dep || !arr) return 0;
  const dd = dep.split("T")[0], ad = arr.split("T")[0];
  if (dd === ad) return 0;
  return Math.round((Date.parse(ad + "T00:00") - Date.parse(dd + "T00:00")) / 86400000);
};
const dur = (mins) => `${Math.floor(Math.max(mins, 0) / 60)}h ${Math.max(mins, 0) % 60}m`;
const dollars = (n) => (n === null || n === undefined) ? "-" : "$" + Math.round(n).toLocaleString();
const money = (n) => {
  if (n === null || n === undefined) return "-";
  const s = n < 0 ? "-" : "";
  return `${s}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

window.logoFallback = (img, code) => { img.outerHTML = `<div class="logo mono">${code || "✈"}</div>`; };
const carrierLogo = (seg) => seg && seg.logo
  ? `<img class="logo" src="${seg.logo}" alt="" referrerpolicy="no-referrer" onerror="logoFallback(this,'${seg.carrier}')">`
  : `<div class="logo mono">${(seg && seg.carrier) || "✈"}</div>`;

let PARSED = null;   // fields from the last "Read it", or null (fixture demo)
let PARTY = 4;       // current party size, for card labels

// --- the "What happened" panel, from the fixture or the user's own paste ---
function renderScenario(d, offer, e) {
  PARTY = d.party_size || PARTY;
  const paid = d.total_paid ? `, ${money(d.total_paid)} paid in total` : "";
  const pnrs = d.pnrs && d.pnrs.length ? ` across ${d.pnrs.length} record locator(s) (${d.pnrs.join(", ")})` : "";
  const offerHtml = offer ? `
    <div class="offer-card">
      <div class="row">
        <div><b>What the airline put you on</b>
          <div class="muted">${offer.segments.map((s) => `${s.carrier}${s.number} ${s.origin}-${s.destination}`).join("  →  ")}</div>
        </div>
        <div class="lands"><span>lands</span><b>${clock(offer.arrive_local)}${dayOffset(offer.depart_local, offer.arrive_local) ? " +" + dayOffset(offer.depart_local, offer.arrive_local) : ""}</b></div>
      </div>
    </div>` : `<p class="muted">The airline hasn't offered you a replacement (or the message didn't say). Everything below is what you could take instead.</p>`;
  const refundHtml = e && e.entitled
    ? `<div class="refund">You're owed <b>${money(e.refund_amount)}</b> back if you decline the rebooking and any voucher. ${e.prompt_refund}</div>`
    : `<p class="muted">Enter what you paid above to see the refund you're owed if you walk away.</p>`;
  $("#scenario-body").className = "";
  $("#scenario-body").innerHTML = `
    <p class="big"><b>${d.original_flight} ${d.origin || "?"}-${d.destination || "?"}</b> was cancelled${d.cause ? ` (${d.cause})` : ""}. Party of ${d.party_size}${pnrs}${paid}.</p>
    ${offerHtml}${refundHtml}`;
}

function nowLocal() {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

// --- the magic: paste the airline message, let the model fill it in ---
async function readMessage() {
  const text = $("#paste-box").value.trim();
  const status = $("#paste-status");
  if (!text) { status.textContent = "Paste the airline's message first."; return; }
  const btn = $("#paste-btn");
  btn.disabled = true;
  status.textContent = "Reading it…";
  try {
    const r = await (await fetch("/api/parse", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    })).json();
    if (r.error) { status.textContent = `Couldn't read it: ${r.error}`; btn.disabled = false; return; }
    PARSED = r.fields;
    const f = $("#search-form");
    if (PARSED.original_origin) f.origin.value = PARSED.original_origin;
    if (PARSED.original_destination || PARSED.wants_destination)
      f.destination.value = PARSED.original_destination || PARSED.wants_destination;
    if (PARSED.party_size) f.party_size.value = PARSED.party_size;
    if (PARSED.total_paid) f.total_paid.value = PARSED.total_paid;
    status.textContent = "Got it, searching your real options…";
    await search();
    status.textContent = "Filled in from your message. Edit anything and search again.";
  } catch (err) {
    status.textContent = `Couldn't read it: ${err}`;
  }
  btn.disabled = false;
}

function optionCard(o, best) {
  const c = o.comparison || {};
  const seg0 = o.segments[0] || {};
  const flightNums = o.segments.map((s) => `${s.carrier}${s.number}`).join(" · ");
  const span = o.door_to_door_minutes - o.ground_to_origin_minutes - 60 - o.ground_from_destination_minutes;
  const d1 = dayOffset(o.depart_local, o.arrive_local);
  const stops = o.segments.length > 1
    ? `<span class="stops one">1 stop ${o.segments[0].destination}</span>`
    : `<span class="stops direct">Direct</span>`;

  const net = c.net_out_of_pocket;
  const netPill = (!o.rejected_reason && net !== undefined)
    ? (net <= 0
        ? `<span class="netpill win">refund covers it +${money(-net)}</span>`
        : `<span class="netpill cost">net ${money(net)}</span>`)
    : "";

  const chips = [];
  chips.push(`<span class="chip ground">leave by ${clock(o.leave_by_local)}</span>`);
  chips.push(`<span class="chip ground">door to door ${dur(o.door_to_door_minutes)}</span>`);
  if (!o.rejected_reason) chips.push(`<span class="chip win">confirmed for all ${PARTY}</span>`);
  if (o.minutes_earlier_than_offer > 0)
    chips.push(`<span class="chip win">${(o.minutes_earlier_than_offer / 60).toFixed(1)}h earlier than the airline</span>`);
  if (o.different_airport_than_ticketed)
    chips.push(`<span class="chip">${o.origin}→${o.destination}, not your ticketed airports</span>`);
  if (o.self_connection) chips.push(`<span class="chip warn">separate tickets, bags not through-checked</span>`);
  if (o.rejected_reason) chips.push(`<span class="chip bad">${o.rejected_reason}</span>`);

  return `<div class="fcard ${best ? "best" : ""} ${o.rejected_reason ? "dead" : ""}">
    ${best ? '<div class="best-tag">BEST · LANDS SOONEST</div>' : ""}
    <div class="fmain">
      <div class="fcarrier">${carrierLogo(seg0)}<div class="cn"><b>${seg0.carrier_name || seg0.carrier || "Flight"}</b><small>${flightNums}</small></div></div>
      <div class="ftimes">
        <div class="t"><b>${clock(o.depart_local)}</b><span>${o.origin}</span></div>
        <div class="fmid">
          <div class="dur">${dur(span)}</div>
          <div class="track">${o.segments.length > 1 ? '<span class="dot"></span>' : ""}</div>
          ${stops}
        </div>
        <div class="t"><b>${clock(o.arrive_local)}${d1 ? `<sup>+${d1}</sup>` : ""}</b><span>${o.destination}</span></div>
      </div>
      <div class="fprice"><b>${dollars(o.price_per_person)}</b><span>per person</span>${netPill}</div>
    </div>
    <div class="fmeta">${chips.join("")}</div>
    ${(!o.rejected_reason && c.verdict) ? `<p class="verdict ${c.hours_earlier_than_airline_offer > 0 ? "" : "flat"}">${c.verdict}</p>` : ""}
  </div>`;
}

function disruptionFromForm(f) {
  const paid = f.total_paid.value ? Number(f.total_paid.value) : null;
  const party = Number(f.party_size.value);
  if (PARSED) return { ...PARSED, party_size: party, total_paid: paid !== null ? paid : PARSED.total_paid };
  if (paid) return { party_size: party, total_paid: paid, original_origin: f.origin.value.trim(), original_destination: f.destination.value.trim() };
  return null;  // nothing entered -> backend uses the fixture demo
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
  const disruption = disruptionFromForm(f);
  if (disruption) body.disruption = disruption;

  $("#results").innerHTML = `<p class="searched">Searching real inventory…</p>`;
  $("#collapsed").innerHTML = "";
  const r = await (await fetch("/api/search", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  })).json();
  if (r.error) { $("#results").innerHTML = `<p class="searched">Error: ${r.error}</p>`; return; }

  if (r.data_source) $("#source-note").textContent = `Data source: ${r.data_source.name}. ${r.data_source.note}`;
  if (r.disruption) {
    renderScenario(r.disruption, r.airline_offer, r.entitlement);
    $("#scenario").style.display = "";
  } else {
    $("#scenario").style.display = "none";
  }
  $("#searched").textContent =
    `Searched ${r.airports_searched.origins.join(", ")} → ${r.airports_searched.destinations.join(", ")} for ${r.query.party_size} passengers.`;

  const shown = r.options.length, total = r.total_options_found ?? shown;
  const head = `<div class="results-head"><div class="count"><b>${total}</b> option${total === 1 ? "" : "s"} that fit all ${r.query.party_size}</div>${total > shown ? `<div class="sub">showing the ${shown} best by arrival</div>` : ""}</div>`;
  $("#results").innerHTML = head +
    (shown ? r.options.map((o, i) => optionCard(o, i === 0)).join("")
           : `<p class="searched">Nothing bookable for all ${r.query.party_size} in this window.</p>`);

  const deadShown = r.collapsed_on_party_size.length, deadTotal = r.total_collapsed_found ?? deadShown;
  $("#collapsed").innerHTML = deadShown
    ? `<div class="results-head"><div class="count">Died on party size${deadTotal > deadShown ? ` <span class="sub">(${deadTotal}, showing ${deadShown})</span>` : ""}</div></div>
       <p class="searched">These come back when you search for one seat and vanish the moment you ask for all ${r.query.party_size} on one booking. Airlines sell seats in fare buckets. Live purchase inventory (the best proxy for the airline's own rebook tool), not a read of your reservation.</p>` +
      r.collapsed_on_party_size.map((o) => optionCard(o, false)).join("")
    : "";
}

// one-click sample cancellations (today's date resolves server-side) so you can
// see the whole flow without typing anything
const EXAMPLES = [
  { label: "🌩 American · SFO→JFK · weather · 4 travelers",
    text: "American Airlines: Flight AA1642 from San Francisco (SFO) to New York JFK today has been cancelled due to weather. We've rebooked all 4 of you on AA512 SFO to Phoenix, then AA1188 Phoenix to Newark (EWR) arriving 11:59pm tonight. Confirmation KQ7T2R. You paid $402 per ticket." },
  { label: "🔧 United · SJC→EWR · mechanical · 2 travelers",
    text: "United: your flight UA1704 from San Jose (SJC) to Newark (EWR) this morning was cancelled (mechanical). They rebooked me and my wife through Denver, landing close to midnight. Confirmation MW9J4L. We paid $565 each." },
  { label: "✈️ Delta · SFO→JFK · no rebooking · solo",
    text: "Delta: Flight DL1290 SFO to New York JFK today was cancelled. We were unable to rebook you automatically. You paid $498." },
  { label: "🌩 JetBlue · OAK→JFK · weather · 3 travelers",
    text: "JetBlue: flight B6 1078 from Oakland (OAK) to New York JFK today is cancelled due to weather. Party of 3. Confirmation PB3X8D. You paid $389 each." },
];

function renderExamples() {
  const box = $("#examples");
  box.innerHTML = EXAMPLES.map((e, i) => `<button type="button" class="ex-chip" data-i="${i}">${e.label}</button>`).join("");
  box.querySelectorAll(".ex-chip").forEach((btn) => btn.addEventListener("click", () => {
    $("#paste-box").value = EXAMPLES[Number(btn.dataset.i)].text;
    readMessage();
  }));
}

$("#search-form").addEventListener("submit", search);
$("#paste-btn").addEventListener("click", readMessage);
$("#search-form").depart_after_local.value = nowLocal();
renderExamples();
$("#results").innerHTML = `<p class="searched">Paste your cancellation above, or tap a sample, to see your real options.</p>`;
