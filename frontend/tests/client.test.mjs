import assert from "node:assert/strict";
import test from "node:test";
import { LibraryClient, parseDeck, parseManifest, validateManifestForDeck } from "../src/client.ts";

const deckId = "11111111-1111-4111-8111-111111111111";
const generationId = "22222222-2222-4222-8222-222222222222";
const assetId = "33333333-3333-4333-8333-333333333333";
const checksum = "a".repeat(64);

function validDeck() {
  return {
    deck_id: deckId,
    filename: "team.pptx",
    source_sha256: checksum,
    byte_length: 42,
    source_revision: 1,
    state: "extracted",
    operation_id: generationId,
    generation_id: generationId,
    completed_slides: 1,
    total_slides: 1,
    stage: "complete",
    error: null,
    warnings: [],
    status_url: `/v1/decks/${deckId}`,
    manifest_url: `/v1/decks/${deckId}/manifest`,
    original_url: `/v1/decks/${deckId}/original`,
  };
}

function validManifest() {
  return {
    schema_version: 1,
    deck_id: deckId,
    source_revision: 1,
    generation_id: generationId,
    source_sha256: checksum,
    source_filename: "team.pptx",
    canonical_original_reference: `/v1/decks/${deckId}/original`,
    content: {
      schema_version: 1,
      slides: [{
        source: { number: 1, source_slide_id: 4, hidden: false, width: 1000, height: 500 },
        preview: { asset_local_id: "slide-1", status: "rendered", method: "slide_render", warnings: [] },
        occurrences: [],
      }],
    },
    assets: [{
      asset_id: assetId,
      local_id: "slide-1",
      media_type: "image/png",
      role: "slide_preview",
      byte_length: 9,
      sha256: checksum,
      pixel_size: [100, 50],
      download_url: `/v1/decks/${deckId}/generations/${generationId}/assets/${assetId}`,
    }],
    warnings: [],
  };
}

test("parses an extracted deck and its source identity", () => {
  assert.equal(parseDeck(validDeck()).state, "extracted");
});

test("rejects a deck with a malformed checksum", () => {
  const deck = validDeck();
  deck.source_sha256 = "bad";
  assert.throws(() => parseDeck(deck), /deck identity/);
});

test("accepts a generation manifest whose preview references its asset", () => {
  assert.equal(parseManifest(validManifest()).content.slides[0].source.number, 1);
});

test("reads the authenticated speaker notes upload policy", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    assert.match(String(url), /\/v1\/policy$/);
    assert.equal(new Headers(init.headers).get("Authorization"), `Bearer ${"x".repeat(32)}`);
    return new Response(JSON.stringify({ include_notes: false }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  try {
    assert.deepEqual(await new LibraryClient("x".repeat(32)).uploadPolicy(), { include_notes: false });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("accepts a retained generation while its deck is being reprocessed", () => {
  const deck = { ...validDeck(), state: "processing", source_revision: 2 };
  assert.equal(validateManifestForDeck(deck, parseManifest(validManifest())).source_revision, 1);
});

test("rejects a manifest that redirects a binary download outside its generation", () => {
  const manifest = validManifest();
  manifest.assets[0].download_url = "https://example.invalid/image.png";
  assert.throws(() => parseManifest(manifest), /asset URL/);
});

test("reuses an upload key after a lost response for the same file", async () => {
  const originalFetch = globalThis.fetch;
  const keys = [];
  let requests = 0;
  globalThis.fetch = async (_url, init) => {
    keys.push(new Headers(init.headers).get("Idempotency-Key"));
    requests += 1;
    if (requests === 1) throw new TypeError("connection lost after upload");
    return new Response(JSON.stringify({
      deck_id: deckId,
      operation_id: generationId,
      status_url: `/v1/decks/${deckId}`,
      operation_url: `/v1/operations/${generationId}`,
    }), { status: 202, headers: { "content-type": "application/json" } });
  };
  try {
    const client = new LibraryClient("x".repeat(32));
    const file = new File(["deck"], "team.pptx", { lastModified: 1 });
    await assert.rejects(client.upload(file), /connection lost/);
    await client.upload(file);
    assert.equal(keys[0], keys[1]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("reuses an upload key after an uncertain server error", async () => {
  const originalFetch = globalThis.fetch;
  const keys = [];
  let requests = 0;
  globalThis.fetch = async (_url, init) => {
    keys.push(new Headers(init.headers).get("Idempotency-Key"));
    requests += 1;
    if (requests === 1) return new Response("upstream error", { status: 502 });
    return new Response(JSON.stringify({
      deck_id: deckId,
      operation_id: generationId,
      status_url: `/v1/decks/${deckId}`,
      operation_url: `/v1/operations/${generationId}`,
    }), { status: 202, headers: { "content-type": "application/json" } });
  };
  try {
    const client = new LibraryClient("x".repeat(32));
    const file = new File(["deck"], "team.pptx", { lastModified: 1 });
    await assert.rejects(client.upload(file), /Library request failed \(502\)/);
    await client.upload(file);
    assert.equal(keys[0], keys[1]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
