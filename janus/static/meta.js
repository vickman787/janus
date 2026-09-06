"use strict";

const $ = (sel) => document.querySelector(sel);

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

async function refreshMeta() {
  const block = $("#pillBlock");
  const mem = $("#pillMem");
  try {
    const cfg = await api("/api/config");
    const isMainnet = cfg.chain_id === 8453;
    const main = $("#pillNet");
    if (main) {
      if (cfg.chain_id === 8453) {
        main.textContent = "base mainnet 8453";
        main.className = "pill on";
      } else if (cfg.chain_id === null) {
        main.textContent = "rpc unreachable";
        main.className = "pill warn";
      } else {
        main.textContent = "chain " + cfg.chain_id + " . not mainnet";
        main.className = "pill warn";
      }
    }
    block.textContent = (isMainnet ? "block " : "block ") + (cfg.block_number === null ? "." : cfg.block_number);
    const db = String(cfg.memory_db || "").split(/[\\/]/).pop();
    mem.textContent = "sibyl memory: " + db;
  } catch (e) {
    const main = $("#pillNet");
    if (main) { main.textContent = "offline"; main.className = "pill warn"; }
  }
}

refreshMeta();
