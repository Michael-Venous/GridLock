import assert from "node:assert/strict";
import test from "node:test";
import { ingestPayload, previewFacts, previewSamples } from "../src/ingest-view.js";

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
