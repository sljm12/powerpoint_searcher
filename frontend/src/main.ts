import { LibraryClient, type Deck, type DeckState } from "./client";
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
const status = requiredElement<HTMLElement>("#status");
const library = requiredElement<HTMLElement>("#library");
const deckList = requiredElement<HTMLElement>("#decks");
const cleanup = requiredElement<HTMLElement>("#cleanup");
const deletionList = requiredElement<HTMLElement>("#deletion-list");
const refreshButton = requiredElement<HTMLButtonElement>("#refresh");

let client: LibraryClient | null = null;
let refreshTimer: number | undefined;
let gallery: Gallery | null = null;
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
    const top = el("div", "deck-top");
    const title = el("div", "deck-title");
    title.append(el("h3", "", deck.filename));
    title.append(el("span", `state state-${deck.state}`, stateLabel(deck.state)));
    top.append(title);
    top.append(el("p", "deck-meta", `${deck.byte_length.toLocaleString()} bytes · Revision ${deck.source_revision}`));
    article.append(top);

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
    if (!card || card.dataset.state !== deck.state || card.dataset.generationId !== (deck.generation_id ?? "")) return false;
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
    client = new LibraryClient(tokenInput.value);
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

refreshButton.addEventListener("click", () => void renderDecks(true).then(() => showStatus("Library refreshed.")).catch(reportError));
fileInput.addEventListener("change", () => {
  fileName.textContent = fileInput.files?.item(0)?.name ?? "No file selected";
});
