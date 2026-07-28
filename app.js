const $ = (s) => document.querySelector(s);

// Everything on this page is built as an HTML string, and almost none of it is
// ours: carrier names and logo URLs come from the live inventory feed, the
// rejected/verdict strings are built server-side, and the disruption fields come
// out of a language model reading whatever the airline (or the user) pasted in.
// So every interpolation of a value we did not write goes through esc().
const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => (v === null || v === undefined) ? "" : String(v).replace(/[&<>"']/g, (c) => ESCAPES[c]);

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

const httpUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u)) ? u : "";
const carrierLogo = (seg) => (seg && httpUrl(seg.logo))
  ? `<img class="logo" src="${esc(httpUrl(seg.logo))}" alt="" referrerpolicy="no-referrer" data-carrier="${esc(seg.carrier)}">`
  : `<div class="logo mono">${esc((seg && seg.carrier) || "✈")}</div>`;

// A broken logo URL falls back to the carrier code. Image error events don't
// bubble, so listen in the capture phase - this replaces an inline onerror=""
// attribute that interpolated the carrier code straight into markup.
document.addEventListener("error", (ev) => {
  const img = ev.target;
  if (!(img instanceof HTMLImageElement) || !img.classList.contains("logo")) return;
  const badge = document.createElement("div");
  badge.className = "logo mono";
  badge.textContent = img.dataset.carrier || "✈";
  img.replaceWith(badge);
}, true);

let PARSED = null;   // fields from the last "Read it", or null (fixture demo)
let PARTY = 4;       // current party size, for card labels

// --- the "What happened" panel, from the fixture or the user's own paste ---
const WHAT_HAPPENED = { cancelled: "was cancelled", delayed: "was delayed", changed: "was changed" };

function renderScenario(d, offer, e) {
  PARTY = d.party_size || PARTY;
  const paid = d.total_paid ? `, ${money(d.total_paid)} paid in total` : "";
  const pnrs = d.pnrs && d.pnrs.length ? ` across ${d.pnrs.length} record locator(s) (${esc(d.pnrs.join(", "))})` : "";
  const offerHtml = offer ? `
    <div class="offer-card">
      <div class="row">
        <div><b>What the airline put you on</b>
          <div class="muted">${offer.segments.map((s) => esc(`${s.carrier}${s.number} ${s.origin}-${s.destination}`)).join("  →  ")}</div>
        </div>
        <div class="lands"><span>lands</span><b>${clock(offer.arrive_local)}${dayOffset(offer.depart_local, offer.arrive_local) ? " +" + dayOffset(offer.depart_local, offer.arrive_local) : ""}</b></div>
      </div>
    </div>` : `<p class="muted">The airline hasn't offered you a replacement (or the message didn't say). Everything below is what you could take instead.</p>`;
  let refundHtml;
  if (e && e.refund_state === "known") {
    refundHtml = `<div class="refund">You're owed <b>${money(e.refund_amount)}</b> back if you decline the rebooking and any voucher. ${esc(e.prompt_refund)}</div>`;
  } else if (e && e.refund_state === "unknown") {
    refundHtml = `<div class="refund">You're owed a full refund of what you paid if you decline the rebooking and any voucher. Enter the total above to see the amount. ${esc(e.prompt_refund)}</div>`;
  } else if (e && e.refund_state === "conditional" && e.refund_if_cancelled != null) {
    refundHtml = `<div class="refund">If it was cancelled - your message didn't say - you're owed <b>${money(e.refund_if_cancelled)}</b> back when you decline the rebooking and any voucher. ${esc(e.prompt_refund)}</div>`;
  } else if (e) {
    refundHtml = `<p class="muted">${esc(e.basis)}</p>`;
  } else {
    refundHtml = `<p class="muted">Enter what you paid above to see the refund you're owed if you walk away.</p>`;
  }
  $("#scenario-body").className = "";
  $("#scenario-body").innerHTML = `
    <p class="big"><b>${esc(d.original_flight)} ${esc(d.origin || "?")}-${esc(d.destination || "?")}</b> ${WHAT_HAPPENED[d.disruption_type] || "was disrupted"}${d.cause ? ` (${esc(d.cause)})` : ""}. Party of ${esc(d.party_size)}${pnrs}${paid}.</p>
    ${offerHtml}${refundHtml}`;
}

function nowLocal() {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

// The date the search is anchored to: the offline demo runs on the scenario clock
// the server hands back, a live source runs on your own clock.
const searchDate = () => {
  const v = $("#search-form").depart_after_local.value;
  return (v && v.includes("T") ? v : nowLocal()).split("T")[0];
};
const at = (hhmm) => `${searchDate()}T${hhmm}`;

// The fixtures are seeded for one scenario day and shifted onto today, so the
// offline demo has to search from the scenario clock, not the wall clock -
// otherwise every seeded flight is already in the past by dinner time.
async function prefillDepartAfter() {
  const f = $("#search-form");
  f.depart_after_local.value = nowLocal();
  try {
    const s = await (await fetch("/api/scenario")).json();
    if (s && s.uses_demo_clock && s.now_local) f.depart_after_local.value = s.now_local;
  } catch (err) { /* wall clock is a fine fallback */ }
}

// Push parsed disruption fields into the form (from the model, or from a sample).
function applyFields(fields) {
  PARSED = fields;
  const f = $("#search-form");
  if (PARSED.original_origin) f.origin.value = PARSED.original_origin;
  if (PARSED.original_destination || PARSED.wants_destination)
    f.destination.value = PARSED.original_destination || PARSED.wants_destination;
  if (PARSED.party_size) f.party_size.value = PARSED.party_size;
  if (PARSED.total_paid) f.total_paid.value = PARSED.total_paid;
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
    applyFields(r.fields);
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
  const flightNums = o.segments.map((s) => esc(`${s.carrier}${s.number}`)).join(" · ");
  const span = o.door_to_door_minutes - o.ground_to_origin_minutes - 60 - o.ground_from_destination_minutes;
  const d1 = dayOffset(o.depart_local, o.arrive_local);
  const stops = o.segments.length > 1
    ? `<span class="stops one">1 stop ${esc(o.segments[0].destination)}</span>`
    : `<span class="stops direct">Direct</span>`;

  const net = c.net_out_of_pocket;
  const netPill = (!o.rejected_reason && net !== undefined && net !== null)
    ? ((c.refund_state === "unknown" || c.refund_state === "conditional")
        ? `<span class="netpill cost">${money(net)} before refund</span>`
        : net <= 0
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
    chips.push(`<span class="chip">${esc(o.origin)}→${esc(o.destination)}, not your ticketed airports</span>`);
  if (o.self_connection) chips.push(`<span class="chip warn">separate tickets, bags not through-checked</span>`);
  if (o.rejected_reason) chips.push(`<span class="chip bad">${esc(o.rejected_reason)}</span>`);

  return `<div class="fcard ${best ? "best" : ""} ${o.rejected_reason ? "dead" : ""}">
    ${best ? '<div class="best-tag">BEST · LANDS SOONEST</div>' : ""}
    <div class="fmain">
      <div class="fcarrier">${carrierLogo(seg0)}<div class="cn"><b>${esc(seg0.carrier_name || seg0.carrier || "Flight")}</b><small>${flightNums}</small></div></div>
      <div class="ftimes">
        <div class="t"><b>${clock(o.depart_local)}</b><span>${esc(o.origin)}</span></div>
        <div class="fmid">
          <div class="dur">${dur(span)}</div>
          <div class="track">${o.segments.length > 1 ? '<span class="dot"></span>' : ""}</div>
          ${stops}
        </div>
        <div class="t"><b>${clock(o.arrive_local)}${d1 ? `<sup>+${d1}</sup>` : ""}</b><span>${esc(o.destination)}</span></div>
      </div>
      <div class="fprice"><b>${dollars(o.price_per_person)}</b><span>per person</span>${netPill}</div>
    </div>
    <div class="fmeta">${chips.join("")}</div>
    ${(!o.rejected_reason && c.verdict) ? `<p class="verdict ${c.hours_earlier_than_airline_offer > 0 ? "" : "flat"}">${esc(c.verdict)}</p>` : ""}
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
  if (r.error) { $("#results").innerHTML = `<p class="searched">Error: ${esc(r.error)}</p>`; return; }

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
  const head = `<div class="results-head"><div class="count"><b>${esc(total)}</b> option${total === 1 ? "" : "s"} that fit all ${esc(r.query.party_size)}</div>${total > shown ? `<div class="sub">showing the ${esc(shown)} best by arrival</div>` : ""}</div>`;
  $("#results").innerHTML = head +
    (shown ? r.options.map((o, i) => optionCard(o, i === 0)).join("")
           : `<p class="searched">Nothing bookable for all ${esc(r.query.party_size)} in this window.</p>`);

  const deadShown = r.collapsed_on_party_size.length, deadTotal = r.total_collapsed_found ?? deadShown;
  $("#collapsed").innerHTML = deadShown
    ? `<div class="results-head"><div class="count">Died on party size${deadTotal > deadShown ? ` <span class="sub">(${esc(deadTotal)}, showing ${esc(deadShown)})</span>` : ""}</div></div>
       <p class="searched">These come back when you search for one seat and vanish the moment you ask for all ${esc(r.query.party_size)} on one booking. Airlines sell seats in fare buckets. Live purchase inventory (the best proxy for the airline's own rebook tool), not a read of your reservation.</p>` +
      r.collapsed_on_party_size.map((o) => optionCard(o, false)).join("")
    : "";
}

// One-click sample cancellations, so you can see the whole flow without typing
// anything. `fields` is the JSON the model returns for that text, shipped with the
// sample - the chips run the full app with no OpenAI key. The /api/parse call is
// for text you paste yourself.
const EXAMPLES = [
  { label: "🌩 American · SFO→JFK · weather · 4 travelers",
    text: "American Airlines: Flight AA1642 from San Francisco (SFO) to New York JFK today has been cancelled due to weather. We've rebooked all 4 of you on AA512 SFO to Phoenix, then AA1188 Phoenix to Newark (EWR) arriving 11:59pm tonight. Confirmation KQ7T2R. You paid $402 per ticket.",
    fields: () => ({
      original_flight: "AA1642", original_origin: "SFO", original_destination: "JFK",
      original_depart_local: null, original_arrive_local: null,
      cause: "weather", disruption_type: "cancelled",
      party_size: 4, pnrs: ["KQ7T2R"], total_paid: 1608,
      airline_rebooking: {
        final_arrive_local: at("23:59"), origin: "SFO", destination: "EWR",
        segments: [
          { flight: "AA512", origin: "SFO", destination: "PHX", depart_local: null, arrive_local: null },
          { flight: "AA1188", origin: "PHX", destination: "EWR", depart_local: null, arrive_local: at("23:59") },
        ],
      },
      wants_destination: "New York",
    }) },
  { label: "🔧 United · SJC→EWR · mechanical · 2 travelers",
    text: "United: your flight UA1704 from San Jose (SJC) to Newark (EWR) this morning was cancelled (mechanical). They rebooked me and my wife through Denver, landing close to midnight. Confirmation MW9J4L. We paid $565 each.",
    fields: () => ({
      original_flight: "UA1704", original_origin: "SJC", original_destination: "EWR",
      original_depart_local: null, original_arrive_local: null,
      cause: "mechanical", disruption_type: "cancelled",
      party_size: 2, pnrs: ["MW9J4L"], total_paid: 1130,
      airline_rebooking: {
        final_arrive_local: at("23:50"), origin: "SJC", destination: "EWR", segments: [],
      },
      wants_destination: "Newark",
    }) },
  { label: "✈️ Delta · SFO→JFK · no rebooking · solo",
    text: "Delta: Flight DL1290 SFO to New York JFK today was cancelled. We were unable to rebook you automatically. You paid $498.",
    fields: () => ({
      original_flight: "DL1290", original_origin: "SFO", original_destination: "JFK",
      original_depart_local: null, original_arrive_local: null,
      cause: null, disruption_type: "cancelled",
      party_size: 1, pnrs: [], total_paid: 498,
      airline_rebooking: null, wants_destination: "New York",
    }) },
  { label: "🌩 JetBlue · OAK→JFK · weather · 3 travelers",
    text: "JetBlue: flight B6 1078 from Oakland (OAK) to New York JFK today is cancelled due to weather. Party of 3. Confirmation PB3X8D. You paid $389 each.",
    fields: () => ({
      original_flight: "B61078", original_origin: "OAK", original_destination: "JFK",
      original_depart_local: null, original_arrive_local: null,
      cause: "weather", disruption_type: "cancelled",
      party_size: 3, pnrs: ["PB3X8D"], total_paid: 1167,
      airline_rebooking: null, wants_destination: "New York",
    }) },
];

async function useExample(i) {
  const ex = EXAMPLES[i];
  $("#paste-box").value = ex.text;
  $("#paste-status").textContent = "Sample cancellation - already read for you.";
  applyFields(ex.fields());
  await search();
}

function renderExamples() {
  const box = $("#examples");
  box.innerHTML = EXAMPLES.map((e, i) => `<button type="button" class="ex-chip" data-i="${i}">${esc(e.label)}</button>`).join("");
  box.querySelectorAll(".ex-chip").forEach((btn) => btn.addEventListener("click", () => useExample(Number(btn.dataset.i))));
}

$("#search-form").addEventListener("submit", search);
$("#paste-btn").addEventListener("click", readMessage);
renderExamples();
$("#results").innerHTML = `<p class="searched">Paste your cancellation above, or tap a sample, to see your real options.</p>`;
prefillDepartAfter();
