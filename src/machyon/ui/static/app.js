// Machyon UI. Plain modules, no framework, no external requests. Every number shown comes from the server.
"use strict";

const main = document.getElementById("main");
const announcer = document.getElementById("announce");
const TOKEN_KEY = "machyon-ui-token";
let token = takeToken();
let state = null;          // GET /api/state
let pollTimer = null;      // the current page's refresh loop
let livePollTimer = null;  // the header's "Downloading…" pill

// ------------------------------------------------------------------ tiny DOM helpers

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const SVG = "http://www.w3.org/2000/svg";
const ICONS = {
  plus: "M12 5v14M5 12h14",
  upload: "M12 16V4m0 0-4 4m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2",
  copy: "M9 9h10v10H9zM5 15V5h10",
  stop: "M7 7h10v10H7z",
  gauge: "M12 14l4-4M4.9 19a9 9 0 1 1 14.2 0",
  retry: "M4 4v6h6M20 20v-6h-6M5.6 15A7 7 0 0 0 18 17M18.4 9A7 7 0 0 0 6 7",
  shield: "M12 3l8 3v6c0 4.4-3.4 8.4-8 9-4.6-.6-8-4.6-8-9V6z M9 12l2 2 4-4",
  ledger: "M6 3h9l3 3v15H6zM9 9h6M9 13h6M9 17h4",
  info: "M12 8h.01M11 12h1v5h1M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  warn: "M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  sun: "M12 4V2m0 20v-2m8-8h2M2 12h2m13.7-5.7 1.4-1.4M4.9 19.1l1.4-1.4m11.4 0 1.4 1.4M4.9 4.9l1.4 1.4M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
};
function icon(name) {
  const s = document.createElementNS(SVG, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "2");
  s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(SVG, "path");
  p.setAttribute("d", ICONS[name]);
  s.append(p);
  return s;
}
function opa(cls) {
  return [h("img", { class: `${cls || ""} on-light`, src: "/static/opa-light.svg", alt: "" }),
          h("img", { class: `${cls || ""} on-dark`, src: "/static/opa-dark.svg", alt: "" })];
}

const nf = new Intl.NumberFormat();
const int = (n) => (n === null || n === undefined ? "–" : nf.format(n));
function bytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
}
function duration(s) {
  if (s === null || s === undefined) return "–";
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${Math.round(s % 60)} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
function when(iso) {
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
const plural = (n, one, many) => `${int(n)} ${n === 1 ? one : many || one + "s"}`;

function say(text) { announcer.textContent = ""; setTimeout(() => (announcer.textContent = text), 50); }
let toastTimer;
function toast(text) {
  const t = document.getElementById("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}
async function copy(text, what) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = h("textarea", { class: "sr", value: text });
    document.body.append(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  toast(`${what} copied`);
}

// ------------------------------------------------------------------ session and API

function takeToken() {
  const m = location.hash.match(/token=([A-Za-z0-9_-]{20,})/);
  try {
    if (m) {
      sessionStorage.setItem(TOKEN_KEY, m[1]);
      history.replaceState(null, "", location.pathname + "#/");   // the key leaves the address bar at once
    }
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return m ? m[1] : null;
  }
}

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function api(path, opts = {}) {
  const headers = { "X-Machyon-Token": token || "" };
  let body = opts.body;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.json); }
  if (opts.type) headers["Content-Type"] = opts.type;
  let r;
  try {
    r = await fetch(path, { method: opts.method || "GET", headers, body, cache: "no-store", credentials: "same-origin" });
  } catch {
    throw new ApiError("Machyon UI isn't answering. Is it still running in the terminal?", 0);
  }
  const data = (r.headers.get("content-type") || "").includes("json") ? await r.json() : null;
  if (r.status === 401) { locked(); throw new ApiError(data && data.error, 401); }
  if (!r.ok) throw new ApiError((data && data.error) || `${r.status} ${r.statusText}`, r.status);
  return data;
}

function upload(file, onProgress) {   // XHR: fetch can't report upload progress
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", "/api/sources/upload");
    x.setRequestHeader("X-Machyon-Token", token || "");
    x.responseType = "json";
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () => {
      if (x.status === 401) { locked(); return reject(new ApiError("session ended", 401)); }
      if (x.status >= 200 && x.status < 300) resolve(x.response);
      else reject(new ApiError((x.response && x.response.error) || `Upload failed (${x.status})`, x.status));
    };
    x.onerror = () => reject(new ApiError("The upload didn't reach Machyon UI.", 0));
    const fd = new FormData();
    fd.append("file", file, file.name);
    x.send(fd);
  });
}

function locked() {
  stopPolling();
  document.querySelector(".nav").hidden = true;
  main.replaceChildren(h("section", { class: "locked fade-in" }, h("div", { class: "card" },
    opa(), h("h1", { text: "Open Machyon from your terminal" }),
    h("p", { class: "muted", text: "For your safety this page only works through the link Machyon printed when it " +
      "started (it carries a key for this session). Copy that link from the terminal window into this browser." }),
    h("p", { class: "faint" }, "Not running? Start it with ", h("code", { text: "machyon ui" }), "."))));
}

// ------------------------------------------------------------------ routing

const ROUTES = [
  [/^#\/?$/, home, "home"],
  [/^#\/new$/, newDownload, "new"],
  [/^#\/run\/(run_[0-9_-]+_[0-9a-f]{4})$/, runPage, null],
];

function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }

async function route() {
  stopPolling();
  if (!token) return locked();
  const hash = location.hash || "#/";
  const [re, view, nav] = ROUTES.find(([re]) => re.test(hash)) || ROUTES[0];
  for (const a of document.querySelectorAll("[data-nav]")) {
    if (a.dataset.nav === nav) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  const args = hash.match(re).slice(1);
  try {
    const el = await view(...args);
    main.replaceChildren(el);
    el.classList.add("fade-in");
  } catch (e) {
    if (e.status !== 401) main.replaceChildren(errorView(e));
  }
  if (route.done) main.focus({ preventScroll: true });
  route.done = true;
  window.scrollTo(0, 0);
}

function errorView(e) {
  return h("section", { class: "card" }, h("h2", { text: "Something went wrong" }),
    h("p", { class: "muted", text: e.message }), h("a", { class: "btn", href: "#/", text: "Back to start" }));
}

async function refreshState() {
  state = await api("/api/state");
  setLive(state.job);
  return state;
}

function setLive(job) {
  const pill = document.getElementById("live");
  const running = job && job.state === "running";
  pill.hidden = !running;
  if (running) {
    pill.href = `#/run/${job.run_id}`;
    const p = job.progress;
    pill.textContent = p ? (p.total ? `Downloading · ${int(p.accounted || p.downloaded + p.skipped + p.failed)} / ${int(p.total)}`
                                    : `Downloading · ${int(p.downloaded + p.skipped + p.failed)}`) : "Downloading…";
  }
  clearTimeout(livePollTimer);
  if (running) livePollTimer = setTimeout(async () => {
    try { setLive((await api("/api/job")).job); } catch { /* the next page load says why */ }
  }, document.hidden ? 4000 : 2000);
}

// ------------------------------------------------------------------ home

async function home() {
  await refreshState();
  const job = state.job && state.job.state === "running" ? state.job : null;
  const recent = state.recent;
  return h("div", { class: "stack" },
    h("section", { class: "card hero" },
      h("div", null,
        h("h1", { text: "Every image, accounted for." }),
        h("p", { class: "lead", text: "Paste links or upload a manifest. Machyon tunes its own speed, retries what " +
          "can recover, keeps only real images, and records why anything it couldn't get failed." }),
        h("div", { class: "actions" },
          h("a", { class: "btn primary", href: "#/new" }, icon("plus"), "New download"),
          job && h("a", { class: "btn", href: `#/run/${job.run_id}` }, icon("gauge"), "Watch the running download"))),
      h("div", { class: "hero-art" }, opa())),
    h("p", { class: "where" }, "Runs are saved in", h("span", { class: "path", text: state.root }),
      h("button", { class: "btn small ghost", type: "button", onclick: () => copy(state.root, "Folder path") },
        icon("copy"), "Copy")),
    h("div", { class: "grid2" },
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("h2", { text: "Recent runs" }), h("span", { class: "grow" }),
          state.runs > recent.length && h("span", { class: "faint", text: `${int(state.runs)} in total` })),
        recent.length ? h("ul", { class: "runs" }, recent.map((r) => h("li", null, runRow(r))))
          : h("div", { class: "empty" }, opa(), h("b", { text: "No runs yet" }),
              h("span", { text: "Your downloads will be listed here, and stay here after a restart." }))),
      h("section", { class: "card" }, h("h2", { text: "How Machyon keeps every image" }),
        h("ul", { class: "points" },
          point("gauge", "Finds the right speed", "Starts with a few requests at once and adds more while the server keeps up; backs off when it pushes back."),
          point("retry", "Retries what can recover", "Timeouts, dropped connections and busy servers are retried. A 404 isn't."),
          point("shield", "Keeps only real images", "Error pages and other non-images are refused, so every file on disk is an image."),
          point("ledger", "Records every failure", "Anything it couldn't get is listed with the reason, and can be retried later.")))));
}

function point(ic, title, text) {
  return h("li", null, h("span", { class: "ic" }, icon(ic)), h("div", null, h("b", { text: title }), h("span", { text })));
}

function sourceName(run) {
  return run.source.kind === "paste" ? "Pasted links" : run.source.original_name || "Manifest";
}

const STATUS = {
  running: ["Downloading", "run"], completed: ["Completed", "ok"], cancelled: ["Stopped", "warn"],
  interrupted: ["Interrupted", "warn"], error: ["Error", "bad"],
};
function statusTag(status, report) {
  let [label, cls] = STATUS[status] || [status, ""];
  if (status === "completed" && report && report.failed) { label = `Completed · ${int(report.failed)} failed`; cls = "warn"; }
  return h("span", { class: `tag ${cls}`, text: label });
}

function runRow(r) {
  const rep = r.report;
  const p = r.progress;
  const n = rep ? `${int(rep.downloaded + rep.skipped)} / ${int(rep.total)}`
                : p ? `${int(p.downloaded + p.skipped)} / ${p.total === null ? "?" : int(p.total)}` : "";
  return h("a", { class: "run-row", href: `#/run/${r.id}` }, statusTag(r.status, rep),
    h("div", null, h("div", { class: "t", text: sourceName(r) }), h("div", { class: "s", text: when(r.created) })),
    h("div", { class: "n", text: n, title: "images on disk / rows" }));
}

// ------------------------------------------------------------------ new download

const REFUSED = {
  unsafe_name: "refused: this name would land outside the run's folder",
  duplicate_name: "refused: another link already has this name",
  bad_name: "refused: the template can't name this row (is a column empty?)",
  invalid_url: "refused: not an http(s) link",
  repeat: "same link and name as an earlier row: downloaded once",
};

async function newDownload() {
  if (!state) await refreshState();
  const d = state.defaults;
  const L = state.limits;
  const s = { source: null, info: null, mode: "paste", names: "", settings: { ...d } };
  let namesTimer = null;
  let namesSeq = 0;

  const resultBox = h("div");
  const namesBox = h("div");
  const dockText = h("span", { class: "grow" });
  const startBtn = h("button", { class: "btn primary", type: "button", disabled: true, onclick: start }, icon("arrow"), "Start download");
  const status = h("p", { class: "faint", role: "status" });

  // ---- step 1: the source
  const ta = h("textarea", { "aria-label": "Image links, one per line", spellcheck: false,
    placeholder: "https://example.com/photos/cat.jpg\nhttps://example.com/photos/dog.png" });
  const pastePanel = h("div", { class: "stack" }, ta,
    h("div", { class: "toolbar" },
      h("button", { class: "btn", type: "button", onclick: checkPaste }, "Check links"),
      h("span", { class: "faint", text: `One link per line, up to ${int(L.paste_lines)}. Lines starting with # are ignored.` })));
  const fileInput = h("input", { type: "file", class: "sr", accept: L.formats.join(","), tabindex: "-1",
    onchange: () => fileInput.files[0] && sendFile(fileInput.files[0]) });
  const drop = h("div", { class: "drop" }, icon("upload"), h("b", { text: "Drop a manifest here" }),
    h("button", { class: "btn small", type: "button", onclick: () => fileInput.click() }, "Choose a file"),
    h("span", { class: "faint", text: `CSV, TSV, TXT, JSON, JSONL/NDJSON or Parquet · up to ${int(L.upload_mb)} MB (plain JSON: ${int(L.json_mb)} MB)` }),
    fileInput);
  for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) sendFile(f); });
  const uploadPanel = h("div", { hidden: true }, drop);

  const segBtns = [["paste", "Paste links"], ["upload", "Upload a file"]].map(([mode, label]) =>
    h("button", { type: "button", "aria-pressed": String(mode === s.mode), onclick: () => setMode(mode), text: label }));
  function setMode(mode) {
    s.mode = mode;
    segBtns.forEach((b, i) => b.setAttribute("aria-pressed", String(["paste", "upload"][i] === mode)));
    pastePanel.hidden = mode !== "paste";
    uploadPanel.hidden = mode !== "upload";
  }

  async function checkPaste() {
    const text = ta.value;
    if (!text.trim()) { status.textContent = "Paste at least one link first."; return; }
    setSource(null);
    status.textContent = "Checking…";
    try {
      setSource(await api("/api/sources/paste", { method: "POST", body: text, type: "text/plain" }));
      status.textContent = "";
    } catch (e) { status.textContent = ""; showProblem(e.message); }
  }

  async function sendFile(file) {
    setSource(null);
    const bar = h("i", { style: null });
    const meter = h("div", { class: "meter", role: "progressbar", "aria-label": "Upload" }, bar);
    resultBox.replaceChildren(h("p", { class: "muted", text: `Uploading ${file.name}…` }), meter);
    try {
      setSource(await upload(file, (f) => { bar.style.width = `${Math.round(f * 100)}%`; meter.setAttribute("aria-valuenow", Math.round(f * 100)); }));
    } catch (e) { showProblem(e.message); }
    fileInput.value = "";
  }

  function showProblem(msg) {
    resultBox.replaceChildren(h("div", { class: "note bad" }, icon("warn"), h("div", { text: msg })));
  }

  function setSource(data) {
    s.source = data ? data.source : null;
    s.info = data;
    resultBox.replaceChildren(data ? sourceView(data) : "");
    refreshNames();
    updateDock();
  }

  async function chooseColumn(col) {
    try {
      const data = await api("/api/sources/inspect", { method: "POST", json: { source: s.source, url_col: col } });
      Object.assign(data, { valid: s.info.valid, invalid: s.info.invalid, invalid_count: s.info.invalid_count });
      setSource(data);
    } catch (e) { showProblem(e.message); }
  }

  function sourceView(data) {
    const r = data.inspection;
    const parts = [];
    const head = h("div", { class: "toolbar" }, h("span", { class: "tag", text: r.format.toUpperCase() }),
      h("b", { text: data.name || "Pasted links" }), h("span", { class: "faint", text: bytes(data.size) }),
      data.valid !== undefined && h("span", { class: "faint", text: `· ${plural(data.valid, "link")}` }));
    parts.push(head);
    if (data.invalid_count) {
      parts.push(h("div", { class: "note warn" }, icon("warn"), h("div", null,
        h("b", { text: `${plural(data.invalid_count, "line")} ${data.invalid_count === 1 ? "isn't" : "aren't"} an http(s) link and will be skipped:` }),
        h("ul", null, data.invalid.slice(0, 8).map((x) => h("li", null, h("span", { class: "faint", text: `line ${x.line}: ` }), h("code", { text: x.text })))),
        data.invalid_count > 8 && h("span", { class: "faint", text: `…and ${int(data.invalid_count - 8)} more.` }))));
    }
    if (r.error && !["no_url_column", "unknown_column"].includes(r.error.kind)) {
      parts.push(h("div", { class: "note bad" }, icon("warn"), h("div", { text: r.error.message })));
      return h("div", { class: "stack" }, parts);
    }
    if (r.columns) {
      const sel = h("select", { onchange: () => chooseColumn(sel.value) },
        !r.url_col && h("option", { value: "", text: "Choose a column…", selected: true, disabled: true }),
        r.columns.map((c) => h("option", { value: c, text: c, selected: c === r.url_col })));
      parts.push(h("label", { class: "field" },
        r.error ? h("span", { class: "tag bad", text: "Which column holds the image links?" }) : "Column with the image links",
        sel,
        h("span", { class: "hint", text: r.error ? r.error.message
          : r.url_col_guessed ? "Found automatically. Change it if this isn't the right one." : "Chosen by you." })));
    }
    const cols = r.columns || Object.keys(r.rows[0] || { url: "" });
    if (r.rows.length) {
      parts.push(h("div", { class: "table-wrap" }, h("table", null,
        h("thead", null, h("tr", null, h("th", { text: "#" }), cols.map((c) => h("th", { class: (c === r.url_col || (!r.columns && c === "url")) ? "is-url" : "", text: c })))),
        h("tbody", null, r.rows.map((row, i) => h("tr", null, h("td", { class: "num", text: i + 1 }),
          cols.map((c) => h("td", { class: (c === r.url_col || (!r.columns && c === "url")) ? "is-url" : "", text: row[c] === null || row[c] === undefined ? "" : String(row[c]), title: String(row[c] ?? "") }))))))));
      parts.push(h("p", { class: "faint", text: r.more ? `The first ${r.rows.length} rows. There are more; all of them will be downloaded.` : `${plural(r.rows.length, "row")} in total.` }));
    }
    return h("div", { class: "stack" }, parts);
  }

  // ---- step 2: names
  const tpl = h("input", { type: "text", class: "template", placeholder: "{hash}{ext}", spellcheck: false, autocomplete: "off",
    "aria-describedby": "tpl-hint", oninput: () => { s.names = tpl.value.trim(); refreshNames(); } });
  const keyChips = h("div", { class: "chips", role: "group", "aria-label": "Insert a key" });
  function insertKey(k) {
    const at = tpl.selectionStart ?? tpl.value.length;
    tpl.value = tpl.value.slice(0, at) + `{${k}}` + tpl.value.slice(tpl.selectionEnd ?? at);
    tpl.focus();
    tpl.setSelectionRange(at + k.length + 2, at + k.length + 2);
    s.names = tpl.value.trim();
    refreshNames();
  }
  function refreshNames() {
    const cols = (s.info && s.info.inspection.columns) || [];
    keyChips.replaceChildren(...[...state.builtin_keys, ...cols].map((k) =>
      h("button", { type: "button", class: "chip", text: `{${k}}`, onclick: () => insertKey(k) })));
    clearTimeout(namesTimer);
    if (!s.source || !(s.info.inspection.ok)) { namesBox.replaceChildren(h("p", { class: "faint", text: "Add images above to see what each file will be called." })); updateDock(); return; }
    namesTimer = setTimeout(previewNames, 250);
  }
  async function previewNames() {
    const seq = ++namesSeq;
    try {
      const data = await api("/api/names", { method: "POST", json: { source: s.source, names: s.names } });
      if (seq !== namesSeq) return;
      s.namesOk = true;
      namesBox.replaceChildren(h("div", { class: "table-wrap" }, h("table", null,
        h("thead", null, h("tr", null, h("th", { text: "#" }), h("th", { text: "Link" }), h("th", { text: "File" }))),
        h("tbody", null, data.rows.map((r, i) => h("tr", null, h("td", { class: "num", text: i + 1 }),
          h("td", { text: r.url, title: r.url }),
          r.refused && r.refused !== "repeat" ? h("td", { class: "refused", text: REFUSED[r.refused] || r.refused })
            : h("td", { class: "mono", text: r.file + (r.refused ? `  (${REFUSED.repeat})` : ""), title: r.file })))))));
    } catch (e) {
      if (seq !== namesSeq) return;
      s.namesOk = false;
      namesBox.replaceChildren(h("div", { class: "note bad" }, icon("warn"), h("div", { text: e.message })));
    }
    updateDock();
  }

  // ---- step 3: settings
  const fields = {};
  function sw(key, label, hint) {
    const input = h("input", { type: "checkbox", checked: !!s.settings[key], onchange: () => { s.settings[key] = input.checked; syncSettings(); } });
    fields[key] = input;
    return h("label", { class: "switch" }, input, h("span", null, label, h("span", { class: "hint", text: hint })));
  }
  function num(key, label, hint, step) {
    const input = h("input", { type: "number", value: s.settings[key], step: step || 1, min: 0,
      oninput: () => { s.settings[key] = input.value === "" ? null : Number(input.value); syncSettings(); } });
    fields[key] = input;
    return h("label", { class: "field" }, label, input, hint && h("span", { class: "hint", text: hint }));
  }
  const settingsNote = h("p", { class: "faint", role: "status" });
  function syncSettings() {
    fields.concurrency.closest("label").firstChild.textContent = s.settings.adaptive ? "Starting concurrency" : "Concurrency";
    fields.max_concurrency.closest("label").hidden = !s.settings.adaptive;
    const bad = [];
    for (const [k, v] of Object.entries(s.settings)) if (v === null || Number.isNaN(v)) bad.push(k);
    if (s.settings.adaptive && s.settings.max_concurrency < s.settings.concurrency) bad.push("max");
    settingsNote.textContent = bad.length ? "Check the highlighted numbers." : "";
    for (const [k, input] of Object.entries(fields)) input.setAttribute("aria-invalid", String(bad.includes(k) || (k === "max_concurrency" && bad.includes("max"))));
    updateDock();
  }

  function updateDock() {
    const ready = s.source && s.info && s.info.inspection.ok && s.namesOk !== false && !settingsNote.textContent;
    startBtn.disabled = !ready;
    dockText.textContent = !s.source ? "Add images to download first."
      : !s.info.inspection.ok ? "Choose the column with the image links."
      : s.namesOk === false ? "Fix the filename template."
      : settingsNote.textContent ? "Check the settings."
      : `Ready: ${s.info.inspection.more ? "every row" : plural(s.info.inspection.rows.length, "row")} → ${s.names || "{hash}{ext}"}`;
  }

  async function start() {
    startBtn.disabled = true;
    const settings = { ...s.settings };
    if (!settings.adaptive) settings.max_concurrency = Math.max(settings.max_concurrency, settings.concurrency);
    try {
      const r = await api("/api/runs", { method: "POST", json: { source: s.source, names: s.names, settings } });
      say("Download started");
      location.hash = `#/run/${r.run}`;
    } catch (e) {
      toast(e.message);
      updateDock();
    }
  }

  const view = h("div", { class: "stack" },
    h("div", null, h("h1", { text: "New download" }),
      h("p", { class: "muted", text: "Three steps: the links, how files are named, and how fast to go. The defaults are fine for almost everyone." })),
    h("section", { class: "card", "aria-labelledby": "s1" },
      h("div", { class: "card-head" }, h("span", { class: "step", text: "1" }), h("h2", { id: "s1", text: "Images to download" }),
        h("span", { class: "grow" }), h("div", { class: "seg", role: "group", "aria-label": "Source" }, segBtns)),
      pastePanel, uploadPanel, status, resultBox),
    h("section", { class: "card", "aria-labelledby": "s2" },
      h("div", { class: "card-head" }, h("span", { class: "step", text: "2" }), h("h2", { id: "s2", text: "File names" })),
      h("label", { class: "field" }, "Filename template", tpl,
        h("span", { class: "hint", id: "tpl-hint" }, "Empty: ", h("code", { text: "{hash}{ext}" }),
          ", a stable name made from the link. A ", h("code", { text: "/" }), " makes folders, e.g. ",
          h("code", { text: "{category}/{name}{ext}" }), ".")),
      keyChips, namesBox),
    h("section", { class: "card", "aria-labelledby": "s3" },
      h("div", { class: "card-head" }, h("span", { class: "step", text: "3" }), h("h2", { id: "s3", text: "Speed and retries" })),
      h("div", { class: "settings" },
        sw("adaptive", "Adaptive concurrency", "Finds the fastest pace the server tolerates."),
        sw("hedge", "Hedging", "Races a second request against a straggler."),
        num("concurrency", "Starting concurrency", "Requests at once to begin with."),
        num("max_concurrency", "Maximum concurrency", "The most it will ever try."),
        num("max_retries", "Retries per image", "After the first attempt.")),
      h("details", { class: "adv" }, h("summary", { text: "Advanced" }),
        h("div", { class: "settings" },
          num("timeout", "Timeout (s)", "Connecting, or a read that stalls.", 0.5),
          num("max_request_time", "Longest request (s)", "Gives up on one image after this.", 1),
          num("max_retry_after", "Longest Retry-After wait (s)", "Most it waits when a server asks.", 1),
          sw("verify_existing", "Check existing files", "Re-check files already on disk are images."),
          sw("warm_start", "Warm start", "Start from the pace that worked last time."),
          sw("fsync", "Flush to disk", "Slower; safe against power loss."))),
      h("div", { class: "note" }, icon("shield"), h("div", { text: "Image checks and HTTPS certificate checks always stay on." })),
      settingsNote),
    h("div", { class: "note" }, icon("info"), h("div", { text: "Machyon downloads these links from this computer, like the " +
      "command line does. A manifest can point at addresses on your own network, and redirects are followed, so use manifests you trust." })),
    h("div", { class: "dock" }, dockText, startBtn));
  syncSettings();
  refreshNames();
  return view;
}

// ------------------------------------------------------------------ a run

async function runPage(id) {
  const data = await api(`/api/runs/${id}`);
  const r = data.run;
  const job = (await api("/api/job")).job;
  const live = job && job.run_id === id && job.state === "running" ? job : null;
  setLive(job);

  const head = h("div", { class: "run-head" },
    h("div", { class: "grow" }, h("p", { class: "faint", text: `${when(r.created)} · ${r.id}` }), h("h1", { text: sourceName(r) })),
    h("div", { class: "toolbar" }, live ? h("span", { class: "tag run", text: live.kind === "retry" ? "Retrying" : "Downloading" }) : statusTag(r.status, r.report)));
  const view = h("div", { class: "stack" }, head);

  if (live) {
    view.append(liveCard(id, live));
  } else if (r.report) {
    view.append(reportCard(r.report));
  } else {
    view.append(stoppedCard(r));
  }
  view.append(folderCard(data));
  return view;
}

function liveCard(id, job) {
  const big = h("div", { class: "big-count" });
  const phase = h("p", { class: "muted" });
  const meter = h("div", { class: "meter", role: "progressbar", "aria-label": "Rows accounted for" });
  const legend = h("div", { class: "legend" });
  const stats = h("div", { class: "stats" });
  const spark = document.createElementNS(SVG, "svg");
  spark.setAttribute("class", "spark");
  spark.setAttribute("viewBox", "0 0 300 64");
  spark.setAttribute("preserveAspectRatio", "none");
  spark.setAttribute("aria-hidden", "true");
  const line = document.createElementNS(SVG, "polyline");
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", "var(--accent-strong)");
  line.setAttribute("stroke-width", "2");
  line.setAttribute("vector-effect", "non-scaling-stroke");
  spark.append(line);
  const stopBtn = h("button", { class: "btn danger", type: "button", onclick: stop }, icon("stop"), "Stop");
  const rates = [];
  let prev = null;
  let lastPhase = null;

  async function stop() {
    if (!confirm("Stop this download? Every file already on disk stays complete, and you can resume later.")) return;
    stopBtn.disabled = true;
    try { await api("/api/job/stop", { method: "POST", json: {} }); toast("Stopping…"); } catch (e) { toast(e.message); }
  }

  function draw(job) {
    const p = job.progress;
    if (!p) { phase.textContent = "Reading the manifest…"; big.replaceChildren("0"); meter.classList.add("unknown"); meter.replaceChildren(h("i")); return; }
    const done = p.downloaded + p.skipped + p.failed;
    const ph = job.stopping ? "Stopping: finishing the files in flight…" : p.phase === "retry" ? "Retrying the failures once more" : "Downloading";
    phase.textContent = ph;
    if (ph !== lastPhase) { say(ph); lastPhase = ph; }
    big.replaceChildren(int(done), p.total !== null ? h("small", { text: ` / ${int(p.total)} rows` }) : h("small", { text: " rows so far · total unknown" }));
    if (p.total) {
      const pct = (n) => `${(100 * n) / Math.max(p.total, 1)}%`;
      meter.classList.remove("unknown");
      meter.setAttribute("aria-valuemin", 0);
      meter.setAttribute("aria-valuemax", p.total);
      meter.setAttribute("aria-valuenow", done);
      const a = h("i"); a.style.width = pct(p.downloaded + p.skipped + p.failed);
      a.className = "fl";
      const b = h("i", { class: "sk" }); b.style.width = pct(p.downloaded + p.skipped);
      const c = h("i"); c.style.width = pct(p.downloaded);
      meter.replaceChildren(a, b, c);
    } else {
      meter.classList.add("unknown");
      meter.replaceChildren(h("i"));
    }
    legend.replaceChildren(
      h("span", null, h("i", { class: "lg-ok" }), `Downloaded ${int(p.downloaded)}`),
      h("span", null, h("i", { class: "lg-sk" }), `Already on disk ${int(p.skipped)}`),
      h("span", null, h("i", { class: "lg-fl" }), `Failed ${int(p.failed)}`));
    if (prev && p.elapsed > prev.elapsed) rates.push((p.downloaded - prev.downloaded) / (p.elapsed - prev.elapsed));
    prev = p;
    if (rates.length > 120) rates.shift();
    const max = Math.max(1, ...rates);
    line.setAttribute("points", rates.map((v, i) => `${(i / Math.max(1, rates.length - 1)) * 300},${62 - (v / max) * 58}`).join(" "));
    const speed = p.elapsed > 0 ? p.downloaded / p.elapsed : 0;
    stats.replaceChildren(
      stat("Downloaded", int(p.downloaded), "ok"), stat("Already on disk", int(p.skipped)), stat("Failed", int(p.failed), p.failed ? "bad" : ""),
      stat("Data", bytes(p.bytes)), stat("Elapsed", duration(p.elapsed)), stat("Average speed", speed.toFixed(1), "", "img/s"),
      stat("Concurrency limit", int(p.concurrency)), stat("In flight", int(p.in_flight)),
      stat("Retries", int(p.retries)), stat("Throttled", int(p.throttled)), stat("Hedged", int(p.hedged)));
    stopBtn.disabled = !!job.stopping;
  }

  async function poll() {
    try {
      const j = (await api("/api/job")).job;
      setLive(j);
      if (!j || j.run_id !== id || j.state !== "running") { route(); return; }   // finished: show the report
      draw(j);
    } catch (e) { if (e.status === 401) return; }
    pollTimer = setTimeout(poll, document.hidden ? 2000 : 500);
  }
  draw(job);
  pollTimer = setTimeout(poll, 500);

  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { class: "grow", text: "Progress" }), stopBtn),
    phase, big, meter, legend, h("div", null, h("p", { class: "faint", text: "Images per second, as it goes" }), spark), stats,
    h("p", { class: "faint", text: "You can close this tab: the download keeps going until it finishes or you stop Machyon UI." }));
}

function stat(k, v, cls, unit) {
  return h("div", { class: `stat ${cls || ""}` }, h("div", { class: "k", text: k }), h("div", { class: "v" }, v, unit && h("small", { text: ` ${unit}` })));
}

function reportCard(rep) {
  const mbs = rep.duration ? rep.bytes / 1048576 / rep.duration : 0;
  const rate = rep.duration ? rep.downloaded / rep.duration : 0;
  const reasons = Object.entries(rep.failure_reasons || {}).sort((a, b) => b[1] - a[1]);
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { class: "grow", text: "Report" }),
      h("span", { class: "faint", text: `${duration(rep.duration)} · ${rate.toFixed(1)} img/s · ${mbs.toFixed(2)} MB/s` })),
    h("div", { class: "big-count" }, int(rep.downloaded + rep.skipped), h("small", { text: ` of ${int(rep.total)} rows are on disk` })),
    h("div", { class: "stats" },
      stat("Rows", int(rep.total)), stat("Downloaded", int(rep.downloaded), "ok"), stat("Already on disk", int(rep.skipped)),
      stat("Failed", int(rep.failed), rep.failed ? "bad" : ""), stat("Retries", int(rep.retries)), stat("Throttled", int(rep.throttled)),
      stat("Hedged", int(rep.hedged)), stat("Duration", duration(rep.duration)), stat("Throughput", rate.toFixed(1), "", "img/s"),
      stat("Data", bytes(rep.bytes))),
    reasons.length ? h("div", { class: "stack" }, h("h3", { text: "Why rows failed" }),
      h("ul", { class: "reasons" }, reasons.map(([reason, n]) => {
        const ex = (rep.failure_examples || {})[reason] || {};
        return h("li", null, h("div", { class: "rh" }, h("code", { text: reason }), h("span", { class: "tag bad", text: plural(n, "row") })),
          ex.hint && h("p", { text: ex.hint }), ex.error && h("p", { class: "mono", text: ex.error }));
      })))
      : h("div", { class: "note" }, icon("shield"), h("div", { text: "Nothing failed: every row is a real image on disk." })));
}

function stoppedCard(r) {
  const p = r.progress;
  const msg = r.status === "error" ? r.error || "The download stopped with an error."
    : r.status === "cancelled" ? "You stopped this download. Every file on disk is complete."
    : "This download didn't finish (Machyon UI was stopped while it ran). Every file on disk is complete.";
  return h("section", { class: "card" }, h("h2", { text: r.status === "error" ? "The download stopped" : "Not finished" }),
    h("div", { class: `note ${r.status === "error" ? "bad" : "warn"}` }, icon("warn"), h("div", { text: msg })),
    p && h("div", { class: "stats" }, stat("Downloaded", int(p.downloaded), "ok"), stat("Already on disk", int(p.skipped)),
      stat("Failed", int(p.failed), p.failed ? "bad" : ""), stat("Rows", p.total === null ? "unknown" : int(p.total)), stat("Elapsed", duration(p.elapsed))));
}

function folderCard(data) {
  return h("section", { class: "card" }, h("h2", { text: "Files" }),
    h("p", { class: "where" }, "Images are in", h("span", { class: "path", text: data.images }),
      h("button", { class: "btn small ghost", type: "button", onclick: () => copy(data.images, "Folder path") }, icon("copy"), "Copy path")),
    h("p", { class: "faint" }, "The run's folder also keeps the original manifest, ", h("code", { text: "run.json" }), " and ",
      h("code", { text: "report.json" }), "; failures are listed in ", h("code", { text: "images/failed_downloads.jsonl" }), "."));
}

// ------------------------------------------------------------------ start

const themeBtn = document.getElementById("theme");
function syncThemeButton() {
  const dark = document.documentElement.dataset.theme === "dark";
  themeBtn.replaceChildren(icon(dark ? "sun" : "moon"));
  themeBtn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}
themeBtn.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("machyon-ui-theme", next); } catch { /* a display preference only */ }
  syncThemeButton();
});
syncThemeButton();
window.addEventListener("hashchange", route);
route();
