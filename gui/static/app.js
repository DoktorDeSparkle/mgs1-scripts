/* MGS1 Undub Studio — frontend
 *
 * Single-file vanilla JS app. Talks to the Python backend over a small JSON
 * API; renders the real game font (shipped as raw 2bpp hex) on canvases for
 * the codec preview and charset browser.
 */

"use strict";

/* ── tiny helpers ─────────────────────────────────────────────────────── */

const $ = (sel, root) => (root || document).querySelector(sel);

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const resp = await fetch(path, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || resp.statusText);
  return data;
}

function toast(msg, kind) {
  const t = el("div", { class: "toast " + (kind || "") }, msg);
  $("#toast-area").append(t);
  setTimeout(() => t.remove(), kind === "error" ? 8000 : 4000);
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function fmtTime(frames, fps) {
  const s = Number(frames) / (fps || 30);
  if (!isFinite(s)) return "?";
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}

function fmtDate(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : "";
}

/* In-game line breaks: radio (codec) carries them as a literal
 * backslash-r-backslash-n escape (＃Ｎ in game bytes); demo/vox/zmovie use the
 * fullwidth pipe ｜. The editor shows real newlines and stores the convention
 * back per kind. */
const KIND_NL = { radio: "\\r\\n", demo: "｜", vox: "｜", zmovie: "｜" };
const toDisplay = (s, kind) => (s || "").split(KIND_NL[kind || "radio"]).join("\n");
const toStored = (s, kind) => (s || "").split("\n").join(KIND_NL[kind || "radio"]);

/* ── global state ─────────────────────────────────────────────────────── */

const S = {
  state: null,            // /api/state
  view: "project",
  font: null,             // /api/font
  charset: null,
  data: {},               // per kind: {original, work, meta}
  dirty: {},              // per kind: unsaved edits
  sel: {},                // per kind: current selection
  search: {},             // per kind: search text
  filterUntranslated: {},
  watchingJob: null,
  buildLog: [],
};

const KINDS = ["radio", "demo", "vox", "zmovie"];
const KIND_LABEL = { radio: "Radio", demo: "Demo", vox: "Vox", zmovie: "ZMovie" };
const KIND_FILE = { radio: "RADIO.DAT", demo: "DEMO.DAT", vox: "VOX.DAT", zmovie: "ZMOVIE.STR" };
const KIND_BANK = { radio: 1, demo: 3, vox: 3, zmovie: 3 };
/* Display limits: codec window is 4 lines; demo/vox/zmovie subtitles are 2.
 * All modes share the same ~260px line width (configurable per project). */
const KIND_MAXLINES = { radio: 4, demo: 2, vox: 2, zmovie: 2 };
function previewWidth(kind) {
  const cfg = (S.state && S.state.project) || {};
  return kind === "radio" ? (cfg.previewWidthRadio || 260)
                          : (cfg.previewWidthDemo || 260);
}

/* ── game font renderer ───────────────────────────────────────────────── */

const FontR = {
  ready: false,
  ascii: [],   // [{pix:Uint8Array, width}]
  kana: [],    // [Uint8Array]  (12x12)
  map: {},     // char -> {type, idx|slot|hex}
  tileCache: new Map(),
  palette: [null, "#1d3d24", "#5fa253", "#bdeaa9"], // 2bpp -> codec green

  load(payload) {
    this.map = payload.mapping || {};
    this.reason = payload.reason;
    if (!payload.available) { this.ready = false; return; }
    this.ascii = payload.ascii.map(g => ({ pix: decode2bpp(g.hex), width: g.width }));
    this.kana = payload.kana.map(decode2bpp);
    this.ready = true;
  },

  glyphFor(ch) {
    if (ch === " ") return { pix: null, width: this.ready ? (this.ascii[0]?.width || 4) : 4 };
    const code = ch.codePointAt(0);
    if (this.ready && code >= 0x20 && code <= 0x7e) {
      const g = this.ascii[code - 0x20];
      if (g) return g;
    }
    const ref = this.map[ch];
    if (ref && this.ready) {
      if (ref.type === "ascii" && this.ascii[ref.idx]) return this.ascii[ref.idx];
      if (ref.type === "kana" && this.kana[ref.slot])
        return { pix: this.kana[ref.slot], width: 12 };
    }
    if (ref && ref.type === "tile") {
      let pix = this.tileCache.get(ref.hex);
      if (!pix) { pix = decode2bpp(ref.hex); this.tileCache.set(ref.hex, pix); }
      return { pix, width: 12 };
    }
    return null; // unknown — drawn as a hollow box
  },

  measure(text) {
    let w = 0;
    for (const ch of stripMarkers(text)) {
      const g = this.glyphFor(ch);
      w += g ? g.width : 12;
    }
    return w;
  },

  wrap(text, maxWidth) {
    const lines = [];
    for (const hard of text.split("\n")) {
      let line = "", width = 0, word = "", wordW = 0;
      const flushWord = () => { line += word; width += wordW; word = ""; wordW = 0; };
      for (const ch of stripMarkers(hard)) {
        const g = this.glyphFor(ch);
        const cw = g ? g.width : 12;
        const isCJK = ch.codePointAt(0) > 0x2e80;
        if (ch === " " || isCJK) {
          flushWord();
          if (width + cw > maxWidth && line) { lines.push(line); line = ""; width = 0; if (ch === " ") continue; }
          line += ch; width += cw;
        } else {
          if (wordW + cw > maxWidth) { flushWord(); }   // pathological long word
          word += ch; wordW += cw;
          if (width + wordW > maxWidth) {
            if (line) { lines.push(line.replace(/ +$/, "")); line = ""; width = 0; }
          }
        }
      }
      flushWord();
      lines.push(line.replace(/ +$/, ""));
    }
    return lines;
  },

  /* Draw text onto a canvas at native game resolution; CSS scales it up. */
  draw(canvas, text, maxWidth) {
    const scale = 2;
    const lineH = 14;
    const pad = 6;
    const lines = this.ready ? this.wrap(text, maxWidth) : (text || "").split("\n");
    const h = Math.max(1, lines.length) * lineH + pad * 2;
    canvas.width = (maxWidth + pad * 2) * scale;
    canvas.height = h * scale;
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.scale(scale, scale);

    if (!this.ready) {
      ctx.font = "11px monospace";
      ctx.fillStyle = this.palette[3];
      lines.forEach((ln, i) => ctx.fillText(stripMarkers(ln), pad, pad + 10 + i * lineH));
      ctx.restore();
      return { lines: lines.length, overflow: false };
    }

    let overflow = false;
    lines.forEach((ln, li) => {
      let x = pad;
      const y = pad + li * lineH;
      for (const ch of ln) {
        const g = this.glyphFor(ch);
        if (!g) {  // unknown char → hollow box
          ctx.strokeStyle = "#7a4040";
          ctx.strokeRect(x + 1.5, y + 1.5, 9, 9);
          x += 12;
          continue;
        }
        if (g.pix) {
          for (let py = 0; py < 12; py++)
            for (let px = 0; px < g.width; px++) {
              const v = g.pix[py * g.width + px];
              if (v) { ctx.fillStyle = this.palette[v]; ctx.fillRect(x + px, y + py, 1, 1); }
            }
        }
        x += g.width;
      }
      if (x - pad > maxWidth) overflow = true;
    });
    ctx.restore();
    return { lines: lines.length, overflow };
  },
};

function decode2bpp(hex) {
  /* 36-byte 12x12 tiles or variable-width ASCII strips: 4 px/byte MSB-first */
  const bytes = [];
  for (let i = 0; i < hex.length; i += 2) bytes.push(parseInt(hex.slice(i, i + 2), 16));
  const out = new Uint8Array(bytes.length * 4);
  bytes.forEach((b, i) => {
    out[i * 4] = (b >> 6) & 3;
    out[i * 4 + 1] = (b >> 4) & 3;
    out[i * 4 + 2] = (b >> 2) & 3;
    out[i * 4 + 3] = b & 3;
  });
  return out;
}

function stripMarkers(text) {
  return (text || "").replaceAll("‹BK›", "").replaceAll("‹TK›", "");
}

function drawTile(canvas, hex) {
  const pix = decode2bpp(hex);
  canvas.width = 12; canvas.height = 12;
  const ctx = canvas.getContext("2d");
  for (let y = 0; y < 12; y++)
    for (let x = 0; x < 12; x++) {
      const v = pix[y * 12 + x];
      if (v) { ctx.fillStyle = FontR.palette[v]; ctx.fillRect(x, y, 1, 1); }
    }
}

/* ── navigation & boot ────────────────────────────────────────────────── */

document.querySelectorAll("#sidenav button[data-view]").forEach(btn => {
  btn.addEventListener("click", () => show(btn.dataset.view));
});

document.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === "s") {
    ev.preventDefault();
    saveCurrent();
  }
});

window.addEventListener("beforeunload", (ev) => {
  if (Object.values(S.dirty).some(Boolean)) ev.preventDefault();
});

async function boot() {
  await refreshState();
  try { S.font = await api("/api/font"); FontR.load(S.font); } catch { /* no project yet */ }
  show(S.state && S.state.project ? "radio" : "project");
}

async function refreshState() {
  S.state = await api("/api/state");
  const cfg = S.state.project;
  $("#project-name").textContent = cfg ? cfg.name || cfg.root : "no project";
  for (const kind of KINDS) {
    const badge = $("#badge-" + kind);
    const st = S.state.status[kind];
    if (st && st.extracted) {
      badge.textContent = `${st.translated}/${st.total}`;
      badge.className = "badge" + (st.translated === st.total && st.total ? " done" : "");
    } else badge.textContent = "";
  }
  updateSaveState();
}

function updateSaveState() {
  const dirty = KINDS.filter(k => S.dirty[k]);
  $("#save-state").innerHTML = dirty.length
    ? `<span class="amber">● unsaved: ${dirty.join(", ")}</span><br><span class="muted">Ctrl+S to save</span>`
    : "";
}

function show(view) {
  S.view = view;
  document.querySelectorAll("#sidenav button[data-view]").forEach(b =>
    b.classList.toggle("active", b.dataset.view === view));
  const main = $("#view");
  main.innerHTML = "";
  if (!S.state) return;
  if (view !== "project" && !S.state.project) {
    main.append(el("div", { class: "panel" }, "Open a project first."));
    return;
  }
  if (view === "project") renderProject(main);
  else if (view === "radio") renderRadio(main);
  else if (view === "charset") renderCharset(main);
  else if (view === "build") renderBuild(main);
  else renderKindEditor(main, view);
}

function saveCurrent() {
  const kind = S.view;
  if (KINDS.includes(kind) && S.dirty[kind]) saveWork(kind);
}

async function saveWork(kind) {
  try {
    await api(`/api/data/${kind}/save`, { work: S.data[kind].work });
    S.dirty[kind] = false;
    updateSaveState();
    toast(`${KIND_LABEL[kind]} translations saved.`);
    refreshState();
  } catch (e) { toast("Save failed: " + e.message, "error"); }
}

function markDirty(kind) {
  S.dirty[kind] = true;
  updateSaveState();
}

/* ── jobs ─────────────────────────────────────────────────────────────── */

async function startJob(path, body, onDone) {
  try {
    const { job } = await api(path, body || {});
    watchJob(job, onDone);
  } catch (e) { toast(e.message, "error"); }
}

function watchJob(id, onDone) {
  S.watchingJob = id;
  const ind = $("#job-indicator");
  ind.classList.remove("hidden");
  const tick = async () => {
    let job;
    try { job = await api(`/api/jobs/${id}`); }
    catch { ind.classList.add("hidden"); return; }
    $("#job-indicator-name").textContent = job.name;
    S.buildLog = job.log;
    const con = $("#build-console");
    if (con) {
      con.innerHTML = "";
      job.log.forEach(line => con.append(renderLogLine(line)));
      con.scrollTop = con.scrollHeight;
    }
    if (job.status === "running") { setTimeout(tick, 700); return; }
    ind.classList.add("hidden");
    S.watchingJob = null;
    if (job.status === "done") toast(`${job.name}: done.`);
    else toast(`${job.name} failed: ${job.error}`, "error");
    await refreshState();
    if (onDone) onDone(job);
    if (S.view === "build") show("build");
  };
  tick();
}

function renderLogLine(line) {
  const cls = line.startsWith("$") ? "cmd" :
    /error|fail|warning/i.test(line) ? "err" : "";
  return el("div", { class: cls }, line);
}

/* ── project view ─────────────────────────────────────────────────────── */

function renderProject(main) {
  const cfg = S.state.project;
  main.append(el("h2", null, "Project"));

  if (S.state.missingDeps && S.state.missingDeps.length) {
    main.append(el("div", { class: "panel", style: "border-left:3px solid var(--amber)" },
      el("h3", { style: "margin-top:0", class: "amber" }, "Missing toolkit dependencies"),
      ...S.state.missingDeps.map(d =>
        el("div", { style: "font-size:12.5px;margin:3px 0" },
          el("span", { class: "mono amber" }, d.pip), " — needed for ", d.neededFor)),
      el("div", { style: "margin-top:8px;font-size:12.5px" },
        "Install with: ",
        el("span", { class: "mono" }, "pip install -r " + S.state.scriptsDir + "/requirements.txt"),
        " then restart the GUI."),
    ));
  }

  // open/create
  const openInput = el("input", {
    type: "text",
    placeholder: "Path to a disc folder (the one containing MGS/RADIO.DAT …)",
    value: cfg ? cfg.root : (S.state.lastRoot || ""),
  });
  main.append(el("div", { class: "panel" },
    el("h3", null, "Disc folder"),
    el("div", { class: "row" },
      Object.assign(openInput, { style: "flex:1" }),
      el("button", { class: "btn", onclick: () => browseDir(openInput) }, "Browse…"),
      el("button", {
        class: "btn primary", onclick: async () => {
          try {
            await api("/api/project/open", { path: openInput.value });
            await refreshState();
            try { S.font = await api("/api/font"); FontR.load(S.font); } catch {}
            S.data = {}; S.sel = {};
            toast("Project opened.");
            show("project");
          } catch (e) { toast(e.message, "error"); }
        },
      }, cfg ? "Re-open / Rescan" : "Open project"),
    ),
    el("div", { class: "muted", style: "margin-top:8px;font-size:12px" },
      "Game files are auto-detected. Extractions, translations and built files ",
      "all live in an ", el("span", { class: "mono" }, "undub-workspace/"),
      " folder inside — your originals are never modified."),
  ));

  if (!cfg) return;

  // paths
  const pathFields = [
    ["radioDat", "RADIO.DAT (codec calls)"],
    ["demoDat", "DEMO.DAT (cutscenes)"],
    ["voxDat", "VOX.DAT (radio voice)"],
    ["zmovieStr", "ZMOVIE.STR (FMV subtitles)"],
    ["stageDir", "STAGE.DIR (offsets + game font)"],
  ];
  const panel = el("div", { class: "panel" }, el("h3", null, "Game files"));
  for (const [key, label] of pathFields) {
    const input = el("input", { type: "text", value: cfg[key] || "" });
    input.addEventListener("change", () => updateProject({ [key]: input.value }));
    panel.append(el("div", { class: "field" },
      el("label", null, label),
      el("div", { class: "row" }, input)));
  }
  main.append(panel);

  // translation settings
  const prov = el("select", null,
    ...["none", "deepl", "google", "libre"].map(p =>
      el("option", { value: p, selected: cfg.mtProvider === p ? "" : null }, p)));
  const keyIn = el("input", { type: "text", value: cfg.mtApiKey || "", placeholder: "API key" });
  const urlIn = el("input", { type: "text", value: cfg.mtUrl || "", placeholder: "LibreTranslate URL (libre only)" });
  const srcIn = el("input", { type: "text", value: cfg.mtSource || "JA", style: "width:70px" });
  const tgtIn = el("input", { type: "text", value: cfg.mtTarget || "EN", style: "width:70px" });
  const fpsIn = el("input", { type: "number", value: cfg.fps || 30, style: "width:80px" });
  const pwRadio = el("input", { type: "number", value: cfg.previewWidthRadio || 260, style: "width:80px" });
  const pwDemo = el("input", { type: "number", value: cfg.previewWidthDemo || 260, style: "width:80px" });
  const saveMt = () => updateProject({
    mtProvider: prov.value, mtApiKey: keyIn.value, mtUrl: urlIn.value,
    mtSource: srcIn.value, mtTarget: tgtIn.value, fps: Number(fpsIn.value) || 30,
    previewWidthRadio: Number(pwRadio.value) || 260,
    previewWidthDemo: Number(pwDemo.value) || 260,
  });
  [prov, keyIn, urlIn, srcIn, tgtIn, fpsIn, pwRadio, pwDemo].forEach(i => i.addEventListener("change", saveMt));
  main.append(el("div", { class: "panel" },
    el("h3", null, "Machine translation (optional)"),
    el("div", { class: "row" },
      el("div", { class: "field" }, el("label", null, "Provider"), prov),
      el("div", { class: "field", style: "flex:1" }, el("label", null, "API key"), keyIn),
    ),
    el("div", { class: "field" }, el("label", null, "Endpoint URL"), urlIn),
    el("div", { class: "row" },
      el("div", { class: "field" }, el("label", null, "Source"), srcIn),
      el("div", { class: "field" }, el("label", null, "Target"), tgtIn),
      el("div", { class: "field" }, el("label", null, "Tick rate (display hint)"), fpsIn),
    ),
    el("div", { class: "muted", style: "font-size:11.5px" },
      "Tick rate ≈30/s nominal on PS1; measured ~26–27/s in practice — affects the m:ss hints only."),
  ));

  main.append(el("div", { class: "panel" },
    el("h3", null, "Preview wrap widths (px)"),
    el("div", { class: "row" },
      el("div", { class: "field" }, el("label", null, "Codec window"), pwRadio),
      el("div", { class: "field" }, el("label", null, "Demo / Vox / ZMovie"), pwDemo),
    ),
    el("div", { class: "muted", style: "font-size:11.5px" },
      "260 px matches the known codec window; demo subtitle width is not yet "
      + "confirmed — tune it here if previews wrap differently than the game."),
  ));

  // font status
  main.append(el("div", { class: "panel" },
    el("h3", null, "Codec preview font"),
    el("div", { class: "muted", style: "font-size:12.5px" },
      FontR.ready
        ? `Game font loaded from STAGE.DIR — ${FontR.ascii.length} ASCII + ${FontR.kana.length} kana/kanji glyphs. Previews use the real in-game font.`
        : `Game font not loaded (${(S.font && S.font.reason) || "no data"}). Previews fall back to a system font; set STAGE.DIR above to enable.`),
  ));

  // csv round trip
  const fileIn = el("input", { type: "file", accept: ".csv", style: "display:none" });
  fileIn.addEventListener("change", async () => {
    const file = fileIn.files[0];
    if (!file) return;
    try {
      const res = await api("/api/import/csv", { csv: await file.text() });
      toast(`CSV imported: ${res.applied} lines applied, ${res.skipped} skipped.`);
      S.data = {};
      refreshState();
    } catch (e) { toast(e.message, "error"); }
    fileIn.value = "";
  });
  main.append(el("div", { class: "panel" },
    el("h3", null, "Spreadsheet round-trip"),
    el("div", { class: "row" },
      el("button", { class: "btn", onclick: () => window.open("/api/export/all") }, "Export all → CSV"),
      el("button", { class: "btn", onclick: () => fileIn.click() }, "Import CSV…"),
      fileIn,
    ),
    el("div", { class: "muted", style: "margin-top:8px;font-size:12px" },
      "Hand the CSV to translators or a CAT tool; re-import fills the working translations."),
  ));
}

async function updateProject(fields) {
  try {
    const { project } = await api("/api/project/update", fields);
    S.state.project = project;
    toast("Project settings saved.");
  } catch (e) { toast(e.message, "error"); }
}

function browseDir(targetInput) {
  const back = el("div", { id: "modal-back", onclick: (ev) => { if (ev.target === back) back.remove(); } });
  const pathLabel = el("div", { class: "mono", style: "font-size:12px;word-break:break-all" });
  const list = el("div", { class: "dir-list" });
  const modal = el("div", { class: "modal" },
    el("h3", { style: "margin-top:0" }, "Choose folder"),
    pathLabel, list,
    el("div", { class: "row" },
      el("button", { class: "btn primary", onclick: () => { targetInput.value = pathLabel.textContent; back.remove(); targetInput.dispatchEvent(new Event("change")); } }, "Use this folder"),
      el("button", { class: "btn", onclick: () => back.remove() }, "Cancel"),
    ));
  back.append(modal);
  document.body.append(back);

  async function load(path) {
    try {
      const data = await api("/api/browse?path=" + encodeURIComponent(path || ""));
      pathLabel.textContent = data.path;
      list.innerHTML = "";
      list.append(el("button", { onclick: () => load(data.parent) }, "⬑ .."));
      data.dirs.forEach(d => list.append(
        el("button", { onclick: () => load(data.path + "/" + d) }, "▸ " + d)));
    } catch (e) { toast(e.message, "error"); }
  }
  load(targetInput.value);
}

/* ── shared editor scaffolding ────────────────────────────────────────── */

async function ensureData(kind) {
  if (!S.data[kind]) S.data[kind] = await api(`/api/data/${kind}`);
  return S.data[kind];
}

function extractPanel(main, kind) {
  main.append(el("h2", null, KIND_LABEL[kind]));
  main.append(el("div", { class: "panel" },
    el("p", { class: "muted" },
      `${KIND_FILE[kind]} has not been extracted into the workspace yet.`),
    el("button", {
      class: "btn primary",
      onclick: () => startJob(`/api/extract/${kind}`, {}, () => { S.data[kind] = null; show(kind); }),
    }, `Extract ${KIND_FILE[kind]}`),
  ));
  const con = el("div", { class: "console", id: "build-console", style: "max-height:300px" });
  S.buildLog.forEach(l => con.append(renderLogLine(l)));
  main.append(con);
}

const checkCache = new Map();
async function batchCheck(texts, bank) {
  const missing = texts.filter(t => !checkCache.has(bank + " " + t));
  if (missing.length) {
    const res = await api("/api/check", { texts: missing, bank });
    missing.forEach((t, i) => checkCache.set(bank + " " + t, res.results[i]));
  }
  return texts.map(t => checkCache.get(bank + " " + t));
}

function byteMeter(span, origBytes, check) {
  if (!check) { span.textContent = ""; return; }
  if (!check.ok) {
    span.className = "bytes-err";
    span.textContent = "✖ cannot encode: " + (check.error || "");
    return;
  }
  const delta = check.bytes - (origBytes ?? check.bytes);
  const dtxt = delta === 0 ? "±0" : (delta > 0 ? "+" + delta : String(delta));
  span.className = delta > 0 ? "bytes-warn" : "bytes-ok";
  span.textContent = `${check.bytes} B (${dtxt})`;
  span.title = "";
  if (check.customChars) {
    span.textContent += ` ⚠ ${check.customChars} custom glyph(s)`;
    span.title = "These characters aren't in the standard tables — they only " +
      "display correctly if a 12x12 tile for them already exists in the " +
      "call/entry graphics data (or you add one via your glyph workflow).";
  }
}

function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(220, ta.scrollHeight + 2) + "px";
}

function previewPane(kind) {
  const canvas = el("canvas");
  const info = el("div", { class: "cf-info" });
  const frame = el("div", { class: "codec-frame" },
    el("div", { class: "cf-label" },
      kind === "radio" ? "Codec window preview" : "Subtitle preview"),
    canvas, info);
  const render = (text) => {
    const res = FontR.draw(canvas, toDisplay(text || "", kind), previewWidth(kind));
    const bits = [];
    bits.push(FontR.ready ? "game font (STAGE.DIR)" : "fallback font — set STAGE.DIR for real glyphs");
    const maxL = KIND_MAXLINES[kind];
    bits.push(`${res.lines} line(s) / max ${maxL}`);
    if (res.lines > maxL) bits.push(`⚠ over ${maxL} lines — will clip in-game`);
    if (res.overflow) bits.push("⚠ line wider than window");
    info.innerHTML = bits.join("<br>");
  };
  render("");
  return { frame, render };
}

function mtButton(texts, apply, kind) {
  const cfg = S.state.project;
  if (!cfg || cfg.mtProvider === "none") return null;
  return el("button", {
    class: "btn small",
    title: "Machine-translate via " + cfg.mtProvider,
    onclick: async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true;
      try {
        const res = await api("/api/translate",
          { texts: texts().map(t => toDisplay(t, kind)) });
        apply(res.translations.map(t => toStored(t, kind)));
      } catch (e) { toast(e.message, "error"); }
      btn.disabled = false;
    },
  }, "MT");
}

/* ── demo / vox / zmovie editor ───────────────────────────────────────── */

async function renderKindEditor(main, kind) {
  let data;
  try { data = await ensureData(kind); }
  catch (e) { main.append(el("div", { class: "panel red" }, e.message)); return; }
  if (!data.extracted) { extractPanel(main, kind); return; }

  const { original, work } = data;
  const fps = S.state.project.fps || 30;
  const entries = Object.keys(original);
  if (!S.sel[kind] || !entries.includes(S.sel[kind])) S.sel[kind] = entries[0];

  main.append(el("h2", null, KIND_LABEL[kind],
    el("span", { class: "muted", style: "font-size:11px;letter-spacing:0;margin-left:10px;text-transform:none" },
      `${KIND_FILE[kind]} — timings are frames (~${fps} fps)`)));

  const editor = el("div", { class: "editor" });
  main.append(editor);

  /* entry list */
  const listBody = el("div", { class: "list-body" });
  const searchIn = el("input", {
    type: "text", placeholder: "Search text…", value: S.search[kind] || "",
    oninput: debounce(() => { S.search[kind] = searchIn.value; fillList(); }, 200),
  });
  editor.append(el("div", { class: "editor-list" },
    el("div", { class: "list-head" }, searchIn), listBody));

  function entryStats(name) {
    const subs = original[name];
    const wsubs = work[name] || {};
    let changed = 0;
    for (const k of Object.keys(subs))
      if (wsubs[k] && subDiffers(subs[k], wsubs[k])) changed++;
    return { total: Object.keys(subs).length, changed };
  }

  function matches(name, term) {
    if (!term) return true;
    term = term.toLowerCase();
    const subs = original[name], wsubs = work[name] || {};
    return Object.keys(subs).some(k =>
      (subs[k].text || "").toLowerCase().includes(term) ||
      ((wsubs[k] || {}).text || "").toLowerCase().includes(term));
  }

  function fillList() {
    listBody.innerHTML = "";
    for (const name of entries) {
      if (!matches(name, S.search[kind])) continue;
      const stats = entryStats(name);
      const item = el("button", {
        class: "entry-item" + (S.sel[kind] === name ? " active" : ""),
        onclick: () => { S.sel[kind] = name; fillList(); fillRows(); },
      },
        el("div", { class: "e-title" }, name),
        el("div", { class: "e-sub" },
          el("span", null, `${stats.total} lines`),
          el("span", { class: stats.changed === stats.total && stats.total ? "green" : "" },
            `${stats.changed}/${stats.total}`)),
        el("div", { class: "progress" },
          el("div", { style: `width:${stats.total ? (100 * stats.changed / stats.total) : 0}%` })),
      );
      listBody.append(item);
    }
  }

  /* main rows */
  const editorMain = el("div", { class: "editor-main" });
  const rows = el("div", { class: "editor-rows" });
  const toolbar = el("div", { class: "editor-toolbar" });
  editorMain.append(toolbar, rows);
  editor.append(editorMain);

  /* side: preview + capacity */
  const side = el("div", { class: "editor-side" });
  const pv = previewPane(kind);
  side.append(pv.frame);
  let capBox = null;
  if (kind === "zmovie") {
    capBox = el("div", { class: "panel", style: "margin:0" },
      el("h3", { style: "margin-top:0" }, "Subtitle block budget"),
      el("div", { class: "cf-info mono muted" }, "…"));
    side.append(capBox);
  }
  editor.append(side);

  const checkCapacity = debounce(async () => {
    if (!capBox) return;
    const entry = S.sel[kind];
    try {
      const cap = await api("/api/zmovie/capacity", { entry, subs: work[entry] || original[entry] });
      const box = capBox.lastChild;
      if (cap.error) { box.innerHTML = `<span class="red">✖ ${cap.error}</span>`; return; }
      const pct = Math.round(100 * cap.used / cap.capacity);
      const cls = cap.used > cap.capacity ? "red" : (pct > 90 ? "amber" : "green");
      box.innerHTML = `<span class="${cls}">${cap.used} / ${cap.capacity} bytes (${pct}%)</span>` +
        `<br>${cap.chunks} subtitle block(s) in this entry` +
        (cap.used > cap.capacity ? "<br><span class='red'>⚠ will NOT fit — shorten text</span>" : "");
    } catch (e) { /* entry may not exist in file */ }
  }, 500);

  function fillRows() {
    rows.innerHTML = "";
    toolbar.innerHTML = "";
    const entry = S.sel[kind];
    const subs = original[entry] || {};
    if (!work[entry]) work[entry] = JSON.parse(JSON.stringify(subs));
    const wsubs = work[entry];

    /* toolbar */
    toolbar.append(el("span", { class: "mono green" }, entry));
    const untrans = Object.keys(subs).filter(k => !subDiffers(subs[k], wsubs[k] || subs[k]));
    const mtAll = mtButton(
      () => untrans.map(k => subs[k].text),
      (translations) => {
        untrans.forEach((k, i) => {
          wsubs[k] = wsubs[k] || JSON.parse(JSON.stringify(subs[k]));
          wsubs[k].text = translations[i];
        });
        markDirty(kind); fillRows(); fillList();
      }, kind);
    if (mtAll && untrans.length) toolbar.append(mtAll, el("span", { class: "muted", style: "font-size:11px" }, `${untrans.length} untranslated`));
    toolbar.append(el("span", { class: "spacer" }));
    toolbar.append(el("button", { class: "btn small", onclick: () => window.open(`/api/export/${kind}`) }, "Export CSV"));
    toolbar.append(el("button", {
      class: "btn small primary", onclick: () => saveWork(kind),
    }, "Save  (Ctrl+S)"));

    const frameKeys = Object.keys(subs).sort((a, b) => Number(a) - Number(b));
    const checkTexts = [];

    for (const key of frameKeys) {
      const osub = subs[key];
      if (!wsubs[key]) wsubs[key] = JSON.parse(JSON.stringify(osub));
      const wsub = wsubs[key];

      const meter = el("span", null);
      const ta = el("textarea", { spellcheck: "false" });
      ta.value = toDisplay(wsub.text, kind);

      const durIn = el("input", { class: "timing-input", type: "text", value: wsub.duration });
      const startIn = el("input", {
        class: "timing-input", type: "text",
        value: wsub.start || key, title: "Start frame (edit to retime)",
      });

      const rowEl = el("div", { class: "subrow" + (subDiffers(osub, wsub) ? " modified" : "") },
        el("div", { class: "sr-head" },
          el("span", null, "▶ frame ", el("b", null, key), ` (${fmtTime(key, fps)})`),
          el("span", null, "start:"), startIn,
          el("span", null, "dur:"), durIn,
          el("span", { class: "muted" }, `(${fmtTime(wsub.duration, fps)})`),
        ),
        el("div", {
          class: "sr-orig", title: "Original — click to copy into the editor",
          onclick: () => { ta.value = toDisplay(osub.text, kind); ta.dispatchEvent(new Event("input")); },
        }, toDisplay(osub.text, kind)),
        ta,
        el("div", { class: "sr-meta" },
          meter,
          el("span", { class: "grow" }),
          mtButton(() => [osub.text], (tr) => { ta.value = toDisplay(tr[0], kind); ta.dispatchEvent(new Event("input")); }, kind),
          el("button", {
            class: "btn small", title: "Revert to original",
            onclick: () => {
              ta.value = toDisplay(osub.text, kind);
              durIn.value = osub.duration; startIn.value = key;
              ta.dispatchEvent(new Event("input"));
              durIn.dispatchEvent(new Event("change"));
              startIn.dispatchEvent(new Event("change"));
            },
          }, "↺"),
        ),
      );

      const liveCheck = debounce(async () => {
        try {
          const [oc, nc] = await batchCheck([osub.text, wsub.text], KIND_BANK[kind]);
          byteMeter(meter, oc.ok ? oc.bytes : null, nc);
        } catch {}
        checkCapacity();
      }, 400);

      ta.addEventListener("input", () => {
        wsub.text = toStored(ta.value, kind);
        rowEl.classList.toggle("modified", subDiffers(osub, wsub));
        markDirty(kind);
        autoGrow(ta);
        pv.render(wsub.text);
        liveCheck();
      });
      ta.addEventListener("focus", () => pv.render(wsub.text));
      durIn.addEventListener("change", () => {
        wsub.duration = durIn.value.trim();
        rowEl.classList.toggle("modified", subDiffers(osub, wsub));
        markDirty(kind); checkCapacity();
      });
      startIn.addEventListener("change", () => {
        const v = startIn.value.trim();
        if (v === key) delete wsub.start; else wsub.start = v;
        rowEl.classList.toggle("modified", subDiffers(osub, wsub));
        markDirty(kind);
      });

      rows.append(rowEl);
      requestAnimationFrame(() => autoGrow(ta));
      checkTexts.push({ meter, osub, wsub });
    }

    /* one batched validation pass for the visible entry */
    (async () => {
      try {
        const texts = checkTexts.flatMap(c => [c.osub.text, c.wsub.text]);
        const res = await batchCheck(texts, KIND_BANK[kind]);
        checkTexts.forEach((c, i) => {
          const oc = res[i * 2], nc = res[i * 2 + 1];
          byteMeter(c.meter, oc && oc.ok ? oc.bytes : null, nc);
        });
      } catch {}
    })();
    checkCapacity();
  }

  fillList();
  fillRows();
}

function subDiffers(osub, wsub) {
  if (!wsub) return false;
  return wsub.text !== osub.text || String(wsub.duration) !== String(osub.duration) ||
    (wsub.start !== undefined && wsub.start !== "");
}

/* ── radio editor ─────────────────────────────────────────────────────── */

const RADIO_SECTIONS = [
  ["calls", "Codec calls"],
  ["freqAdd", "Codec contacts"],
  ["prompts", "Prompts"],
  ["saves", "Save slots"],
];

async function renderRadio(main) {
  let data;
  try { data = await ensureData("radio"); }
  catch (e) { main.append(el("div", { class: "panel red" }, e.message)); return; }
  if (!data.extracted) { extractPanel(main, "radio"); return; }

  const { original, work, meta } = data;
  if (!S.sel.radio) S.sel.radio = { section: "calls", key: null };
  const sel = S.sel.radio;

  main.append(el("h2", null, "Radio — codec dialogue"));
  const editor = el("div", { class: "editor" });
  main.append(editor);

  /* left: section selector + call list */
  const sectionSel = el("select", { style: "width:100%" },
    ...RADIO_SECTIONS.map(([v, l]) =>
      el("option", { value: v, selected: sel.section === v ? "" : null },
        l + ` (${Object.keys(original[v] || {}).length})`)));
  const searchIn = el("input", {
    type: "text", placeholder: "Search text / freq…", value: S.search.radio || "",
    style: "margin-top:6px",
    oninput: debounce(() => { S.search.radio = searchIn.value; fillList(); }, 250),
  });
  const listBody = el("div", { class: "list-body" });
  editor.append(el("div", { class: "editor-list" },
    el("div", { class: "list-head" }, sectionSel, searchIn), listBody));
  sectionSel.addEventListener("change", () => {
    sel.section = sectionSel.value; sel.key = null; fillList(); fillRows();
  });

  const editorMain = el("div", { class: "editor-main" });
  const toolbar = el("div", { class: "editor-toolbar" });
  const rows = el("div", { class: "editor-rows" });
  editorMain.append(toolbar, rows);
  editor.append(editorMain);

  const side = el("div", { class: "editor-side" });
  const pv = previewPane("radio");
  const callInfo = el("div", { class: "panel", style: "margin:0" },
    el("h3", { style: "margin-top:0" }, "Call info"),
    el("div", { class: "cf-info mono muted" }, "select a call"));
  side.append(pv.frame, callInfo);
  editor.append(side);

  function callStats(off) {
    const voxes = original.calls[off];
    let total = 0, changed = 0;
    for (const [vox, subs] of Object.entries(voxes))
      for (const [so, text] of Object.entries(subs)) {
        total++;
        const cur = ((work.calls || {})[off] || {})[vox]?.[so];
        if (cur !== undefined && cur !== text) changed++;
      }
    return { total, changed };
  }

  function callMatches(off, term) {
    if (!term) return true;
    term = term.toLowerCase();
    const m = meta[off];
    if (m && m.freq && String(m.freq).includes(term)) return true;
    for (const [vox, subs] of Object.entries(original.calls[off]))
      for (const [so, text] of Object.entries(subs)) {
        if (text.toLowerCase().includes(term)) return true;
        const cur = ((work.calls || {})[off] || {})[vox]?.[so];
        if (cur && cur.toLowerCase().includes(term)) return true;
      }
    return false;
  }

  function fillList() {
    listBody.innerHTML = "";
    if (sel.section === "calls") {
      const offs = Object.keys(original.calls)
        .sort((a, b) => Number(a) - Number(b));
      if (!sel.key || !original.calls[sel.key]) sel.key = offs[0];
      for (const off of offs) {
        if (!callMatches(off, S.search.radio)) continue;
        const m = meta[off] || {};
        const st = callStats(off);
        listBody.append(el("button", {
          class: "entry-item" + (sel.key === off ? " active" : ""),
          onclick: () => { sel.key = off; fillList(); fillRows(); },
        },
          el("div", { class: "e-title" }, m.freq ? `📡 ${m.freq}` : `call @ ${off}`),
          el("div", { class: "e-sub" },
            el("span", null, `@${off}`),
            el("span", { class: st.changed === st.total && st.total ? "green" : "" }, `${st.changed}/${st.total}`)),
          el("div", { class: "progress" },
            el("div", { style: `width:${st.total ? 100 * st.changed / st.total : 0}%` })),
        ));
      }
    } else {
      // flat sections render all rows at once; the list shows the section
      listBody.append(el("div", { style: "padding:10px", class: "muted" },
        "All entries shown on the right."));
      sel.key = null;
    }
  }

  function makeRow(origText, getCur, setCur, headBits) {
    const meter = el("span", null);
    const ta = el("textarea", { spellcheck: "false" });
    ta.value = toDisplay(getCur());
    const rowEl = el("div", { class: "subrow" + (getCur() !== origText ? " modified" : "") },
      el("div", { class: "sr-head" }, ...headBits),
      el("div", {
        class: "sr-orig", title: "Original — click to copy into the editor",
        onclick: () => { ta.value = toDisplay(origText); ta.dispatchEvent(new Event("input")); },
      }, toDisplay(origText)),
      ta,
      el("div", { class: "sr-meta" },
        meter,
        el("span", { class: "grow" }),
        mtButton(() => [origText], (tr) => { ta.value = toDisplay(tr[0]); ta.dispatchEvent(new Event("input")); }),
        el("button", {
          class: "btn small", title: "Revert to original",
          onclick: () => { ta.value = toDisplay(origText); ta.dispatchEvent(new Event("input")); },
        }, "↺"),
      ));
    const liveCheck = debounce(async () => {
      try {
        const [oc, nc] = await batchCheck([origText, getCur()], 1);
        byteMeter(meter, oc.ok ? oc.bytes : null, nc);
        updateCallInfo();
      } catch {}
    }, 400);
    ta.addEventListener("input", () => {
      setCur(toStored(ta.value));
      rowEl.classList.toggle("modified", getCur() !== origText);
      markDirty("radio");
      autoGrow(ta);
      pv.render(getCur());
      liveCheck();
    });
    ta.addEventListener("focus", () => pv.render(getCur()));
    requestAnimationFrame(() => autoGrow(ta));
    return { rowEl, meter, origText, getCur };
  }

  let rowChecks = [];

  async function runBatchChecks() {
    if (!rowChecks.length) return;
    try {
      const texts = rowChecks.flatMap(c => [c.origText, c.getCur()]);
      const res = await batchCheck(texts, 1);
      rowChecks.forEach((c, i) => {
        const oc = res[i * 2], nc = res[i * 2 + 1];
        byteMeter(c.meter, oc && oc.ok ? oc.bytes : null, nc);
      });
      updateCallInfo();
    } catch {}
  }

  async function updateCallInfo() {
    const box = callInfo.lastChild;
    if (sel.section !== "calls" || !sel.key) { box.textContent = "—"; return; }
    const m = meta[sel.key] || {};
    let delta = 0, encodable = true;
    for (const c of rowChecks) {
      const oc = checkCache.get("1 " + c.origText);
      const nc = checkCache.get("1 " + c.getCur());
      if (oc && nc && oc.ok && nc.ok) delta += nc.bytes - oc.bytes;
      if (nc && !nc.ok) encodable = false;
    }
    const newLen = (m.length || 0) + delta;
    const pct = m.length ? Math.round(100 * newLen / 65535) : 0;
    const longMode = !!(S.state.project.radioFlags || {}).long;
    const cls = newLen > 65535 ? (longMode ? "amber" : "red") : (pct > 90 ? "amber" : "green");
    let overMsg = "";
    if (newLen > 65535) {
      overMsg = longMode
        ? "<br><span class='amber'>over 64 KB — OK with 4-byte lengths (-l), requires the patched executable</span>"
        : "<br><span class='red'>⚠ exceeds the 2-byte length limit — enable the -l build flag " +
          "(needs the patched executable) or shorten text</span>";
    }
    box.innerHTML =
      `freq <b>${m.freq || "?"}</b> — offset ${sel.key}` +
      `<br>${m.subtitles || rowChecks.length} subtitle(s)` +
      (m.length ? `<br>call size ≈ <span class="${cls}">${newLen.toLocaleString()}</span> / 65,535 bytes (${pct}%)` : "") +
      overMsg +
      (!encodable ? "<br><span class='red'>⚠ a line cannot be encoded</span>" : "");
  }

  function fillRows() {
    rows.innerHTML = "";
    toolbar.innerHTML = "";
    rowChecks = [];
    work.calls = work.calls || {};

    toolbar.append(el("span", { class: "mono green" },
      sel.section === "calls"
        ? (meta[sel.key] ? `freq ${meta[sel.key].freq} @ ${sel.key}` : `call @ ${sel.key}`)
        : RADIO_SECTIONS.find(s => s[0] === sel.section)[1]));
    toolbar.append(el("span", { class: "spacer" }));
    toolbar.append(el("button", { class: "btn small", onclick: () => window.open("/api/export/radio") }, "Export CSV"));
    toolbar.append(el("button", { class: "btn small primary", onclick: () => saveWork("radio") }, "Save  (Ctrl+S)"));

    if (sel.section === "calls") {
      if (!sel.key) return;
      const voxes = original.calls[sel.key];
      work.calls[sel.key] = work.calls[sel.key] || {};
      const wcall = work.calls[sel.key];

      // MT for untranslated lines in this call
      const untranslated = [];
      for (const [vox, subs] of Object.entries(voxes))
        for (const [so, text] of Object.entries(subs))
          if ((wcall[vox]?.[so] ?? text) === text) untranslated.push([vox, so, text]);
      const mtAll = mtButton(
        () => untranslated.map(u => u[2]),
        (tr) => {
          untranslated.forEach(([vox, so], i) => {
            wcall[vox] = wcall[vox] || {};
            wcall[vox][so] = tr[i];
          });
          markDirty("radio"); fillRows(); fillList();
        });
      if (mtAll && untranslated.length) {
        toolbar.insertBefore(el("span", { class: "muted", style: "font-size:11px" }, `${untranslated.length} untranslated`), toolbar.children[1]);
        toolbar.insertBefore(mtAll, toolbar.children[1]);
      }

      for (const [vox, subs] of Object.entries(voxes)) {
        wcall[vox] = wcall[vox] || {};
        rows.append(el("div", { class: "muted mono", style: "font-size:11px;padding:2px 4px" },
          vox === "none" ? "— no VOX group —" : `VOX group @ ${vox}`));
        for (const [so, text] of Object.entries(subs)) {
          const row = makeRow(
            text,
            () => wcall[vox][so] ?? text,
            (v) => { wcall[vox][so] = v; },
            [el("span", null, "sub @ ", el("b", null, so))]);
          rows.append(row.rowEl);
          rowChecks.push(row);
        }
      }
    } else if (sel.section === "freqAdd") {
      work.freqAdd = work.freqAdd || {};
      for (const [key, text] of Object.entries(original.freqAdd || {})) {
        const row = makeRow(text,
          () => work.freqAdd[key] ?? text,
          (v) => { work.freqAdd[key] = v; },
          [el("span", null, "contact name @ ", el("b", null, key))]);
        rows.append(row.rowEl);
        rowChecks.push(row);
      }
    } else {
      work[sel.section] = work[sel.section] || {};
      for (const [key, entries2] of Object.entries(original[sel.section] || {})) {
        work[sel.section][key] = work[sel.section][key] || {};
        rows.append(el("div", { class: "muted mono", style: "font-size:11px;padding:2px 4px" },
          `${sel.section === "saves" ? "save block" : "prompt"} @ ${key}`));
        for (const [idx, text] of Object.entries(entries2)) {
          const row = makeRow(text,
            () => work[sel.section][key][idx] ?? text,
            (v) => { work[sel.section][key][idx] = v; },
            [el("span", null, "option ", el("b", null, idx))]);
          rows.append(row.rowEl);
          rowChecks.push(row);
        }
      }
    }
    runBatchChecks();
  }

  fillList();
  fillRows();
}

/* ── charset view ─────────────────────────────────────────────────────── */

async function renderCharset(main) {
  main.append(el("h2", null, "Character sets"));
  if (!S.charset) {
    try { S.charset = await api("/api/charset"); }
    catch (e) { main.append(el("div", { class: "panel red" }, e.message)); return; }
  }
  const cs = S.charset;

  main.append(el("div", { class: "panel muted", style: "font-size:12.5px" },
    "Every character the encoder knows. Anything not listed here gets dynamically ",
    "allocated as a custom glyph at recompile time (watch the ⚠ custom glyph ",
    "warnings in the editors). Kinsoku markers ‹BK›/‹TK› are line-break hints — keep them ",
    "with the character they follow, or delete the pair knowingly."));

  for (const section of cs.sections) {
    const grid = el("div", { class: "charset-grid" });
    for (const entry of section.entries) {
      grid.append(el("div", { class: "charset-cell", title: `0x${entry.code}` },
        el("div", { class: "cc-glyph" }, entry.char),
        el("div", { class: "cc-code" }, entry.code)));
    }
    main.append(el("h3", null, `${section.name} — ${section.entries.length}`), grid);
  }

  if (cs.customTiles && cs.customTiles.length) {
    const grid = el("div", { class: "charset-grid" });
    const tileSearch = el("input", { type: "text", placeholder: "Filter by character…", style: "margin-bottom:8px" });
    let shown = 0;
    const PAGE = 400;
    const moreBtn = el("button", { class: "btn small", style: "margin-top:8px" }, "Show more");
    const renderTiles = (reset) => {
      if (reset) { grid.innerHTML = ""; shown = 0; }
      const term = tileSearch.value.trim();
      const list = term ? cs.customTiles.filter(t => (t.char || "").includes(term)) : cs.customTiles;
      const slice = list.slice(shown, shown + PAGE);
      for (const tile of slice) {
        const canvas = el("canvas");
        drawTile(canvas, tile.hex);
        grid.append(el("div", { class: "charset-cell", title: tile.char },
          canvas, el("div", { class: "cc-code" }, tile.char || "?")));
      }
      shown += slice.length;
      moreBtn.classList.toggle("hidden", shown >= list.length);
      moreBtn.textContent = `Show more (${list.length - shown} left)`;
    };
    tileSearch.addEventListener("input", debounce(() => renderTiles(true), 250));
    moreBtn.addEventListener("click", () => renderTiles(false));
    main.append(el("h3", null,
      `Identified custom glyph tiles (per-call kanji) — ${cs.customTiles.length}`),
      tileSearch, grid, moreBtn);
    renderTiles(true);
  }

  if (FontR.ready) {
    const grid = el("div", { class: "charset-grid" });
    FontR.kana.forEach((pix, slot) => {
      const canvas = el("canvas");
      canvas.width = 12; canvas.height = 12;
      const ctx = canvas.getContext("2d");
      for (let y = 0; y < 12; y++) for (let x = 0; x < 12; x++) {
        const v = pix[y * 12 + x];
        if (v) { ctx.fillStyle = FontR.palette[v]; ctx.fillRect(x, y, 1, 1); }
      }
      grid.append(el("div", { class: "charset-cell", title: `font slot ${slot}` },
        canvas, el("div", { class: "cc-code" }, String(slot))));
    });
    main.append(el("h3", null, `STAGE.DIR font glyphs — ${FontR.kana.length}`), grid);
  }
}

/* ── build view ───────────────────────────────────────────────────────── */

function renderBuild(main) {
  main.append(el("h2", null, "Build"));
  const cfg = S.state.project;
  const status = S.state.status;

  const grid = el("div", { class: "build-grid" });
  for (const kind of KINDS) {
    const st = status[kind] || {};
    const lines = [];
    lines.push(st.extracted ? `extracted ✓ — ${st.total} lines` : "not extracted");
    if (st.extracted) lines.push(`${st.translated} line(s) changed`);
    lines.push(st.built ? `built ✓  ${fmtDate(st.builtTime)}` : "not built");
    grid.append(el("div", { class: "build-card" },
      el("h4", null, KIND_FILE[kind]),
      el("div", { class: "bc-status" }, lines.map(l => el("div", null, l))),
      el("div", { class: "row" },
        st.extracted
          ? el("button", {
              class: "btn small primary",
              disabled: S.state.jobsRunning ? "" : null,
              onclick: () => startJob(`/api/build/${kind}`),
            }, "Build")
          : el("button", {
              class: "btn small",
              disabled: S.state.jobsRunning ? "" : null,
              onclick: () => startJob(`/api/extract/${kind}`, {}, () => { S.data[kind] = null; }),
            }, "Extract"),
        st.extracted ? el("button", {
          class: "btn small",
          disabled: S.state.jobsRunning ? "" : null,
          title: "Re-extract from the source file (working translations are kept)",
          onclick: () => startJob(`/api/extract/${kind}`, {}, () => { S.data[kind] = null; }),
        }, "Re-extract") : null,
      )));
  }
  main.append(grid);

  /* radio recompiler flags */
  const flags = cfg.radioFlags || {};
  const flagDefs = [
    ["integral", "Integral disc (-I: 0x800-aligned calls, block-index STAGE.DIR)"],
    ["pad", "Pad calls to 0x800 (-P)"],
    ["long", "4-byte length fields (-l, patched executable only)"],
    ["doubleWidth", "Double-width save blocks (-D, matches original encoding)"],
  ];
  const flagPanel = el("div", { class: "panel" }, el("h3", { style: "margin-top:0" }, "Radio recompiler options"));
  for (const [key, label] of flagDefs) {
    const cb = el("input", { type: "checkbox" });
    cb.checked = !!flags[key];
    cb.addEventListener("change", () => {
      const nf = { ...(S.state.project.radioFlags || {}) };
      nf[key] = cb.checked;
      updateProject({ radioFlags: nf });
      S.state.project.radioFlags = nf;
    });
    flagPanel.append(el("label", { class: "row", style: "font-size:12.5px;margin:4px 0;cursor:pointer" }, cb, label));
  }
  main.append(flagPanel);

  main.append(el("div", { class: "panel", style: "display:flex;gap:12px;align-items:center" },
    el("button", {
      class: "btn primary",
      disabled: S.state.jobsRunning ? "" : null,
      onclick: () => startJob("/api/build/all"),
    }, "▶ Build everything"),
    el("span", { class: "muted", style: "font-size:12px" },
      "Order: DEMO → VOX → RADIO (+ STAGE.DIR offsets) → ZMOVIE. ",
      "Built files land in ",
      el("span", { class: "mono" }, "undub-workspace/out/"),
      " — copy them over the originals in your build folder, then rebuild the ISO with mkpsxiso."),
  ));

  const con = el("div", { class: "console", id: "build-console" });
  S.buildLog.forEach(l => con.append(renderLogLine(l)));
  con.scrollTop = con.scrollHeight;
  main.append(con);
}

/* ── go ───────────────────────────────────────────────────────────────── */

boot().catch(e => toast("Failed to start: " + e.message, "error"));
