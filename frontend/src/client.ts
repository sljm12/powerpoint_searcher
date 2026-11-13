export type DeckState = "queued" | "processing" | "extracted" | "failed" | "deleting" | "deleted";

export type Deck = {
  deck_id: string;
  filename: string;
  source_sha256: string;
  byte_length: number;
  source_revision: number;
  state: DeckState;
  operation_id: string;
  generation_id: string | null;
  completed_slides: number;
  total_slides: number;
  stage: string;
  error: { code: string; message: string; retryable: boolean } | null;
  warnings: Warning[];
  status_url: string;
  manifest_url: string | null;
  original_url: string;
  search?: SearchStatus | null;
};

export type SearchStatus = {
  state: "not_configured" | "queued" | "processing" | "ready" | "failed" | "deleting";
  index_revision: number;
  generation_id: string | null;
  profile_id: string | null;
  job_id: string | null;
  completed_segments: number;
  total_segments: number;
  eligible_slides: number;
  searchable_slides: number;
  blank_slides: number;
  hidden_slides: number;
  warnings: { code: string; message: string }[];
  error: { code: string; message: string; retryable: boolean } | null;
};

export type Warning = { code: string; message: string; affected_occurrence_ids: string[] };
export type SearchPolicy = { state: "not_configured" | "ready" | "unavailable"; profile_id: string | null; model: string | null; location: string | null; disclosure: string };
export type UploadPolicy = { include_notes: boolean; search: SearchPolicy };
export type SearchMatch = { slide_number: number; generation_id: string; index_revision: number; score: number; score_kind: "rrf" | "lexical"; retrieval_paths: ("lexical" | "semantic")[]; matched_channel: "native" | "notes"; snippet: string; preview_url: string; warning_metadata: { code: string; message: string }[] };
export type SearchGroup = { deck_id: string; filename: string; matches: SearchMatch[]; has_more_matches: boolean };
export type SearchResponse = { query_id: string; profile_id: string; mode: "hybrid" | "lexical"; degraded: boolean; warnings: { code: string; message: string }[]; coverage: Record<string, number>; decks: SearchGroup[]; next_offset: number | null };
export type Asset = {
  asset_id: string;
  local_id: string;
  media_type: string;
  role: "slide_preview" | "original_picture" | "isolated_render" | "group_render" | "contextual_crop";
  byte_length: number;
  sha256: string;
  pixel_size: [number, number] | null;
  download_url: string;
};
export type Rendition = {
  asset_local_id: string | null;
  status: "rendered" | "not_visible";
  method: "slide_render" | "libreoffice_shape" | "libreoffice_group" | "slide_crop" | "none";
  warnings: Warning[];
};
export type Occurrence = {
  source: { id: string; slide: number; source_name: string; kind: string; text: string; alt_text: string };
  original_asset_local_id: string | null;
  original_available: boolean;
  rendition: Rendition;
};
export type Slide = {
  source: { number: number; hidden: boolean };
  notes: { state: "present" | "empty" | "excluded"; paragraphs: string[]; truncated: boolean } | null;
  preview: Rendition;
  occurrences: Occurrence[];
};
export type Manifest = {
  deck_id: string;
  source_revision: number;
  generation_id: string;
  source_sha256: string;
  source_filename: string;
  content: { slides: Slide[] };
  assets: Asset[];
  warnings: Warning[];
};
export type Operation = { operation_id: string; state: string; phase: string; error: Deck["error"] };
export type Accepted = { deck_id: string; operation_id: string; status_url: string; operation_url: string };
export type DeletionStatus = { deck_id: string; state: "deleting" | "deleted"; operation_id: string; updated_at: string };

class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const checksumPattern = /^[0-9a-f]{64}$/;

function object(value: unknown, name: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error(`Invalid ${name} response.`);
  const result: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) result[key] = item;
  return result;
}

function text(value: unknown, name: string): string {
  if (typeof value !== "string" || value.length === 0) throw new Error(`Invalid ${name} response.`);
  return value;
}

function route(value: unknown, name: string): string {
  const path = text(value, name);
  if (!path.startsWith("/v1/") || path.startsWith("//") || path.includes("\\")) throw new Error(`Invalid ${name} response.`);
  return path;
}

function integer(value: unknown, name: string, minimum = 0): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum) throw new Error(`Invalid ${name} response.`);
  return value;
}

function parseWarning(value: unknown): Warning {
  const row = object(value, "warning");
  if (!Array.isArray(row.affected_occurrence_ids) || !row.affected_occurrence_ids.every((item) => typeof item === "string")) {
    throw new Error("Invalid warning response.");
  }
  return { code: text(row.code, "warning code"), message: text(row.message, "warning message"), affected_occurrence_ids: row.affected_occurrence_ids };
}

function parseError(value: unknown): Deck["error"] {
  if (value === null) return null;
  const row = object(value, "error");
  if (typeof row.retryable !== "boolean") throw new Error("Invalid error response.");
  return { code: text(row.code, "error code"), message: text(row.message, "error message"), retryable: row.retryable };
}

function parseAccepted(value: unknown): Accepted {
  const row = object(value, "accepted operation");
  if (typeof row.deck_id !== "string" || !uuidPattern.test(row.deck_id)) throw new Error("Invalid deck ID response.");
  const operationId = text(row.operation_id, "operation ID");
  if (!uuidPattern.test(operationId)) throw new Error("Invalid operation ID response.");
  return { deck_id: row.deck_id, operation_id: operationId, status_url: route(row.status_url, "status URL"), operation_url: route(row.operation_url, "operation URL") };
}

function parseUploadPolicy(value: unknown): UploadPolicy {
  const row = object(value, "upload policy");
  if (typeof row.include_notes !== "boolean") throw new Error("Invalid speaker notes policy response.");
  const search = row.search === undefined ? { state: "not_configured" as const, profile_id: null,
    model: null, location: null, disclosure: "Text search is not configured." } : parseSearchPolicy(row.search);
  return { include_notes: row.include_notes, search };
}

function parseDeletion(value: unknown): DeletionStatus {
  const row = object(value, "deletion");
  if (typeof row.deck_id !== "string" || !uuidPattern.test(row.deck_id) ||
      (row.state !== "deleting" && row.state !== "deleted")) throw new Error("Invalid deletion response.");
  const operationId = text(row.operation_id, "deletion operation ID");
  if (!uuidPattern.test(operationId)) throw new Error("Invalid deletion operation ID.");
  return { deck_id: row.deck_id, state: row.state, operation_id: operationId, updated_at: text(row.updated_at, "deletion timestamp") };
}

function parseDeckState(value: unknown): DeckState {
  switch (value) {
    case "queued":
    case "processing":
    case "extracted":
    case "failed":
    case "deleting":
    case "deleted": return value;
    default: throw new Error("Invalid deck state response.");
  }
}

function parseRenditionStatus(value: unknown): Rendition["status"] {
  if (value === "rendered" || value === "not_visible") return value;
  throw new Error("Invalid rendition status response.");
}

function parseRenditionMethod(value: unknown): Rendition["method"] {
  switch (value) {
    case "slide_render":
    case "libreoffice_shape":
    case "libreoffice_group":
    case "slide_crop":
    case "none": return value;
    default: throw new Error("Invalid rendition method response.");
  }
}

function parseAssetRole(value: unknown): Asset["role"] {
  switch (value) {
    case "slide_preview":
    case "original_picture":
    case "isolated_render":
    case "group_render":
    case "contextual_crop": return value;
    default: throw new Error("Invalid asset role response.");
  }
}

export function parseDeck(value: unknown): Deck {
  const row = object(value, "deck");
  if (typeof row.deck_id !== "string" || !uuidPattern.test(row.deck_id) || typeof row.source_sha256 !== "string" || !checksumPattern.test(row.source_sha256)) {
    throw new Error("Invalid deck identity response.");
  }
  if (!Array.isArray(row.warnings)) throw new Error("Invalid deck warnings response.");
  const generation = row.generation_id;
  if (generation !== null && (typeof generation !== "string" || !uuidPattern.test(generation))) throw new Error("Invalid generation response.");
  const manifestUrl = row.manifest_url;
  if (manifestUrl !== null && typeof manifestUrl !== "string") throw new Error("Invalid manifest URL response.");
  const search = row.search === undefined || row.search === null ? null : parseSearchStatus(row.search);
  return {
    deck_id: row.deck_id,
    filename: text(row.filename, "filename"),
    source_sha256: row.source_sha256,
    byte_length: integer(row.byte_length, "byte length", 1),
    source_revision: integer(row.source_revision, "source revision", 1),
    state: parseDeckState(row.state),
    operation_id: text(row.operation_id, "operation ID"),
    generation_id: generation,
    completed_slides: integer(row.completed_slides, "completed slides"),
    total_slides: integer(row.total_slides, "total slides"),
    stage: text(row.stage, "stage"),
    error: parseError(row.error),
    warnings: row.warnings.map(parseWarning),
    status_url: route(row.status_url, "status URL"),
    manifest_url: manifestUrl === null ? null : route(manifestUrl, "manifest URL"),
    original_url: route(row.original_url, "original URL"),
    search,
  };
}

function parseSearchStatus(value: unknown): SearchStatus {
  const row = object(value, "search status");
  const states = ["not_configured", "queued", "processing", "ready", "failed", "deleting"];
  if (typeof row.state !== "string" || !states.includes(row.state)) throw new Error("Invalid search status response.");
  if (!Array.isArray(row.warnings)) throw new Error("Invalid search warning response.");
  const generation = row.generation_id;
  if (generation !== null && (typeof generation !== "string" || !uuidPattern.test(generation))) throw new Error("Invalid search generation response.");
  const job = row.job_id;
  if (job !== null && (typeof job !== "string" || !uuidPattern.test(job))) throw new Error("Invalid search job response.");
  const profile = row.profile_id;
  if (profile !== null && typeof profile !== "string") throw new Error("Invalid search profile response.");
  return { state: row.state as SearchStatus["state"], index_revision: integer(row.index_revision, "search index revision"),
    generation_id: generation as string | null, profile_id: profile as string | null, job_id: job as string | null,
    completed_segments: integer(row.completed_segments, "completed search segments"),
    total_segments: integer(row.total_segments, "total search segments"), warnings: row.warnings.map(parseSearchNotice),
    eligible_slides: integer(row.eligible_slides ?? 0, "eligible slides"),
    searchable_slides: integer(row.searchable_slides ?? 0, "searchable slides"),
    blank_slides: integer(row.blank_slides ?? 0, "blank slides"), hidden_slides: integer(row.hidden_slides ?? 0, "hidden slides"),
    error: parseError(row.error) };
}

function parseSearchNotice(value: unknown): { code: string; message: string } {
  const row = object(value, "search warning");
  return { code: text(row.code, "search warning code"), message: text(row.message, "search warning message") };
}

function parseSearchPolicy(value: unknown): SearchPolicy {
  const row = object(value, "search policy");
  if (row.state !== "not_configured" && row.state !== "ready" && row.state !== "unavailable") throw new Error("Invalid search policy response.");
  const nullableText = (item: unknown, name: string) => item === null ? null : text(item, name);
  return { state: row.state, profile_id: nullableText(row.profile_id, "search profile"),
    model: nullableText(row.model, "search model"), location: nullableText(row.location, "search location"),
    disclosure: text(row.disclosure, "search disclosure") };
}

function parseSearchResponse(value: unknown): SearchResponse {
  const row = object(value, "search response");
  if (typeof row.query_id !== "string" || !uuidPattern.test(row.query_id) || typeof row.profile_id !== "string" ||
      (row.mode !== "hybrid" && row.mode !== "lexical") || typeof row.degraded !== "boolean" ||
      !Array.isArray(row.warnings) || !Array.isArray(row.decks)) throw new Error("Invalid search response.");
  const coverage = object(row.coverage, "search coverage");
  const counts: Record<string, number> = {};
  for (const [name, count] of Object.entries(coverage)) counts[name] = integer(count, "coverage count");
  const decks = row.decks.map((value): SearchGroup => {
    const group = object(value, "search deck group");
    if (typeof group.deck_id !== "string" || !uuidPattern.test(group.deck_id) || typeof group.has_more_matches !== "boolean" || !Array.isArray(group.matches)) throw new Error("Invalid search deck group.");
    return { deck_id: group.deck_id, filename: text(group.filename, "search filename"), has_more_matches: group.has_more_matches,
      matches: group.matches.map((value): SearchMatch => {
        const match = object(value, "search match");
        if (typeof match.generation_id !== "string" || !uuidPattern.test(match.generation_id) ||
            (match.score_kind !== "rrf" && match.score_kind !== "lexical") ||
            (match.matched_channel !== "native" && match.matched_channel !== "notes") ||
            !Array.isArray(match.retrieval_paths) || !match.retrieval_paths.every((item) => item === "lexical" || item === "semantic") ||
            !Array.isArray(match.warning_metadata)) throw new Error("Invalid slide search match.");
        return { slide_number: integer(match.slide_number, "slide number", 1), generation_id: match.generation_id,
          index_revision: integer(match.index_revision, "index revision", 1), score: finite(match.score, "ranking score"),
          score_kind: match.score_kind, retrieval_paths: match.retrieval_paths as SearchMatch["retrieval_paths"],
          matched_channel: match.matched_channel, snippet: boundedText(match.snippet, "search snippet", 500),
          preview_url: route(match.preview_url, "search preview URL"), warning_metadata: match.warning_metadata.map(parseSearchNotice) };
      }) };
  });
  const next = row.next_offset;
  if (next !== null && (typeof next !== "number" || !Number.isInteger(next) || next < 0)) throw new Error("Invalid search continuation.");
  return { query_id: row.query_id, profile_id: row.profile_id, mode: row.mode, degraded: row.degraded,
    warnings: row.warnings.map(parseSearchNotice), coverage: counts, decks,
    next_offset: next as number | null };
}

function finite(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`Invalid ${name} response.`);
  return value;
}

function boundedText(value: unknown, name: string, max: number): string {
  if (typeof value !== "string" || value.length > max) throw new Error(`Invalid ${name} response.`);
  return value;
}

function parseRendition(value: unknown): Rendition {
  const row = object(value, "rendition");
  if (!Array.isArray(row.warnings)) throw new Error("Invalid rendition response.");
  const localId = row.asset_local_id;
  if (localId !== null && typeof localId !== "string") throw new Error("Invalid rendition asset response.");
  return { asset_local_id: localId, status: parseRenditionStatus(row.status), method: parseRenditionMethod(row.method), warnings: row.warnings.map(parseWarning) };
}

function parseAsset(value: unknown): Asset {
  const row = object(value, "asset");
  if (typeof row.asset_id !== "string" || !uuidPattern.test(row.asset_id) || typeof row.sha256 !== "string" || !checksumPattern.test(row.sha256) ||
      typeof row.role !== "string") throw new Error("Invalid asset response.");
  const mediaTypes = ["image/png", "image/jpeg", "image/gif", "image/tiff", "image/bmp", "image/svg+xml", "application/octet-stream"];
  if (typeof row.media_type !== "string" || !mediaTypes.includes(row.media_type)) throw new Error("Invalid asset media type response.");
  let size: [number, number] | null = null;
  if (row.pixel_size !== null) {
    if (!Array.isArray(row.pixel_size) || row.pixel_size.length !== 2) throw new Error("Invalid image dimensions response.");
    size = [integer(row.pixel_size[0], "image width", 1), integer(row.pixel_size[1], "image height", 1)];
  }
  return {
    asset_id: row.asset_id,
    local_id: text(row.local_id, "asset local ID"),
    media_type: text(row.media_type, "asset media type"),
    role: parseAssetRole(row.role),
    byte_length: integer(row.byte_length, "asset byte length", 1),
    sha256: row.sha256,
    pixel_size: size,
    download_url: route(row.download_url, "asset URL"),
  };
}

function parseOccurrence(value: unknown): Occurrence {
  const row = object(value, "occurrence");
  const source = object(row.source, "occurrence source");
  if (typeof row.original_available !== "boolean") throw new Error("Invalid original image response.");
  if (typeof source.source_name !== "string" || typeof source.text !== "string" || typeof source.alt_text !== "string") throw new Error("Invalid occurrence text response.");
  const originalId = row.original_asset_local_id;
  if (originalId !== null && typeof originalId !== "string") throw new Error("Invalid original image asset response.");
  return {
    source: {
      id: text(source.id, "occurrence ID"),
      slide: integer(source.slide, "slide number", 1),
      source_name: source.source_name,
      kind: text(source.kind, "shape kind"),
      text: source.text,
      alt_text: source.alt_text,
    },
    original_asset_local_id: originalId,
    original_available: row.original_available,
    rendition: parseRendition(row.rendition),
  };
}

export function parseManifest(value: unknown): Manifest {
  const row = object(value, "manifest");
  const content = object(row.content, "manifest content");
  if (row.schema_version !== 1 || typeof row.deck_id !== "string" || !uuidPattern.test(row.deck_id) ||
      typeof row.generation_id !== "string" || !uuidPattern.test(row.generation_id) || typeof row.source_sha256 !== "string" ||
      !checksumPattern.test(row.source_sha256) || !Array.isArray(content.slides) || !Array.isArray(row.assets) || !Array.isArray(row.warnings)) {
    throw new Error("Invalid extraction manifest response.");
  }
  const assets = row.assets.map(parseAsset);
  const assetByLocalId = new Map(assets.map((asset) => [asset.local_id, asset]));
  if (assetByLocalId.size !== assets.length) throw new Error("Duplicate asset identity in manifest.");
  for (const asset of assets) {
    if (asset.download_url !== `/v1/decks/${row.deck_id}/generations/${row.generation_id}/assets/${asset.asset_id}`) {
      throw new Error("Invalid library asset URL in manifest.");
    }
  }
  const slides = content.slides.map((value, index): Slide => {
    const slide = object(value, "slide");
    const source = object(slide.source, "slide source");
    if (typeof source.hidden !== "boolean") throw new Error("Invalid slide visibility response.");
    let notes: Slide["notes"] = null;
    if (slide.notes != null) {
      const row = object(slide.notes, "slide notes");
      const state = row.state;
      const paragraphs = row.paragraphs;
      const truncated = row.truncated;
      if ((state !== "present" && state !== "empty" && state !== "excluded") ||
          !Array.isArray(paragraphs) || !paragraphs.every((paragraph) => typeof paragraph === "string") ||
          typeof truncated !== "boolean") throw new Error("Invalid slide notes response.");
      notes = { state, paragraphs, truncated };
    }
    const preview = parseRendition(slide.preview);
    const previewAsset = preview.asset_local_id === null ? undefined : assetByLocalId.get(preview.asset_local_id);
    if (preview.status !== "rendered" || previewAsset?.role !== "slide_preview" || integer(source.number, "slide number", 1) !== index + 1) throw new Error("Slide preview is unavailable.");
    const occurrences = slide.occurrences;
    if (!Array.isArray(occurrences)) throw new Error("Invalid slide occurrences response.");
    return { source: { number: integer(source.number, "slide number", 1), hidden: source.hidden }, notes,
      preview, occurrences: occurrences.map(parseOccurrence) };
  });
  for (const slide of slides) for (const item of slide.occurrences) {
    if (item.source.slide !== slide.source.number || item.original_available !== (item.original_asset_local_id !== null)) throw new Error("Occurrence identity is inconsistent.");
    if (item.original_asset_local_id !== null && assetByLocalId.get(item.original_asset_local_id)?.role !== "original_picture") throw new Error("Original picture asset is unavailable.");
    if (item.rendition.asset_local_id !== null) {
      const role = item.rendition.method === "slide_crop" ? "contextual_crop" : item.rendition.method === "libreoffice_group" ? "group_render" : "isolated_render";
      if (assetByLocalId.get(item.rendition.asset_local_id)?.role !== role) throw new Error("Rendered graphic asset is unavailable.");
    }
  }
  return {
    deck_id: row.deck_id,
    source_revision: integer(row.source_revision, "source revision", 1),
    generation_id: row.generation_id,
    source_sha256: row.source_sha256,
    source_filename: text(row.source_filename, "source filename"),
    content: { slides },
    assets,
    warnings: row.warnings.map(parseWarning),
  };
}

export function validateManifestForDeck(deck: Deck, manifest: Manifest): Manifest {
  if (manifest.deck_id !== deck.deck_id || manifest.generation_id !== deck.generation_id ||
      manifest.source_revision > deck.source_revision ||
      (deck.state === "extracted" && manifest.source_revision !== deck.source_revision) ||
      manifest.source_sha256 !== deck.source_sha256) {
    throw new Error("The deck changed while its preview was loading. Refresh the deck list.");
  }
  return manifest;
}

export class LibraryClient {
  private readonly baseUrl: string;
  private readonly token: string;
  private pendingUpload: { fingerprint: string; key: string } | null = null;

  constructor(token: string) {
    const configuredUrl = import.meta.env?.VITE_LIBRARY_URL;
    this.baseUrl = typeof configuredUrl === "string" ? configuredUrl.replace(/\/$/, "") : "";
    if (token.length < 32) throw new Error("The library token must contain at least 32 characters.");
    this.token = token;
  }

  async request(path: string, init: RequestInit = {}): Promise<Response> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${this.token}`, ...init.headers },
    });
    if (!response.ok) {
      const data: unknown = await response.json().catch(() => null);
      if (typeof data === "object" && data !== null && "error" in data) {
        const envelope = object(data.error, "API error");
        if (typeof envelope.message === "string") throw new ApiError(envelope.message, response.status);
      }
      throw new ApiError(`Library request failed (${response.status}).`, response.status);
    }
    return response;
  }

  async decks(): Promise<Deck[]> {
    const value: unknown = await (await this.request("/v1/decks")).json();
    if (!Array.isArray(value)) throw new Error("Invalid deck list response.");
    return value.map(parseDeck);
  }

  async deletions(): Promise<DeletionStatus[]> {
    const value: unknown = await (await this.request("/v1/deletions")).json();
    if (!Array.isArray(value)) throw new Error("Invalid deletion list response.");
    return value.map(parseDeletion);
  }

  async uploadPolicy(): Promise<UploadPolicy> {
    return parseUploadPolicy(await (await this.request("/v1/policy")).json());
  }

  async deck(deckId: string): Promise<Deck> {
    return parseDeck(await (await this.request(`/v1/decks/${deckId}`)).json());
  }

  async search(query: string, options: { deckId?: string; limit?: number; offset?: number; signal?: AbortSignal } = {}): Promise<SearchResponse> {
    return parseSearchResponse(await (await this.request("/v1/search", { method: "POST", ...(options.signal ? { signal: options.signal } : {}),
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, deck_id: options.deckId ?? null, limit: options.limit ?? 20, offset: options.offset ?? 0 }) })).json());
  }

  async upload(file: File): Promise<Accepted> {
    const fingerprint = `${file.name}\0${file.size}\0${file.lastModified}`;
    if (this.pendingUpload?.fingerprint !== fingerprint) {
      this.pendingUpload = { fingerprint, key: crypto.randomUUID() };
    }
    const body = new FormData();
    body.set("file", file);
    try {
      const accepted = parseAccepted(await (await this.request("/v1/decks", {
        method: "POST", body, headers: { "Idempotency-Key": this.pendingUpload.key },
      })).json());
      this.pendingUpload = null;
      return accepted;
    } catch (error) {
      if (error instanceof ApiError && error.status < 500 && error.status !== 408 && error.status !== 429) {
        this.pendingUpload = null;
      }
      throw error;
    }
  }

  async operation(operationId: string): Promise<Operation> {
    const value: unknown = await (await this.request(`/v1/operations/${operationId}`)).json();
    const row = object(value, "operation");
    return { operation_id: text(row.operation_id, "operation ID"), state: text(row.state, "operation state"), phase: text(row.phase, "operation phase"), error: parseError(row.error) };
  }

  async act(deckId: string, action: "retry" | "reprocess"): Promise<Accepted> {
    return parseAccepted(await (await this.request(`/v1/decks/${deckId}/${action}`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } })).json());
  }

  async remove(deckId: string): Promise<Accepted> {
    return parseAccepted(await (await this.request(`/v1/decks/${deckId}`, { method: "DELETE" })).json());
  }

  async retrySearch(deckId: string): Promise<SearchStatus> {
    return parseSearchStatus(await (await this.request(`/v1/decks/${deckId}/search/retry`, {
      method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() },
    })).json());
  }

  async manifest(deck: Deck): Promise<Manifest> {
    if (deck.generation_id === null) throw new Error("This deck has no complete generation yet.");
    const manifest = parseManifest(await (await this.request(`/v1/decks/${deck.deck_id}/manifest?generation=${deck.generation_id}`)).json());
    return validateManifestForDeck(deck, manifest);
  }

  async download(path: string, filename: string): Promise<void> {
    const response = await this.request(path);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async image(path: string): Promise<string> {
    const response = await this.request(path);
    return URL.createObjectURL(await response.blob());
  }
}
