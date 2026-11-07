import { LibraryClient, type Asset, type Deck, type DeckState, type Manifest, type Occurrence } from "./client";
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
let imageUrls: string[] = [];

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

function assetFor(manifest: Manifest, localId: string | null): Asset | undefined {
  return localId === null ? undefined : manifest.assets.find((asset) => asset.local_id === localId);
}

function clearImages(): void {
  for (const url of imageUrls) URL.revokeObjectURL(url);
  imageUrls = [];
}

async function showPreview(deck: Deck, article: HTMLElement): Promise<void> {
  if (client === null) return;
  const preview = article.querySelector<HTMLElement>(".preview-area");
  if (!preview) return;
  preview.replaceChildren(el("p", "hint", "Loading slide previews…"));
  const manifest = await client.manifest(deck);
  const list = el("div", "slide-grid");
  for (const slide of manifest.content.slides) {
    const slideCard = el("article", "slide-card");
    slideCard.append(el("h4", "slide-label", `Slide ${slide.source.number}${slide.source.hidden ? " · Hidden" : ""}`));
    const asset = assetFor(manifest, slide.preview.asset_local_id);
    if (asset) {
      slideCard.append(actionButton("Show slide preview", async (button) => {
        const existing = slideCard.querySelector<HTMLImageElement>(".slide-image");
        if (existing) {
          URL.revokeObjectURL(existing.src);
          existing.remove();
          button.textContent = "Show slide preview";
          return;
        }
        const image = el("img", "slide-image");
        image.alt = `Slide ${slide.source.number} preview from ${deck.filename}`;
        image.src = await client?.image(asset.download_url) ?? "";
        imageUrls.push(image.src);
        slideCard.insertBefore(image, slideCard.querySelector(".download-slide"));
        button.textContent = "Hide slide preview";
      }, "button button-small"));
      const download = actionButton("Download slide PNG", () => client?.download(asset.download_url, `${deck.filename}-slide-${slide.source.number}.png`), "button button-small");
      download.classList.add("download-slide");
      slideCard.append(download);
    }
    for (const occurrence of slide.occurrences) slideCard.append(renderOccurrence(deck, manifest, occurrence));
    list.append(slideCard);
  }
  preview.replaceChildren(list);
}

function renderOccurrence(deck: Deck, manifest: Manifest, occurrence: Occurrence): HTMLElement {
  const card = el("div", "graphic-row");
  const heading = el("div", "graphic-heading");
  heading.append(el("strong", "graphic-name", occurrence.source.source_name || occurrence.source.kind));
  heading.append(el("span", "graphic-kind", occurrence.source.kind.replaceAll("_", " ")));
  card.append(heading);
  if (occurrence.source.alt_text) card.append(el("p", "graphic-text", `Alt text: ${occurrence.source.alt_text}`));
  if (occurrence.source.text) card.append(el("p", "graphic-text", occurrence.source.text));

  const rendition = occurrence.rendition;
  const rendered = assetFor(manifest, rendition.asset_local_id);
  if (rendered) {
    const label = rendition.method === "slide_crop" ? "Contextual crop" : rendition.method === "libreoffice_group" ? "Rendered group" : "Rendered graphic";
    if (rendition.method === "slide_crop") {
      card.append(el("p", "warning", "Slide crop. It may include surrounding objects or the slide background. It is not an isolated graphic."));
    }
    for (const warning of rendition.warnings) card.append(el("p", "warning", warning.message));
    card.append(el("p", "hint", `Slide ${occurrence.source.slide} · Export method: ${rendition.method} · ${rendered.media_type}`));
    card.append(actionButton(`Download ${label.toLowerCase()}`, () => client?.download(rendered.download_url, `${deck.filename}-slide-${occurrence.source.slide}-${label.toLowerCase().replaceAll(" ", "-")}.png`), "button button-small"));
    card.append(actionButton("Show preview", async () => {
      const prior = card.querySelector<HTMLImageElement>(".graphic-image");
      if (prior) {
        URL.revokeObjectURL(prior.src);
        prior.remove();
        return;
      }
      const image = el("img", "graphic-image");
      image.alt = `${label} for ${occurrence.source.source_name}, slide ${occurrence.source.slide}`;
      image.src = await client?.image(rendered.download_url) ?? "";
      imageUrls.push(image.src);
      card.append(image);
    }, "button button-small"));
  }
  if (occurrence.original_available) {
    const original = assetFor(manifest, occurrence.original_asset_local_id);
    if (original) card.append(actionButton("Download original embedded picture", () => client?.download(original.download_url, `${deck.filename}-slide-${occurrence.source.slide}-original.${extension(original.media_type)}`), "button button-small"));
  }
  for (const warning of manifest.warnings.filter((item) => item.affected_occurrence_ids.includes(occurrence.source.id))) {
    card.append(el("p", "warning", warning.message));
  }
  return card;
}

function extension(mediaType: string): string {
  const known: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/tiff": "tiff", "image/bmp": "bmp", "image/svg+xml": "svg" };
  return known[mediaType] ?? "bin";
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
  clearImages();
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
        article.append(actionButton(previous ? "View previous generation" : "View slides and graphics", () => showPreview(deck, article), "button button-quiet"));
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
    const preview = el("div", "preview-area");
    article.append(preview);
    deckList.append(article);
  }
  if (decks.length === 0) deckList.append(el("div", "empty-state card", "Your library is ready for its first deck."));
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
