"use strict";

const $ = (sel) => document.querySelector(sel);

/* The bridge glyph is the thesis in one character.
   = memory and chain agree, cobalt
   != they disagree, red
   ? no checkpoint to compare against, steel */
const VERDICT = {
  consistent: { word: "CONSISTENT", glyph: "=", action: true },
  completed: { word: "COMPLETED", glyph: "=", action: false },
  mismatch: { word: "MISMATCH", glyph: "\u2260", action: false },
  no_checkpoint: { word: "NO CHECKPOINT", glyph: "?", action: false },
};

const STEP_GLYPH = { done: "[x]", current: "[>]", pending: "[ ]" };

let currentOpId = null;

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function short(addr, head = 10, tail = 6) {
  if (!addr) return ".";
  if (addr.length <= head + tail + 1) return addr;
  return addr.slice(0, head) + "\u2026" + addr.slice(-tail);
}

function banner(text, kind) {
  const b = $("#banner");
  if (!text) {
    b.classList.add("hidden");
    b.textContent = "";
    return;
  }
  b.className = "banner" + (kind ? " " + kind : "");
  b.textContent = text;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

/* ------------------------------------------------------------- rendering */

function renderRemembered(op) {
  $("#opTitle").textContent = op.operation_type.replace(/_/g, " ");
  $("#opMeta").textContent = op.operation_id + " . contract " + op.contract_address;

  const steps = $("#mvSteps");
  steps.innerHTML = "";
  for (const s of op.steps) {
    const cls = s.state === "done" ? "done" : s.state === "current" ? "cur" : "pend";
    steps.appendChild(el(
      '<div class="line ' + cls + '"><span class="m">' + STEP_GLYPH[s.state] +
      "</span><span>" + esc(s.label) + "</span></div>"
    ));
  }

  const kv = $("#mvKv");
  kv.innerHTML = "";
  const rows = [
    ["expects owner", short(op.expected_current_owner)],
    ["expects pending", short(op.target_owner)],
    ["chain id", op.chain_id],
    ["last block", op.last_verified_block === null ? "." : op.last_verified_block],
    ["next step", op.next_expected_step || "."],
  ];
  for (const [k, v] of rows) {
    kv.appendChild(el(
      '<div class="r"><span class="k">' + esc(k) + '</span><span class="v">' + esc(v) + "</span><span></span></div>"
    ));
  }

  renderTxs(op.transaction_hashes || []);
}

function renderLive(live, op) {
  const kv = $("#lvKv");
  kv.innerHTML = "";
  if (!live) {
    kv.appendChild(el('<div class="r"><span class="k">state</span><span class="v">no contract to read</span><span></span></div>'));
    return;
  }

  const expectOwner = op ? (op.expected_current_owner || "").toLowerCase() : null;
  const expectPending = op ? (op.target_owner || "").toLowerCase() : null;
  const isDone = op && (op.current_step === "completed" || op.current_step === "owner_verified");
  const zero = "0x" + "0".repeat(40);

  let ownerFlag = null;
  let pendingFlag = null;
  if (expectOwner) {
    const want = isDone ? expectPending : expectOwner;
    ownerFlag = live.owner.toLowerCase() === want;
  }
  if (expectPending) {
    const want = isDone ? zero : expectPending;
    pendingFlag = live.pending_owner.toLowerCase() === want;
  }

  const chainFlag = op ? live.chain_id === op.chain_id : null;

  const rows = [
    ["owner()", live.owner, ownerFlag],
    ["pendingOwner()", live.pending_owner, pendingFlag],
    ["chainId", live.chain_id, chainFlag],
    ["codeHash", short(live.runtime_code_hash, 10, 4), null],
    ["block", live.block_number, null],
  ];

  for (const [k, v, flag] of rows) {
    let tag = "<span></span>";
    if (flag === true) tag = '<span class="flag match">match</span>';
    if (flag === false) tag = '<span class="flag differs">differs</span>';
    kv.appendChild(el(
      '<div class="r"><span class="k">' + esc(k) + '</span><span class="v">' +
      esc(typeof v === "string" && v.startsWith("0x") && v.length > 30 ? short(v) : v) +
      "</span>" + tag + "</div>"
    ));
  }
}

function renderVerdict(verdict) {
  const meta = VERDICT[verdict.status] || { word: verdict.status.toUpperCase(), glyph: "?", action: false };

  $("#bridge").dataset.state = verdict.status;
  $("#bridgeGlyph").textContent = meta.glyph;

  $("#verdict").dataset.state = verdict.status;
  $("#verdictWord").textContent = meta.word;
  $("#verdictMsg").textContent = verdict.message || "";

  const reasons = $("#verdictReasons");
  reasons.innerHTML = "";
  if (verdict.reasons && verdict.reasons.length) {
    reasons.classList.remove("hidden");
    for (const r of verdict.reasons) reasons.appendChild(el("<li>" + esc(r) + "</li>"));
  } else {
    reasons.classList.add("hidden");
  }

  const show = Boolean(meta.action && verdict.next_action);
  $("#ctaWrap").classList.toggle("hidden", !show);
  if (show) {
    $("#nextAction").textContent =
      verdict.next_action === "acceptOwnership" ? "acceptOwnership()" : verdict.next_action;
  }
}

function renderTxs(txs) {
  const tb = $("#txTable tbody");
  tb.innerHTML = "";
  if (!txs.length) {
    tb.appendChild(el('<tr><td colspan="4" class="muted">no transactions recorded</td></tr>'));
    return;
  }
  for (const tx of txs) {
    const link = tx.url
      ? '<a target="_blank" rel="noopener" href="' + esc(tx.url) + '">' + esc(tx.tx_hash) + "</a>"
      : esc(tx.tx_hash);
    tb.appendChild(el(
      "<tr><td>" + esc(tx.step) + "</td><td>" + link +
      '</td><td class="' + (tx.status === 1 ? "ok" : "bad") + '">' + (tx.status === 1 ? "ok" : "FAILED") +
      "</td><td>" + esc(tx.block_number) + "</td></tr>"
    ));
  }
}

/* --------------------------------------------------------------- actions */

function showPanels(mode) {
  const setup = mode === "setup";
  $("#startPanel").classList.toggle("hidden", !setup);
  $("#restorePanel").classList.toggle("hidden", !setup);
  $("#opPanel").classList.toggle("hidden", setup);
}

async function showOperation(operationId, fallbackContract) {
  let url = "/api/operations/" + encodeURIComponent(operationId);
  if (fallbackContract) url += "?contract=" + encodeURIComponent(fallbackContract);
  const data = await api(url);
  currentOpId = operationId;

  if (data.operation) {
    renderRemembered(data.operation);
    $("#opNote").textContent = "restored from Sibyl memory . no runtime state carried over";
  } else {
    $("#opTitle").textContent = "ownership transfer";
    $("#opMeta").textContent = operationId + " . not present in Sibyl memory";
    $("#mvSteps").innerHTML = '<div class="line pend"><span class="m">[ ]</span><span>no execution history</span></div>';
    $("#mvKv").innerHTML = "";
    $("#opNote").textContent = "Sibyl memory holds no record for this operation id";
    renderTxs([]);
  }

  renderLive(data.live, data.operation);
  renderVerdict(data.verdict);
  $("#sibylRaw").textContent = JSON.stringify(data, null, 2);

  try {
    const mem = await api("/api/operations/" + encodeURIComponent(operationId) + "/memory");
    $("#journalRaw").textContent = JSON.stringify(mem.events || [], null, 2);
  } catch (e) {
    $("#journalRaw").textContent = "no journal entries";
  }

  showPanels("op");
}

async function loadOps() {
  const data = await api("/api/operations");
  const sel = $("#selOps");
  sel.innerHTML = "";
  if (!data.operations.length) {
    sel.appendChild(el('<option value="">no operations in Sibyl memory</option>'));
    return;
  }
  for (const op of data.operations) {
    sel.appendChild(el(
      '<option value="' + esc(op.operation_id) + '">' +
      esc(op.operation_id + "  .  " + op.current_step + "  .  " + short(op.target_owner, 8, 4)) +
      "</option>"
    ));
  }
}

async function refreshMeta() {
  try {
    const cfg = await api("/api/config");
    const net = $("#pillNet");
    if (cfg.chain_id === 8453) {
      net.textContent = "base mainnet 8453";
      net.className = "pill on";
    } else if (cfg.chain_id === null) {
      net.textContent = "rpc unreachable";
      net.className = "pill warn";
    } else {
      net.textContent = "chain " + cfg.chain_id + " . not mainnet";
      net.className = "pill warn";
    }
    $("#pillBlock").textContent = cfg.block_number === null ? "block ." : "block " + cfg.block_number;
    const db = String(cfg.memory_db || "").split(/[\\/]/).pop();
    $("#pillMem").textContent = "sibyl memory: " + db;
    if (cfg.contract_address && !$("#inpContract").value) {
      $("#inpContract").value = cfg.contract_address;
    }
    if (!cfg.has_owner_key || !cfg.has_target_key) {
      banner("Signer keys are missing. Set JANUS_OWNER_KEY and JANUS_TARGET_KEY in .env to run a real transfer on Base mainnet.", "err");
    }
  } catch (e) {
    $("#pillNet").textContent = "offline";
    $("#pillNet").className = "pill warn";
  }
}

$("#btnDeploy").addEventListener("click", async () => {
  const btn = $("#btnDeploy");
  btn.disabled = true;
  $("#deployState").textContent = "deploying to Base mainnet .";
  try {
    const data = await api("/api/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    $("#inpContract").value = data.contract_address;
    $("#deployState").textContent = "deployed at " + short(data.contract_address) + " in block " + data.block_number;
    banner("");
  } catch (e) {
    banner("Deploy failed. " + e.message, "err");
    $("#deployState").textContent = "";
  } finally {
    btn.disabled = false;
  }
});

$("#btnBegin").addEventListener("click", async () => {
  const btn = $("#btnBegin");
  btn.disabled = true;
  $("#startStatus").textContent = "reading Base, submitting transferOwnership, checkpointing .";
  try {
    const body = { contract_address: $("#inpContract").value.trim() || null };
    const data = await api("/api/operations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const id = data.operation.operation_id;
    banner("Checkpoint written to Sibyl memory. Kill this process, start a fresh one, then restore " + id + ".", "ok");
    await loadOps();
    await showOperation(id);
  } catch (e) {
    banner("Begin refused. " + e.message, "err");
  } finally {
    btn.disabled = false;
    $("#startStatus").textContent = "";
  }
});

$("#btnLoad").addEventListener("click", async () => {
  const id = $("#inpOpId").value.trim() || $("#selOps").value;
  if (!id) {
    banner("Pick an operation or type an operation id.", "err");
    return;
  }
  try {
    banner("");
    await showOperation(id);
  } catch (e) {
    banner("Restore failed. " + e.message, "err");
  }
});

$("#btnRefresh").addEventListener("click", async () => {
  try { await loadOps(); banner(""); } catch (e) { banner(e.message, "err"); }
});

$("#btnContinue").addEventListener("click", async () => {
  const btn = $("#btnContinue");
  btn.disabled = true;
  banner("Revalidating against Base before signing .", "ok");
  try {
    await api("/api/operations/" + encodeURIComponent(currentOpId) + "/continue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ execute: true }),
    });
    banner("");
    await showOperation(currentOpId);
    await loadOps();
  } catch (e) {
    banner("Continuation refused. " + e.message, "err");
    await showOperation(currentOpId);
  } finally {
    btn.disabled = false;
  }
});

$("#btnDelete").addEventListener("click", async () => {
  if (!currentOpId) return;
  try {
    await api("/api/operations/" + encodeURIComponent(currentOpId), { method: "DELETE" });
    banner("Checkpoint deleted from Sibyl memory. Restore it again to see the refusal.", "ok");
    await loadOps();
    await showOperation(currentOpId);
  } catch (e) {
    banner(e.message, "err");
  }
});

$("#btnBack").addEventListener("click", () => {
  showPanels("setup");
  banner("");
  loadOps();
});

document.querySelectorAll(".tablink").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tablink").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    for (const name of ["txs", "sibyl", "journal"]) {
      $("#tab-" + name).classList.toggle("hidden", name !== tab);
    }
  });
});

refreshMeta();
loadOps();

/* Deep link support: /?op=op_xxx restores straight into the operation view.
   Used by the demo, and by the screenshot script that captures each verdict. */
(async () => {
  const params = new URLSearchParams(window.location.search);
  const op = params.get("op");
  if (!op) return;
  const contract = params.get("contract");
  try {
    await showOperation(op, contract);
  } catch (e) {
    banner("Could not restore " + op + ". " + e.message, "err");
  }
})();
