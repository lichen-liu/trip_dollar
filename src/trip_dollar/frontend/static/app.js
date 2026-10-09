"use strict";

const $ = (id) => document.getElementById(id);
const form = $("ledger-form");
let revision = 0;
let result = null;
let errorLine = null;
let loading = false;
let paymentText = "";
const currencySources = {
  explicit: "Stated in notes",
  inherited: "Carried forward",
  initial_config: "Starting currency",
  override: "Correction",
  unknown: "Unknown",
};

// All financial values arrive as exact decimal strings or server-formatted text.
// DOM text, not HTML, is used for uploaded notes and participant names.
function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function changed() {
  revision += 1;
  $("error-box").hidden = true;
  $("copy-status").textContent = "";
  if (result) {
    $("stale-note").hidden = false;
    $("download-button").disabled = true;
    $("copy-button").disabled = true;
  }
  const lines = $("source").value ? $("source").value.split(/\r?\n/).length : 0;
  $("line-count").textContent = `${lines} ${lines === 1 ? "line" : "lines"}`;
  for (const row of $("participants").children) {
    const code = row.querySelector(".person-code").value || "this person";
    row
      .querySelector(".person-split")
      .setAttribute("aria-label", `Include ${code} in default split`);
  }
}

function addPerson(code = "", name = "", split = false) {
  const row = $("person-template").content.firstElementChild.cloneNode(true);
  row.querySelector(".person-code").value = code;
  row.querySelector(".person-name").value = name;
  row.querySelector(".person-split").checked = split;
  row.querySelector(".remove-person").addEventListener("click", () => {
    row.remove();
    $("add-person").disabled = false;
    changed();
    $("add-person").focus();
  });
  $("participants").append(row);
  // There are 52 case-sensitive ASCII letters; uppercase A is reserved.
  $("add-person").disabled = $("participants").children.length >= 51;
  return row;
}

addPerson();
addPerson();
$("add-person").addEventListener("click", () => {
  addPerson().querySelector(".person-code").focus();
  changed();
});
form.addEventListener("input", changed);
// Codes are case-sensitive in the existing parser: do not silently change them.
// Currency codes are case-insensitive and normalized by the server.

function showError(message, field = null, line = null) {
  $("error-message").textContent = message;
  $("error-box").hidden = false;
  errorLine = line;
  $("error-jump").hidden = !line;
  const id =
    { fx_date: "fx-date", initial_currency: "initial-currency" }[field] ||
    field;
  if (["fx", "fx-date", "initial-currency"].includes(id))
    $("rate-settings").open = true;
  const target =
    field === "participants" ? document.querySelector(".person-code") : $(id);
  if (target && target.focus) target.focus({ preventScroll: true });
  $("error-box").scrollIntoView({ block: "center" });
}

$("error-jump").addEventListener("click", () => {
  const source = $("source");
  const lines = source.value.split("\n");
  const start = lines
    .slice(0, errorLine - 1)
    .reduce((length, text) => length + text.length + 1, 0);
  source.focus();
  source.setSelectionRange(start, start + (lines[errorLine - 1] || "").length);
});

$("file-input").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 64000)
      throw new Error("Choose a text file smaller than 64 KB.");
    if (
      $("source").value.trim() &&
      !window.confirm("Replace the notes currently in the editor?")
    )
      return;
    const before = revision;
    const text = new TextDecoder("utf-8", { fatal: true }).decode(
      await file.arrayBuffer(),
    );
    if (before !== revision)
      throw new Error(
        "Your notes changed while loading the file. Upload it again to replace them.",
      );
    if (text.includes("\0"))
      throw new Error(
        "This looks like a binary file. Choose a UTF-8 text file.",
      );
    $("source").value = text;
    $("file-status").textContent = file.name;
    changed();
  } catch (error) {
    showError(
      error instanceof TypeError
        ? "Choose a UTF-8 text file, not a document or spreadsheet."
        : error.message,
    );
  } finally {
    event.target.value = "";
  }
});

$("example-button").addEventListener("click", async () => {
  const before = revision;
  $("example-button").disabled = true;
  try {
    const response = await fetch("/static/example.json");
    if (!response.ok) throw new Error("Could not load the example.");
    const example = await response.json();
    if (revision !== before) return;
    if (
      $("source").value.trim() &&
      !window.confirm("Replace your notes and settings with the example?")
    )
      return;
    $("source").value = example.source;
    $("participants").replaceChildren();
    for (const person of example.participants)
      addPerson(person.code, person.name, person.split);
    $("base").value = example.base;
    $("fx").value = example.fx;
    $("fx-date").value = "";
    $("initial-currency").value = "";
    $("rate-settings").open = true;
    $("file-status").textContent =
      "Example loaded · all rates supplied, no lookup needed";
    changed();
    $("source").focus();
  } catch (error) {
    showError(error.message);
  } finally {
    $("example-button").disabled = false;
  }
});

function inputs() {
  return {
    source: $("source").value,
    participants: Array.from($("participants").children, (row) => ({
      code: row.querySelector(".person-code").value,
      name: row.querySelector(".person-name").value,
      split: row.querySelector(".person-split").checked,
    })),
    base: $("base").value,
    fx: $("fx").value,
    fx_date: $("fx-date").value,
    initial_currency: $("initial-currency").value,
  };
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (loading) return;
  loading = true;
  const submittedRevision = revision;
  $("error-box").hidden = true;
  $("results").hidden = true;
  $("results-link").hidden = true;
  $("calculate-button").disabled = true;
  $("calculate-button").textContent = "Calculating…";
  $("loading-status").hidden = false;
  form.setAttribute("aria-busy", "true");
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 120000);
  try {
    const response = await fetch("/api/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(inputs()),
      signal: controller.signal,
    });
    const data = await response.json();
    if (revision !== submittedRevision) {
      showError(
        "Your inputs changed while calculating. Calculate again to use the latest notes.",
      );
      return;
    }
    if (!response.ok) {
      showError(
        data.error || "Could not calculate this ledger.",
        data.field,
        data.line,
      );
      return;
    }
    result = data;
    renderResult(data);
    $("results").hidden = false;
    $("results-link").hidden = false;
    $("stale-note").hidden = true;
    $("download-button").disabled = false;
    $("copy-button").disabled = false;
    $("copy-status").textContent = "";
    $("results").scrollIntoView({ block: "start" });
  } catch (error) {
    showError(
      error.name === "AbortError"
        ? "Rate lookup took too long. Try again, or enter your own exchange rates."
        : "Could not reach the server. Check that the app is running and try again.",
    );
  } finally {
    window.clearTimeout(timeout);
    loading = false;
    form.removeAttribute("aria-busy");
    $("calculate-button").disabled = false;
    $("calculate-button").textContent = "Calculate the split →";
    $("loading-status").hidden = true;
  }
});

function personNode(code, names) {
  const node = element("span", "", "person");
  if (!names[code] || names[code] === code) {
    node.append(element("span", code));
    return node;
  }
  node.append(
    element("span", code, "avatar"),
    element("span", names[code] || code),
  );
  return node;
}

function renderResult({ audit, display }) {
  const names = Object.fromEntries(
    audit.configuration.participants.map((p) => [p.id, p.name || p.code]),
  );
  const base = audit.base_currency;
  $("result-total").textContent = display.total;
  $("result-currency").textContent = base;
  $("result-subtitle").textContent =
    `${audit.transactions.length} expenses · ${display.balances.length} people · settled in ${base}`;
  $("record-badge").textContent = audit.transactions.length;
  $("payments").replaceChildren();
  paymentText = display.payments
    .map(
      (p) => `${names[p.from]} → ${names[p.to]}: ${p.display_amount} ${base}`,
    )
    .join("\n");
  if (!display.payments.length) {
    $("payments").append(
      element("p", "No payments needed. Everyone is balanced."),
    );
    paymentText = "No payments needed. Everyone is balanced.";
  }
  for (const payment of display.payments) {
    const row = element("div", "", "payment");
    const amount = element(
      "span",
      `${payment.display_amount} ${base}`,
      "amount",
    );
    amount.title = `Exact: ${payment.amount} ${base}`;
    row.append(
      personNode(payment.from, names),
      element("span", "→", "arrow"),
      personNode(payment.to, names),
      amount,
    );
    $("payments").append(row);
  }
  $("balances").replaceChildren();
  for (const person of display.balances) {
    const row = element("tr");
    const nameCell = element("td");
    const who = personNode(person.id, names);
    who.classList.add("balance-person");
    nameCell.append(who);
    const netCell = element("td", person.net, `number ${person.direction}`);
    netCell.title = `Exact net: ${audit.net[person.id]} ${base}`;
    netCell.append(
      element(
        "span",
        { receive: "Receives", pay: "Pays", settled: "Settled" }[
          person.direction
        ],
        "direction-label",
      ),
    );
    row.append(
      nameCell,
      element("td", person.paid, "number"),
      element("td", person.share, "number"),
      netCell,
    );
    $("balances").append(row);
  }
  $("ledger-search").value = "";
  renderLedger(audit, display, names);
  renderRates(audit);
  selectTab("balances");
}

function renderLedger(audit, display, names) {
  const query = $("ledger-search").value.toLowerCase().trim();
  const formatted = Object.fromEntries(
    display.expenses.map((tx) => [tx.id, tx]),
  );
  $("ledger").replaceChildren();
  let count = 0;
  for (const tx of audit.transactions) {
    if (
      query &&
      ![tx.description, tx.raw_text, tx.payer_code, names[tx.payer_id], tx.date]
        .join(" ")
        .toLowerCase()
        .includes(query)
    )
      continue;
    count += 1;
    const row = element("tr");
    const sequence = element("td", `#${tx.sequence}`);
    sequence.append(
      element("span", tx.date || "No date", "ledger-date ledger-raw"),
    );
    const expense = element("td");
    const details = element("details", "", "ledger-detail");
    const summary = element("summary", tx.description || "Expense");
    summary.append(element("code", tx.raw_text, "ledger-raw"));
    const extra = element("div");
    extra.append(
      element("div", `Source line ${tx.source_sequence} · ${tx.status}`),
      element("code", tx.source_text),
    );
    extra.append(
      element(
        "div",
        `Currency: ${currencySources[tx.currency_source]}${tx.explicit_currency ? ` (${tx.explicit_currency})` : ""}`,
      ),
    );
    extra.append(
      element(
        "div",
        `Exact base amount: ${tx.base_amount} ${audit.base_currency}`,
      ),
    );
    for (const [person, amount] of Object.entries(tx.shares))
      extra.append(
        element("div", `${names[person]}: ${amount} ${audit.base_currency}`),
      );
    details.append(summary, extra);
    expense.append(details);
    const allocation =
      tx.allocation.type === "all_equal"
        ? "Everyone"
        : tx.allocation.participants.map((p) => names[p]).join(" + ");
    const original = element(
      "td",
      `${formatted[tx.id].amount} ${tx.currency}`,
      "number",
    );
    original.title = `Exact original: ${tx.original_amount} ${tx.currency}`;
    original.append(
      element("span", currencySources[tx.currency_source], "currency-badge"),
    );
    row.append(
      sequence,
      expense,
      element("td", names[tx.payer_id]),
      element("td", allocation),
      original,
      element(
        "td",
        `${formatted[tx.id].base_amount} ${audit.base_currency}`,
        "number",
      ),
    );
    $("ledger").append(row);
  }
  if (!count) {
    const cell = element("td", "No matching expenses.");
    cell.colSpan = 6;
    const row = element("tr");
    row.append(cell);
    $("ledger").append(row);
  }
  $("ledger-count").textContent =
    `${count} of ${audit.transactions.length} records`;
}

function renderRates(audit) {
  $("rate-list").replaceChildren();
  const base = audit.base_currency;
  $("rate-list").append(
    element(
      "p",
      `${base} is the base currency. No conversion needed.`,
      "field-hint",
    ),
  );
  for (const rate of audit.fx_provenance) {
    const item = element("div", "", "rate-item");
    item.append(element("div", `1 ${rate.currency} = ${rate.rate} ${base}`));
    if (rate.source === "supplied") {
      item.append(element("p", `Your rate · ${rate.equation}`));
    } else {
      item.append(
        element(
          "p",
          `${rate.source} · Published ${rate.effective_date} · Requested ${rate.requested_date}`,
        ),
      );
      // Provider URL remains in the audit. The UI uses a fixed trusted link.
      const link = element("a", "About the reference rates");
      link.href = "https://frankfurter.dev/";
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      item.append(link);
    }
    $("rate-list").append(item);
  }
  $("segments").replaceChildren();
  const segments = [];
  for (const tx of audit.transactions) {
    const previous = segments.at(-1);
    if (previous && previous.currency === tx.currency && !tx.explicit_currency)
      previous.end = tx.sequence;
    else
      segments.push({
        start: tx.sequence,
        end: tx.sequence,
        currency: tx.currency,
        source: tx.currency_source,
      });
  }
  for (const segment of segments) {
    const row = element("div", "", "segment");
    const label =
      segment.start === segment.end
        ? `#${segment.start}`
        : `#${segment.start}–#${segment.end}`;
    row.append(
      element("span", label),
      element(
        "span",
        `${segment.currency} · ${currencySources[segment.source]}`,
      ),
    );
    $("segments").append(row);
  }
}

const tabs = ["balances", "ledger", "rates"];
function selectTab(name) {
  for (const tab of tabs) {
    const active = tab === name;
    $("tab-" + tab).classList.toggle("active", active);
    $("tab-" + tab).setAttribute("aria-selected", active.toString());
    $("tab-" + tab).tabIndex = active ? 0 : -1;
    $("panel-" + tab).hidden = !active;
  }
}
for (const [index, name] of tabs.entries()) {
  $("tab-" + name).addEventListener("click", () => selectTab(name));
  $("tab-" + name).addEventListener("keydown", (event) => {
    const next = {
      ArrowRight: (index + 1) % 3,
      ArrowLeft: (index + 2) % 3,
      Home: 0,
      End: 2,
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    selectTab(tabs[next]);
    $("tab-" + tabs[next]).focus();
  });
}
$("ledger-search").addEventListener("input", () => {
  if (!result) return;
  const names = Object.fromEntries(
    result.audit.configuration.participants.map((p) => [
      p.id,
      p.name || p.code,
    ]),
  );
  renderLedger(result.audit, result.display, names);
});
$("edit-button").addEventListener("click", () => $("source").focus());
$("download-button").addEventListener("click", () => {
  if (!result || $("download-button").disabled) return;
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(result.audit, null, 2)], {
      type: "application/json",
    }),
  );
  const link = element("a");
  link.href = url;
  link.download = "trip-split-audit.json";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$("copy-button").addEventListener("click", async () => {
  if (!result || $("copy-button").disabled) return;
  try {
    await navigator.clipboard.writeText(paymentText);
    $("copy-status").textContent = "Payment plan copied.";
  } catch {
    $("copy-status").textContent =
      "Clipboard unavailable. Select the payments above, or download the audit.";
  }
});
