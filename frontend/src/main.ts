import { LibraryClient, type Deck, type DeckState, type SearchGroup, type SearchMatch, type SearchPolicy } from "./client";
import { openGallery, type Gallery } from "./gallery";
import "./style.css";

function requiredElement<T extends HTMLElement>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (element === null) throw new Error(`The library page is missing ${selector}.`);
  return element;
}

const tokenInput = requiredElement<HTMLInputElement>("#token");
const connectForm = requiredElement<HTMLFormElement>("#connect-form");
const uploadForm = requiredElement<HTMLFormElement>("#upload-form");
const fileInput = requiredElement<HTMLInputElement>("#deck-file");
const fileName = requiredElement<HTMLElement>("#file-name");
const notesPolicy = requiredElement<HTMLElement>("#notes-policy");
const status = requiredElement<HTMLElement>("#status");
const library = requiredElement<HTMLElement>("#library");
const deckList = requiredElement<HTMLElement>("#decks");
const cleanup = requiredElement<HTMLElement>("#cleanup");
const deletionList = requiredElement<HTMLElement>("#deletion-list");
const refreshButton = requiredElement<HTMLButtonElement>("#refresh");
const searchForm = requiredElement<HTMLFormElement>("#search-form");
const searchInput = requiredElement<HTMLInputElement>("#search-query");
const searchButton = requiredElement<HTMLButtonElement>("#search-submit");
const searchDisclosure = requiredElement<HTMLElement>("#search-disclosure");
const searchStatus = requiredElement<HTMLElement>("#search-status");
const searchResults = requiredElement<HTMLElement>("#search-results");

let client: LibraryClient | null = null;
let refreshTimer: number | undefined;
let gallery: Gallery | null = null;
let searchPolicy: SearchPolicy | null = null;
let searchController: AbortController | null = null;
let searchSequence = 0;
let searchOffset: number | null = null;
const searchImageUrls = new Set<string>();
const libraryPage = requiredElement<HTMLElement>("#app");

function showStatus(message: string, kind: "info" | "error" = "info"): void {
  status.textContent = message;
  status.className = `status status-${kind}`;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function actionButton(label: string, action: (button: HTMLButtonElement) => void | Promise<void>, className = "button button-quiet"): HTMLButtonElement {
  const button = el("button", className, label);
  button.type = "button";
  button.addEventListener("click", () => void Promise.resolve(action(button)).catch(reportError));
  return button;
}

function reportError(error: unknown): void {
  showStatus(error instanceof Error ? error.message : "The request failed.", "error");
}

function showNotesPolicy(includeNotes: boolean): void {
  notesPolicy.textContent = includeNotes
    ? "Speaker notes are included in extracted content and can be read by library users. The original deck keeps its notes."
    : "Speaker notes are excluded from extracted content. The original deck keeps its notes.";
}

function showSearchPolicy(policy: SearchPolicy): void {
  searchPolicy = policy;
  searchDisclosure.textContent = policy.disclosure;
  searchButton.disabled = policy.state !== "ready";
  if (policy.state !== "ready") searchStatus.textContent = policy.state === "unavailable"
    ? "Search is temporarily unavailable. Your library remains available."
    : "Text search is not configured.";
}

function stateLabel(state: DeckState): string {
  switch (state) {
    case "queued": return "Queued";
    case "processing": return "Extracting";
    case "extracted": return "Extracted";
    case "failed": return "Failed";
    case "deleting": return "Removing";
    case "deleted": return "Deleted";
    default: {
      const exhaustive: never = state;
      return exhaustive;
    }
  }
}

async function renderDecks(force = false): Promise<void> {
  if (client === null) return;
  const [decks, deletions] = await Promise.all([client.decks(), client.deletions()]);
  const pendingDeletions = deletions.filter((deletion) => deletion.state === "deleting");
  cleanup.hidden = pendingDeletions.length === 0;
  deletionList.replaceChildren(...pendingDeletions.map((deletion) => {
    const row = el("p", "deletion-row", `Deck ${deletion.deck_id} cleanup is in progress.`);
    row.dataset.operationId = deletion.operation_id;
    return row;
  }));

  if (!force && updateExistingDecks(decks)) {
    scheduleRefresh(decks, pendingDeletions.length);
    return;
  }
  deckList.replaceChildren();
  for (const deck of decks) {
    const article = el("article", "deck-card card");
    article.dataset.deckId = deck.deck_id;
    article.dataset.state = deck.state;
    article.dataset.generationId = deck.generation_id ?? "";
    article.dataset.searchState = deck.search?.state ?? "";
    article.dataset.searchProgress = `${deck.search?.completed_segments ?? 0}/${deck.search?.total_segments ?? 0}`;
    const top = el("div", "deck-top");
    const title = el("div", "deck-title");
    title.append(el("h3", "", deck.filename));
    title.append(el("span", `state state-${deck.state}`, stateLabel(deck.state)));
    top.append(title);
    top.append(el("p", "deck-meta", `${deck.byte_length.toLocaleString()} bytes · Revision ${deck.source_revision}`));
    article.append(top);
    if (deck.search?.state === "queued" || deck.search?.state === "processing") {
      article.append(el("p", "search-index-status", `Search indexing ${deck.search.completed_segments} of ${deck.search.total_segments} text segments.`));
    } else if (deck.search?.state === "ready") {
      article.append(el("p", "search-index-status", `Search ready · ${deck.search.searchable_slides} searchable, ${deck.search.blank_slides} blank, ${deck.search.hidden_slides} hidden slides.`));
    } else if (deck.search?.state === "failed") {
      article.append(el("p", "error-copy", deck.search.error?.message ?? "Search indexing failed. Reprocess this deck to retry."));
      if (deck.search.error?.retryable) article.append(actionButton("Retry search indexing", async () => {
        await client?.retrySearch(deck.deck_id);
        await renderDecks(true);
      }, "button button-accent"));
    }

    if (deck.state === "queued" || deck.state === "processing") {
      article.append(el("p", "progress-copy", `${deck.stage} · ${deck.completed_slides} of ${deck.total_slides} slides`));
      const progress = el("progress", "progress");
      progress.max = Math.max(deck.total_slides, 1);
      progress.value = deck.completed_slides;
      progress.setAttribute("aria-label", `Processing ${deck.filename}`);
      article.append(progress);
    }
    if (deck.state === "failed" && deck.error) {
      article.append(el("p", "error-copy", deck.error.message));
      if (deck.error.retryable) article.append(actionButton("Retry extraction", async () => {
        await client?.act(deck.deck_id, "retry");
        await renderDecks(true);
      }, "button button-accent"));
    }
    if (deck.warnings.length > 0) for (const warning of deck.warnings) article.append(el("p", "warning", warning.message));

    if (deck.state !== "deleting" && deck.state !== "deleted") {
      article.append(actionButton("Download original deck", () => client?.download(deck.original_url, deck.filename), "button button-dark"));
      if (deck.generation_id !== null) {
        const previous = deck.state !== "extracted";
        const galleryButton = actionButton(previous ? "View previous generation" : "View slides and graphics", (opener) => {
          if (client === null) return;
          gallery?.close();
          gallery = openGallery({ client, deck, opener, libraryPage, onClose: () => {
            gallery = null;
            if (!opener.isConnected) {
              const replacement = deckList.querySelector<HTMLButtonElement>(`[data-gallery-deck="${deck.deck_id}"]`);
              (replacement ?? refreshButton).focus({ preventScroll: true });
            }
          } });
        }, "button button-quiet");
        galleryButton.dataset.galleryDeck = deck.deck_id;
        article.append(galleryButton);
      }
      if (deck.state === "extracted" && deck.generation_id !== null) {
        article.append(actionButton("Reprocess deck", async () => {
          await client?.act(deck.deck_id, "reprocess");
          await renderDecks(true);
        }, "button button-quiet"));
      }
      article.append(actionButton("Delete deck", async () => {
        if (!window.confirm(`Delete ${deck.filename} and its extracted files?`)) return;
        const accepted = await client?.remove(deck.deck_id);
        if (accepted) void followDeletion(accepted).catch(reportError);
        showStatus("Deck removed from the active library. Cleanup will finish in the background.");
        await renderDecks(true);
      }, "button button-danger"));
    } else {
      article.append(el("p", "hint", "Downloads are disabled while cleanup finishes."));
    }
    deckList.append(article);
  }
  if (decks.length === 0) deckList.append(el("div", "empty-state card", "Your library is ready for its first deck."));
  if (gallery !== null) {
    const openDeck = decks.find((deck) => deck.deck_id === gallery?.deckId);
    if (!openDeck || openDeck.state === "deleting" || openDeck.state === "deleted" || openDeck.generation_id !== gallery.generationId) gallery.close();
  }
  scheduleRefresh(decks, pendingDeletions.length);
}

function updateExistingDecks(decks: Deck[]): boolean {
  const cards = Array.from(deckList.querySelectorAll<HTMLElement>(".deck-card"));
  if (cards.length !== decks.length) return false;
  for (const deck of decks) {
    const card = cards.find((item) => item.dataset.deckId === deck.deck_id);
    if (!card || card.dataset.state !== deck.state || card.dataset.generationId !== (deck.generation_id ?? "") ||
        card.dataset.searchState !== (deck.search?.state ?? "") ||
        card.dataset.searchProgress !== `${deck.search?.completed_segments ?? 0}/${deck.search?.total_segments ?? 0}`) return false;
  }
  for (const deck of decks) {
    const card = cards.find((item) => item.dataset.deckId === deck.deck_id);
    if (card && (deck.state === "queued" || deck.state === "processing")) {
      const text = card.querySelector<HTMLElement>(".progress-copy");
      const progress = card.querySelector<HTMLProgressElement>("progress");
      if (text) text.textContent = `${deck.stage} · ${deck.completed_slides} of ${deck.total_slides} slides`;
      if (progress) {
        progress.max = Math.max(deck.total_slides, 1);
        progress.value = deck.completed_slides;
      }
    }
  }
  return true;
}

function scheduleRefresh(decks: Deck[], pendingDeletions: number): void {
  window.clearTimeout(refreshTimer);
  if (pendingDeletions > 0 || decks.some((deck) => deck.state === "queued" || deck.state === "processing")) {
    refreshTimer = window.setTimeout(() => void renderDecks().catch(reportError), 1500);
  }
}

async function followDeletion(accepted: { operation_id: string }): Promise<void> {
  while (client !== null) {
    const operation = await client.operation(accepted.operation_id);
    if (operation.state === "succeeded") {
      showStatus("Deck and extracted files were removed.");
      return;
    }
    if (operation.state === "failed") {
      showStatus(operation.error?.message ?? "Deletion needs attention. Check cleanup status.", "error");
      return;
    }
    await new Promise<void>((resolve) => window.setTimeout(resolve, 1500));
  }
}

connectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const connectedClient = new LibraryClient(tokenInput.value);
    const policy = await connectedClient.uploadPolicy();
    client = connectedClient;
    showNotesPolicy(policy.include_notes);
    showSearchPolicy(policy.search);
    await renderDecks(true);
    library.hidden = false;
    showStatus("Connected to the shared library.");
    tokenInput.value = "";
    tokenInput.disabled = true;
    connectForm.querySelector<HTMLButtonElement>("button")?.setAttribute("disabled", "true");
  } catch (error) {
    client = null;
    reportError(error);
  }
});

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (client === null) return;
  const file = fileInput.files?.item(0);
  if (!file) return;
  try {
    const policy = await client.uploadPolicy();
    showNotesPolicy(policy.include_notes);
    showSearchPolicy(policy.search);
    showStatus(`Uploading ${file.name}…`);
    await client.upload(file);
    fileInput.value = "";
    fileName.textContent = "No file selected";
    showStatus("Upload accepted. Extraction is queued.");
    await renderDecks(true);
  } catch (error) {
    reportError(error);
  }
});

refreshButton.addEventListener("click", () => void (async () => {
  if (client === null) return;
  const policy = await client.uploadPolicy();
  showNotesPolicy(policy.include_notes);
  showSearchPolicy(policy.search);
  await renderDecks(true);
  showStatus("Library refreshed.");
})().catch(reportError));

function clearSearchResults(): void {
  for (const url of searchImageUrls) URL.revokeObjectURL(url);
  searchImageUrls.clear();
  searchResults.replaceChildren();
}

function renderMatch(group: SearchGroup, match: SearchMatch, sequence: number): HTMLElement {
  const article = el("article", "search-match card");
  const preview = el("div", "search-preview");
  preview.append(el("p", "preview-message", "Loading slide preview…"));
  const details = el("div", "search-match-copy");
  details.append(el("p", "eyebrow", `SLIDE ${match.slide_number} · ${match.matched_channel === "notes" ? "SPEAKER NOTES" : "SLIDE TEXT"}`));
  details.append(el("p", "search-snippet", match.snippet));
  for (const warning of match.warning_metadata) details.append(el("p", "warning", warning.message));
  const open = actionButton(`Open slide ${match.slide_number}`, async (button) => {
    if (!client) return;
    const current = await client.deck(group.deck_id);
    if (current.generation_id !== match.generation_id || current.search?.index_revision !== match.index_revision || current.search?.state !== "ready") {
      searchStatus.textContent = "This search result changed while you were browsing. Search again for the current slide.";
      return;
    }
    gallery?.close();
    gallery = openGallery({ client, deck: current, opener: button, libraryPage, initialSlide: match.slide_number,
      onClose: () => { gallery = null; if (!button.isConnected) refreshButton.focus({ preventScroll: true }); } });
  }, "button button-quiet");
  details.append(open);
  article.append(preview, details);
  void (async () => {
    try {
      if (!client || sequence !== searchSequence) return;
      const url = await client.image(match.preview_url);
      if (!preview.isConnected || sequence !== searchSequence) { URL.revokeObjectURL(url); return; }
      searchImageUrls.add(url);
      const image = el("img", "search-slide-image");
      image.src = url;
      image.alt = `Slide ${match.slide_number} preview from ${group.filename}`;
      preview.replaceChildren(image);
    } catch {
      if (preview.isConnected && sequence === searchSequence) preview.replaceChildren(el("p", "preview-message", "Preview unavailable"));
    }
  })();
  return article;
}

function renderSearchGroups(groups: SearchGroup[], sequence: number, append = false): void {
  if (!append) clearSearchResults();
  for (const group of groups) {
    const section = el("section", "search-deck-group");
    section.append(el("h3", "search-deck-title", group.filename));
    for (const match of group.matches) section.append(renderMatch(group, match, sequence));
    if (group.has_more_matches && group.matches.length) {
      let matchOffset = group.matches.length;
      const more = actionButton(`More matches in ${group.filename}`, async (button) => {
        if (!client) return;
        button.disabled = true;
        const response = await client.search(searchInput.value, { deckId: group.deck_id, limit: 20,
          offset: matchOffset, ...(searchController ? { signal: searchController.signal } : {}) });
        if (sequence !== searchSequence) return;
        const additional = response.decks[0]?.matches ?? [];
        for (const match of additional) section.insertBefore(renderMatch(group, match, sequence), button);
        matchOffset += additional.length;
        if (!response.decks[0]?.has_more_matches) button.remove();
        else button.disabled = false;
      }, "button button-small");
      section.append(more);
    }
    searchResults.append(section);
  }
}

async function submitSearch(query: string, offset = 0, append = false): Promise<void> {
  if (!client || searchPolicy?.state !== "ready") return;
  if (!append) {
    searchController?.abort();
    searchController = new AbortController();
    searchSequence += 1;
    searchOffset = null;
    clearSearchResults();
  }
  const sequence = searchSequence;
  searchStatus.textContent = "Searching indexed slides…";
  try {
    const result = await client.search(query, { limit: 20, offset, ...(searchController ? { signal: searchController.signal } : {}) });
    if (sequence !== searchSequence) return;
    searchOffset = result.next_offset;
    const notes = result.warnings.map((warning) => warning.message);
    if (result.mode === "lexical") notes.unshift("Semantic matching is unavailable. These results use slide text only; cross-language and meaning-based matches may be missing.");
    searchStatus.textContent = [result.degraded ? "Search is degraded." : "", ...notes,
      `${result.coverage.eligible_slides ?? 0} visible slides are eligible; ${result.coverage.blank_slides ?? 0} are blank and ${result.coverage.hidden_slides ?? 0} are hidden.`].filter(Boolean).join(" ");
    if (!result.decks.length && result.mode === "lexical") searchResults.append(el("p", "empty-state card", "No lexical matches were found. Semantic matching is currently unavailable."));
    else if (!result.decks.length) searchResults.append(el("p", "empty-state card", "No matching slides were found in the indexed library."));
    else renderSearchGroups(result.decks, sequence, append);
    searchResults.querySelector(".more-decks")?.remove();
    if (searchOffset !== null) searchResults.append(actionButton("More matching decks", () => submitSearch(query, searchOffset ?? 0, true), "button button-quiet more-decks"));
  } catch (error) {
    if (sequence !== searchSequence || (error instanceof DOMException && error.name === "AbortError")) return;
    searchStatus.textContent = error instanceof Error ? error.message : "Search request failed.";
    if (!append) searchResults.append(el("p", "error-copy", "Search could not be completed. Check the connection and try again."));
  }
}

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void submitSearch(searchInput.value.trim());
});
fileInput.addEventListener("change", () => {
  fileName.textContent = fileInput.files?.item(0)?.name ?? "No file selected";
});
