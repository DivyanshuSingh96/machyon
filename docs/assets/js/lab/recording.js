/* Machyon Lab recordings in the browser: load and validate a recording made by `python -m lab.record`.

   The same rules as lab/schema.py, so the docs never draw a recording the Python side would reject; the tests in
   lab/tests/test_schema.py run both validators over the same good and bad files. Analysis is not recomputed here:
   the browser draws `analysis` exactly as lab/analysis.py saved it.

   Usage:  const rec = await MachyonLab.loadRecording(url);   // throws MachyonLab.RecordingError with .problems
           const problems = MachyonLab.validateRecording(doc); // [] when valid
           const events = MachyonLab.eventsOf(rec.runs[0]);    // one object per server event
*/
(function (root) {
  "use strict";

  const SCHEMA = "machyon-lab-recording";
  const SCHEMA_VERSIONS = [1];
  const OUTCOMES = new Set(["complete", "truncated", "client_closed", "cancelled", "error"]);
  // Events are stored as columns (see lab/schema.py): field names once, then one row per event in server order.
  // seq is the row number + 1, event kinds are "s"/"e", request ids are integers.
  const EVENT_FIELDS = ["event", "id", "t_ms", "image", "inflight", "attempt", "same_image_open", "status",
    "outcome", "bytes", "duration_ms"];
  const KIND_NAMES = { s: "start", e: "end" };
  const START_FIELDS = ["seq", "event", "id", "t_ms", "image", "inflight", "attempt", "same_image_open"];
  const END_FIELDS = ["seq", "event", "id", "t_ms", "image", "inflight", "status", "outcome", "bytes", "duration_ms"];

  function decodeEvents(block) {
    return block.rows.map((row, i) => {
      const full = { seq: i + 1 };
      EVENT_FIELDS.forEach((f, k) => { full[f] = row[k]; });
      if (full.event in KIND_NAMES) full.event = KIND_NAMES[full.event];
      const keep = full.event === "start" ? START_FIELDS : END_FIELDS;
      const e = {};
      for (const f of keep) e[f] = full[f];
      return e;
    });
  }

  const decoded = new WeakMap();
  /** One object per event for a run, decoded once and cached. */
  function eventsOf(run) {
    if (!decoded.has(run)) decoded.set(run, decodeEvents(run.fake_cdn.events));
    return decoded.get(run);
  }
  const REPORT_KEYS = ["total", "downloaded", "skipped", "failed", "bytes", "duration", "retries", "throttled",
    "hedged", "peak_concurrency", "final_concurrency", "latency_p50", "latency_p95"];
  const PROVENANCE_KEYS = ["machyon_version", "python_version", "platform", "recorded_at", "recorder"];
  const SLUG = /^[a-z0-9][a-z0-9-]*$/;
  const ISO_UTC = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$/;

  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const isInt = (v) => Number.isInteger(v);
  const fmt = (v) => (v === undefined ? "null" : JSON.stringify(v));   // as Python's json.dumps writes it

  function validateRecording(doc) {
    const errs = [];
    if (!isObj(doc)) return ["recording must be a JSON object"];
    if (doc.schema !== SCHEMA) errs.push(`schema must be '${SCHEMA}'`);
    if (!SCHEMA_VERSIONS.includes(doc.schema_version)) {
      errs.push(`unsupported schema_version ${fmt(doc.schema_version)}`);
      return errs;
    }
    if (typeof doc.experiment !== "string" || !SLUG.test(doc.experiment.replace(/_/g, "-"))) {
      errs.push("experiment must be a lowercase identifier");
    }
    const prov = doc.provenance;
    if (!isObj(prov)) {
      errs.push("provenance is missing");
    } else {
      for (const k of PROVENANCE_KEYS) {
        if (typeof prov[k] !== "string" || !prov[k]) errs.push(`provenance.${k} must be a non-empty string`);
      }
      if (typeof prov.recorded_at === "string" && !ISO_UTC.test(prov.recorded_at)) {
        errs.push("provenance.recorded_at must be an ISO 8601 UTC time ending in Z");
      }
      if ("source_commit" in prov && prov.source_commit !== null && typeof prov.source_commit !== "string") {
        errs.push("provenance.source_commit must be a string or null");
      }
    }
    const inp = doc.input;
    if (!isObj(inp) || !isInt(inp.images) || inp.images < 1) errs.push("input.images must be a positive integer");
    const runs = doc.runs;
    if (!Array.isArray(runs) || runs.length === 0) {
      errs.push("runs must be a non-empty list");
      return errs;
    }
    const names = new Set();
    runs.forEach((run, n) => {
      const where = `runs[${n}]`;
      if (!isObj(run)) { errs.push(`${where} must be an object`); return; }
      if (typeof run.name !== "string" || !SLUG.test(run.name)) {
        errs.push(`${where}.name must be a lowercase slug`);
      } else if (names.has(run.name)) {
        errs.push(`${where}.name '${run.name}' is not unique`);
      }
      names.add(run.name);
      if (typeof run.label !== "string" || !run.label) errs.push(`${where}.label must be a non-empty string`);
      errs.push(...checkMachyon(run.machyon, `${where}.machyon`));
      errs.push(...checkFakeCdn(run.fake_cdn, `${where}.fake_cdn`));
      errs.push(...checkFilesystem(run, `${where}.filesystem`));
      if (!isObj(run.analysis) || !isInt(run.analysis.analysis_version)) {
        errs.push(`${where}.analysis must be an object with an integer analysis_version`);
      }
    });
    return errs;
  }

  function checkMachyon(m, where) {
    if (!isObj(m) || m.source !== "machyon") return [`${where} must be an object with source 'machyon'`];
    const errs = [];
    if (!isObj(m.config)) errs.push(`${where}.config must be an object`);
    const rep = m.report;
    if (!isObj(rep)) return errs.concat([`${where}.report must be an object`]);
    for (const k of REPORT_KEYS) if (!isNum(rep[k])) errs.push(`${where}.report.${k} must be a number`);
    if (!errs.length && rep.downloaded + rep.skipped + rep.failed !== rep.total) {
      errs.push(`${where}.report: downloaded + skipped + failed must equal total`);
    }
    return errs;
  }

  function checkFakeCdn(f, where) {
    if (!isObj(f) || f.source !== "fake_cdn") return [`${where} must be an object with source 'fake_cdn'`];
    const errs = [];
    if (!Array.isArray(f.args) || !f.args.every((a) => typeof a === "string")) {
      errs.push(`${where}.args must be a list of strings`);
    }
    if (!isObj(f.stats)) errs.push(`${where}.stats must be an object`);
    if (f.dropped !== 0) errs.push(`${where}.dropped must be 0: a recording must contain every event`);
    const block = f.events;
    if (!isObj(block) || JSON.stringify(block.fields) !== JSON.stringify(EVENT_FIELDS)) {
      return errs.concat([`${where}.events must be {fields, rows} with fields ${JSON.stringify(EVENT_FIELDS)}`]);
    }
    if (!Array.isArray(block.rows) ||
        !block.rows.every((r) => Array.isArray(r) && r.length === EVENT_FIELDS.length)) {
      return errs.concat([`${where}.events.rows must be a list of rows with ${EVENT_FIELDS.length} values each`]);
    }
    const evs = decodeEvents(block);
    const started = new Set();
    const ended = new Set();
    let lastT = -1;
    for (let i = 0; i < evs.length; i++) {
      const e = evs[i];
      const w = `${where}.events.rows[${i}]`;
      if (!isObj(e)) { errs.push(`${w} must be an object`); break; }
      if (!isNum(e.t_ms) || e.t_ms < lastT) { errs.push(`${w}.t_ms must be a number that never decreases`); break; }
      lastT = e.t_ms;
      if (!isInt(e.inflight) || e.inflight < 0) { errs.push(`${w}.inflight must be a non-negative integer`); break; }
      if (!isInt(e.id)) { errs.push(`${w}.id must be an integer`); break; }
      if (e.event === "start") {
        if (started.has(e.id)) { errs.push(`${w}: request id ${e.id} started twice`); break; }
        started.add(e.id);
        if (!isInt(e.same_image_open) || e.same_image_open < 0) {
          errs.push(`${w}.same_image_open must be a non-negative integer`); break;
        }
      } else if (e.event === "end") {
        if (!started.has(e.id) || ended.has(e.id)) {
          errs.push(`${w}: end for request ${e.id} without a matching start`); break;
        }
        ended.add(e.id);
        if (!OUTCOMES.has(e.outcome)) {
          errs.push(`${w}.outcome must be one of ${fmt([...OUTCOMES].sort())}`); break;
        }
        if (!isNum(e.duration_ms) || e.duration_ms < 0) {
          errs.push(`${w}.duration_ms must be a non-negative number`); break;
        }
        if (e.status !== null && e.status !== undefined && !isInt(e.status)) {
          errs.push(`${w}.status must be an integer or null`); break;
        }
      } else {
        errs.push(`${w}.event must be 's' (start) or 'e' (end)`); break;
      }
    }
    return errs;
  }

  function checkFilesystem(run, where) {
    const fs = run.filesystem;
    if (!isObj(fs) || fs.source !== "filesystem") return [`${where} must be an object with source 'filesystem'`];
    const errs = ["files", "ledger_rows"].filter((k) => !isInt(fs[k]) || fs[k] < 0)
      .map((k) => `${where}.${k} must be a non-negative integer`);
    const rep = (run.machyon && run.machyon.report) || {};
    if (!errs.length && isNum(rep.failed) && fs.ledger_rows !== rep.failed) {
      errs.push(`${where}.ledger_rows (${fs.ledger_rows}) must equal report.failed (${rep.failed})`);
    }
    if (!errs.length && isNum(rep.downloaded) && isNum(rep.skipped) && fs.files !== rep.downloaded + rep.skipped) {
      errs.push(`${where}.files (${fs.files}) must equal report.downloaded + report.skipped`);
    }
    return errs;
  }

  class RecordingError extends Error {
    constructor(message, problems) {
      super(message);
      this.name = "RecordingError";
      this.problems = problems || [];
    }
  }

  async function loadRecording(url, fetchImpl) {
    const res = await (fetchImpl || root.fetch)(url);
    if (!res.ok) throw new RecordingError(`couldn't load ${url}: HTTP ${res.status}`);
    let doc;
    try {
      doc = await res.json();
    } catch (e) {
      throw new RecordingError(`${url} is not valid JSON`);
    }
    const problems = validateRecording(doc);
    if (problems.length) throw new RecordingError(`${url} is not a valid Machyon Lab recording`, problems);
    return doc;
  }

  const api = { SCHEMA, SCHEMA_VERSIONS, EVENT_FIELDS, validateRecording, loadRecording, decodeEvents, eventsOf,
    RecordingError };
  root.MachyonLab = Object.assign(root.MachyonLab || {}, api);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
