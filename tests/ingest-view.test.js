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
