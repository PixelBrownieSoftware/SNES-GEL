"use strict";

const ROLE_NAMES = { code: "Code + assets", spc: "SPC program", soundbank: "Soundbank", data: "Level data" };
const $ = (id) => document.getElementById(id);
let state = null;

function hex(value, width = 4) {
  return "$" + value.toString(16).toUpperCase().padStart(width, "0");
}

function kb(bytes) {
  return (bytes / 1024).toFixed(1) + " KB";
}

function setStatus(text, bad) {
  const status = $("status");
  status.textContent = text;
  status.className = bad ? "warn" : "ok";
}

function fillSelect(select, values, current) {
  select.textContent = "";
  for (const value of values) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = value === 0 ? "None" : value + " KB";
    select.append(option);
  }
  select.value = String(current);
}

function line(parent, text, className) {
  const p = document.createElement("p");
  p.textContent = text;
  if (className) p.className = className;
  parent.append(p);
}

function render() {
  const { header, build } = state;
  fillSelect($("rom-size"), state.rom_sizes, header.rom_size_kb);
  fillSelect($("sram-size"), state.sram_sizes, header.sram_kb);
  $("asset-start").value = hex(state.asset_start);
  $("title").value = header.title;
  $("region").value = String(header.region);
  $("developer").value = header.developer;
  $("version").value = header.version;

  const summary = $("summary");
  const banks = $("banks");
  summary.textContent = "";
  banks.textContent = "";
  if (!build) {
    line(summary, "No build found yet. Build the game to see how the ROM is allocated.");
    return;
  }
  const codeFree = build.code_limit - build.code_end;
  line(summary, `ROM: ${build.rom_kb} KB (settings ask for at least ${build.requested_kb} KB; the content needs ${build.needed_kb} KB).`);
  line(summary, `Code: ${hex(build.code_start)}-${hex(build.code_end)}, ${codeFree} bytes free before ${hex(build.code_limit)}.`,
    codeFree < 256 ? "warn" : "");
  line(summary, `Assets: ${hex(build.asset_start)}-${hex(build.asset_end)}, ${build.asset_end - build.asset_start} bytes used.`);
  line(summary, `Sound: ${(build.soundbank_bytes / 1024).toFixed(1)} KB soundbank. Total used: ${kb(build.used_bytes)}.`);

  for (const bank of build.banks) {
    const row = document.createElement("div");
    row.className = "bank";
    const label = document.createElement("span");
    label.textContent = "Bank " + hex(bank.bank, 2);
    const bar = document.createElement("div");
    bar.className = "bank-bar";
    const fill = document.createElement("div");
    fill.className = "bank-fill " + (bank.used ? bank.role : "empty");
    fill.style.width = (100 * bank.used / bank.size) + "%";
    bar.append(fill);
    if (bank.role === "code") {
      const split = document.createElement("div");
      split.className = "bank-split";
      split.style.left = (100 * (build.asset_start - 0x8000) / bank.size) + "%";
      bar.append(split);
    }
    const info = document.createElement("span");
    info.textContent = bank.used
      ? `${ROLE_NAMES[bank.role]}: ${kb(bank.used)} of 32 KB`
      : "Padding (empty)";
    row.append(label, bar, info);
    banks.append(row);
  }
}

async function load() {
  const response = await fetch("/api/rom-overview");
  state = await response.json();
  if (!response.ok) throw new Error(state.error || "Could not load ROM overview");
  render();
}

async function save() {
  const text = $("asset-start").value.trim().replace(/^\$/, "0x");
  const assetStart = Number(text);
  const body = {
    rom_size_kb: Number($("rom-size").value),
    sram_kb: Number($("sram-size").value),
    asset_start: assetStart,
    title: $("title").value,
    region: Number($("region").value),
    developer: Number($("developer").value),
    version: Number($("version").value),
  };
  if (!Number.isInteger(assetStart)) {
    setStatus("Asset start must look like $C000.", true);
    return;
  }
  try {
    const response = await fetch("/api/rom-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Save failed");
    state = result;
    render();
    setStatus("Saved to gel.json. Rebuild the game to apply.", false);
  } catch (error) {
    setStatus(error.message, true);
  }
}

window.addEventListener("DOMContentLoaded", () => {
  $("save").addEventListener("click", save);
  load().catch((error) => setStatus(error.message, true));
});
