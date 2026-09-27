import assert from "node:assert/strict";
import test from "node:test";
import { ingestPayload, previewFacts, previewSamples, validatePdfUpload } from "../src/ingest-view.js";

test("PDF uploads accept optional provenance and exclude stale ZIP options", () => {
  assert.deepEqual(ingestPayload({ mode: "upload", source: "", company: "Santee Cooper", zipMember: "stale.pdf" }), {
    mode: "upload", source: "", company: "Santee Cooper",
  });
  assert.throws(() => ingestPayload({ mode: "upload", source: "file:///plan.pdf" }), /public HTTP/);
  assert.doesNotThrow(() => validatePdfUpload({ name: "plan.PDF", size: 100 }));
  assert.throws(() => validatePdfUpload(null), /Choose/);
  assert.throws(() => validatePdfUpload({ name: "plan.zip", size: 100 }), /PDF/);
  assert.throws(() => validatePdfUpload({ name: "plan.pdf", size: 65 * 1024 * 1024 }), /64 MB/);
  assert.throws(() => validatePdfUpload({ name: "plan.pdf", size: 0 }), /nonempty/);
});

test("ingest form accepts a public PDF link with explicit document hints", () => {
  assert.deepEqual(ingestPayload({ mode: "url", source: " https://example.org/plan.pdf ", company: " GPC ", date: "2026-09-01", edition: "2027-2031", title: " Plan ", zipMember: "" }), {
    mode: "url", source: "https://example.org/plan.pdf", company: "GPC", date: "2026-09-01", edition: "2027-2031", title: "Plan",
  });
  assert.throws(() => ingestPayload({ mode: "url", source: "file:///tmp/plan.pdf" }), /public HTTP or HTTPS/);
  assert.throws(() => ingestPayload({ mode: "url", source: "plan.pdf" }), /complete public URL/);
});

test("AI discovery uses a utility name and never carries stale company selection", () => {
  assert.deepEqual(ingestPayload({ mode: "find", source: " Santee Cooper ", company: "GPC", zipMember: "plan.pdf" }), {
    mode: "find", source: "Santee Cooper",
  });
  assert.throws(() => ingestPayload({ mode: "find", source: "  " }), /utility name/);
});

test("preview facts omit missing values and preserve zero counts", () => {
  assert.deepEqual(previewFacts({ title: "Plan", records: 0, dated: 0, withId: null }), [["Filing", "Plan"], ["Project records", 0], ["With target dates", 0]]);
});

test("preview sample rows distinguish a dated record from a published date", () => {
  assert.deepEqual(previewSamples({ samples: [{ projectId: "A", name: "Station upgrade", hasInServiceDate: true }, { projectId: null, name: "New tie", inService: "2028-01-01" }] }), [
    { id: "A", name: "Station upgrade", date: "Date present" }, { id: "—", name: "New tie", date: "2028-01-01" },
  ]);
});

test("refresh restores a preview and polling retries interruptions without resubmitting", async () => {
  const { createIngestView } = await import("../src/ingest-view.js");
  const savedGlobals = Object.fromEntries(["document", "sessionStorage", "fetch", "setTimeout", "clearTimeout"].map(k => [k, globalThis[k]]));
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, { value: "", files: [], dataset: {}, handlers: {}, hidden: false,
      addEventListener(name, fn) { this.handlers[name] = fn; }, replaceChildren() {},
    });
    return nodes.get(id);
  };
  const modes = ["url", "find", "upload"].map(value => ({ value, name: "ingest-mode", checked: value === "url" }));
  const form = node("ingest-form");
  form.querySelectorAll = () => modes;
  form.querySelector = selector => selector.includes(":checked") ? modes.find(m => m.checked)
    : selector.includes('[value="') ? modes.find(m => selector.includes(`[value="${m.value}"]`)) : node("options");
  // Native radios uncheck their siblings when checked.
  let chosen = "url";
  modes.forEach(m => Object.defineProperty(m, "checked", { get: () => chosen === m.value, set: on => { if (on) chosen = m.value; } }));
  const id = "a".repeat(32), calls = [], timers = [];
  const store = new Map([["gridlock.ingest-session", JSON.stringify({ id, payload: {mode:"upload",source:"https://example.org/file.pdf",company:"Santee Cooper"} })]]);
  try {
    globalThis.document = { getElementById: node };
    globalThis.sessionStorage = { getItem: k => store.get(k), setItem: (k,v) => store.set(k,v), removeItem: k => store.delete(k) };
    globalThis.setTimeout = (fn, delay) => { timers.push({fn,delay}); return timers.length; };
    globalThis.clearTimeout = () => {};
    let polls = 0;
    globalThis.fetch = async (url, options) => {
      calls.push({url, options});
      if (url.endsWith("/status")) return {ok:true,json:async()=>({available:true,aiAvailable:true})};
      if (++polls === 1) throw new Error("temporary disconnect");
      return {ok:false,status:404,json:async()=>({error:"No such ingest job"})};
    };
    const view = createIngestView({onBack:()=>{}});
    assert.equal(node("ingest-company").value, "Santee Cooper");
    assert.equal(chosen, "upload");
    await view.open();
    await new Promise(resolve => setImmediate(resolve));
    assert.match(node("ingest-progress").textContent, /Retrying progress/);
    assert.equal(node("ingest-preview").disabled, true);
    assert.equal(timers.length, 1);
    await timers[0].fn();
    assert.match(node("ingest-progress").textContent, /server restarted/);
    assert.equal(store.has("gridlock.ingest-session"), false);
    assert.equal(node("ingest-preview").disabled, false);
    assert.ok(calls.some(c => c.url.endsWith(id)));
    assert.ok(calls.every(c => !c.options?.method), "recovery must never re-submit an import");
  } finally { Object.assign(globalThis, savedGlobals); }
});
