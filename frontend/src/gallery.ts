import type { Asset, Deck, LibraryClient, Manifest, Occurrence, Slide } from "./client";

export type Gallery = { deckId: string; generationId: string | null; close: () => void };

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function assetFor(manifest: Manifest, localId: string | null): Asset | undefined {
  return manifest.assets.find((asset) => asset.local_id === localId);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The request failed. Try again.";
}

export function openGallery({ client, deck, opener, libraryPage, onClose, initialSlide }: {
  client: Pick<LibraryClient, "manifest" | "image" | "download">;
  deck: Deck;
  opener: HTMLButtonElement;
  libraryPage: HTMLElement;
  onClose: () => void;
  initialSlide?: number;
}): Gallery {
  const scrollY = window.scrollY;
  const gallery = el("main", "gallery");
  const dialog = el("dialog", "slide-dialog");
  const galleryUrls = new Set<string>();
  const detailUrls = new Set<string>();
  let closed = false;
  let detailOwner: HTMLElement | null = null;
  const header = el("header", "gallery-header");
  const title = el("h1", "gallery-title", deck.filename);
  title.id = "gallery-title";
  gallery.setAttribute("aria-labelledby", title.id);
  const feedback = el("p", "gallery-feedback");
  feedback.setAttribute("role", "status");
  const grid = el("div", "slide-grid");
  grid.style.setProperty("--slide-columns", "3");
  const count = el("p", "hint", "Loading slides...");

  function button(label: string, action: (button: HTMLButtonElement) => void | Promise<void>, className = "button button-quiet"): HTMLButtonElement {
    const node = el("button", className, label);
    node.type = "button";
    node.addEventListener("click", () => {
      void Promise.resolve().then(() => action(node)).catch((error: unknown) => {
        if (closed) return;
        const target = dialog.open ? dialog.querySelector<HTMLElement>(".detail-feedback") : feedback;
        if (target) target.textContent = errorMessage(error);
      });
    });
    return node;
  }

  function revoke(urls: Set<string>): void {
    for (const url of urls) URL.revokeObjectURL(url);
    urls.clear();
  }

  function close(): void {
    if (closed) return;
    closed = true;
    detailOwner = null;
    dialog.close();
    dialog.remove();
    gallery.remove();
    revoke(galleryUrls);
    revoke(detailUrls);
    libraryPage.hidden = false;
    document.body.classList.remove("gallery-open", "slide-dialog-open");
    onClose();
    if (opener.isConnected) opener.focus({ preventScroll: true });
    window.scrollTo(0, scrollY);
  }

  function imagePreview(asset: Asset | undefined, alt: string, urls: Set<string>): HTMLElement {
    const frame = el("div", "preview-frame");
    if (!asset) {
      frame.append(el("p", "preview-message", "Preview unavailable"));
      return frame;
    }
    const availableAsset = asset;
    async function load(): Promise<void> {
      frame.replaceChildren(el("p", "preview-message", "Loading preview..."));
      try {
        const url = await client.image(availableAsset.download_url);
        if (closed || !frame.isConnected) {
          URL.revokeObjectURL(url);
          return;
        }
        urls.add(url);
        const image = el("img", "slide-image");
        image.alt = alt;
        image.addEventListener("error", () => {
          URL.revokeObjectURL(url);
          urls.delete(url);
          failed("The preview image could not be displayed.");
        }, { once: true });
        image.src = url;
        frame.replaceChildren(image);
      } catch (error) {
        if (!closed && frame.isConnected) failed(errorMessage(error));
      }
    }
    function failed(message: string): void {
      frame.replaceChildren(el("p", "preview-message", message), button("Retry preview", load, "button button-small"));
    }
    queueMicrotask(() => { if (!closed && frame.isConnected) void load(); });
    return frame;
  }

  function renderOccurrence(manifest: Manifest, occurrence: Occurrence): HTMLElement {
    const card = el("article", "graphic-row");
    const heading = el("div", "graphic-heading");
    heading.append(el("h3", "graphic-name", occurrence.source.source_name || occurrence.source.kind));
    heading.append(el("span", "graphic-kind", occurrence.source.kind.replaceAll("_", " ")));
    card.append(heading);
    if (occurrence.source.alt_text) card.append(el("p", "graphic-text", `Alt text: ${occurrence.source.alt_text}`));
    if (occurrence.source.text) card.append(el("p", "graphic-text", occurrence.source.text));
    const rendition = occurrence.rendition;
    const rendered = assetFor(manifest, rendition.asset_local_id);
    if (rendition.method === "slide_crop") card.append(el("p", "warning", "Slide crop. It may include surrounding objects or the slide background. It is not an isolated graphic."));
    for (const warning of rendition.warnings) card.append(el("p", "warning", warning.message));
    if (rendered) {
      const label = rendition.method === "slide_crop" ? "Contextual crop" : rendition.method === "libreoffice_group" ? "Rendered group" : "Rendered graphic";
      card.append(el("p", "hint", `Slide ${occurrence.source.slide} · Export method: ${rendition.method} · ${rendered.media_type}`));
      card.append(button(`Download ${label.toLowerCase()}`, () => client.download(rendered.download_url, `${deck.filename}-slide-${occurrence.source.slide}-${label.toLowerCase().replaceAll(" ", "-")}.png`), "button button-small"));
      card.append(button("Show preview", (toggle) => {
        const previous = card.querySelector<HTMLElement>(".preview-frame");
        if (previous) {
          const image = previous.querySelector<HTMLImageElement>("img");
          if (image) {
            URL.revokeObjectURL(image.src);
            detailUrls.delete(image.src);
          }
          previous.remove();
          toggle.textContent = "Show preview";
        } else {
          card.append(imagePreview(rendered, `${label} for ${occurrence.source.source_name}, slide ${occurrence.source.slide}`, detailUrls));
          toggle.textContent = "Hide preview";
        }
      }, "button button-small"));
    }
    if (occurrence.original_available) {
      const original = assetFor(manifest, occurrence.original_asset_local_id);
      if (original) {
        const extensions: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/tiff": "tiff", "image/bmp": "bmp", "image/svg+xml": "svg" };
        card.append(button("Download original embedded picture", () => client.download(original.download_url, `${deck.filename}-slide-${occurrence.source.slide}-original.${extensions[original.media_type] ?? "bin"}`), "button button-small"));
      }
    }
    for (const warning of manifest.warnings.filter((item) => item.affected_occurrence_ids.includes(occurrence.source.id))) card.append(el("p", "warning", warning.message));
    return card;
  }

  function showSlide(manifest: Manifest, slide: Slide, trigger: HTMLButtonElement): void {
    revoke(detailUrls);
    detailOwner = trigger;
    const heading = el("div", "detail-heading");
    const title = el("h2", "", `Slide ${slide.source.number}${slide.source.hidden ? " · Hidden" : ""}`);
    title.id = "slide-detail-title";
    heading.append(title, button("Close slide", () => dialog.close()));
    const source = el("p", "detail-source", deck.filename);
    const feedback = el("p", "detail-feedback error-copy");
    feedback.setAttribute("role", "status");
    const content = el("div", "detail-layout");
    const preview = el("section", "detail-preview");
    const asset = assetFor(manifest, slide.preview.asset_local_id);
    preview.append(imagePreview(asset, `Slide ${slide.source.number} preview from ${deck.filename}`, detailUrls));
    if (asset) preview.append(button("Download slide PNG", () => client.download(asset.download_url, `${deck.filename}-slide-${slide.source.number}.png`), "button button-dark"));
    for (const warning of slide.preview.warnings) preview.append(el("p", "warning", warning.message));
    const components = el("section", "slide-components");
    components.setAttribute("aria-label", "Slide components");
    components.append(el("h2", "components-title", `Components (${slide.occurrences.length})`));
    if (slide.occurrences.length === 0) components.append(el("p", "hint", "No components were extracted for this slide."));
    for (const occurrence of slide.occurrences) components.append(renderOccurrence(manifest, occurrence));
    const notes = el("section", "slide-notes");
    notes.setAttribute("aria-label", "Speaker notes");
    notes.append(el("h2", "components-title", "Speaker notes"));
    if (!slide.notes) notes.append(el("p", "hint", "Speaker notes are unavailable for this older manifest."));
    else if (slide.notes.state === "excluded") notes.append(el("p", "hint", "Speaker notes were excluded during extraction."));
    else if (slide.notes.state === "empty") notes.append(el("p", "hint", "This slide has no speaker notes."));
    else {
      for (const paragraph of slide.notes.paragraphs) notes.append(el("p", "notes-paragraph", paragraph));
      if (slide.notes.truncated) notes.append(el("p", "hint", "Speaker notes were shortened to fit the supported text limit."));
    }
    content.append(preview, components, notes);
    dialog.replaceChildren(heading, source, feedback, content);
    dialog.showModal();
    dialog.scrollTop = 0;
    document.body.classList.add("slide-dialog-open");
  }

  const back = button("Back to library", close);
  const identity = el("div", "gallery-identity");
  identity.append(el("p", "eyebrow", "SLIDE GALLERY"), title, count);
  header.append(back, identity, button("Download original deck", () => client.download(deck.original_url, deck.filename), "button button-dark"));
  const toolbar = el("div", "gallery-toolbar");
  const guide = el("p", "hint", "Select a slide to explore its components and downloads.");
  const columnLabel = el("label", "column-control", "Slides per row");
  const columns = el("select", "column-select");
  for (let number = 1; number <= 6; number += 1) {
    const option = el("option", "", String(number));
    option.value = String(number);
    columns.append(option);
  }
  columns.value = "3";
  columns.addEventListener("change", () => grid.style.setProperty("--slide-columns", columns.value));
  columnLabel.append(columns);
  toolbar.append(guide, columnLabel);
  gallery.append(header, toolbar, feedback, grid);
  dialog.setAttribute("aria-labelledby", "slide-detail-title");
  dialog.addEventListener("close", () => {
    revoke(detailUrls);
    dialog.replaceChildren();
    document.body.classList.remove("slide-dialog-open");
    detailOwner?.focus({ preventScroll: true });
    detailOwner = null;
  });
  libraryPage.hidden = true;
  document.body.classList.add("gallery-open");
  document.body.append(gallery, dialog);
  back.focus({ preventScroll: true });
  window.scrollTo(0, 0);

  async function loadManifest(): Promise<void> {
    feedback.textContent = "Loading slide previews...";
    grid.replaceChildren();
    try {
      const manifest = await client.manifest(deck);
      if (closed) return;
      feedback.textContent = "";
      count.textContent = `${manifest.content.slides.length} slides · Revision ${manifest.source_revision}${deck.state !== "extracted" ? " · Previous generation" : ""}`;
      if (manifest.content.slides.length === 0) grid.append(el("p", "empty-state card", "No slides were extracted from this deck."));
      for (const warning of manifest.warnings.filter((item) => item.affected_occurrence_ids.length === 0)) gallery.insertBefore(el("p", "warning", warning.message), grid);
      for (const slide of manifest.content.slides) {
        const card = el("article", "slide-card");
        const trigger = button(`Slide ${slide.source.number}${slide.source.hidden ? " · Hidden" : ""}`, () => showSlide(manifest, slide, trigger), "slide-select");
        trigger.setAttribute("aria-label", `Open slide ${slide.source.number}${slide.source.hidden ? ", hidden" : ""}`);
        trigger.setAttribute("aria-haspopup", "dialog");
        trigger.dataset.slideNumber = String(slide.source.number);
        const preview = imagePreview(assetFor(manifest, slide.preview.asset_local_id), `Slide ${slide.source.number} preview from ${deck.filename}`, galleryUrls);
        const footer = el("div", "slide-caption");
        footer.append(el("span", "slide-label", trigger.textContent ?? ""), el("span", "hint", `${slide.occurrences.length} components`));
        trigger.replaceChildren(footer);
        card.append(preview, trigger);
        preview.addEventListener("click", (event) => {
          if (event.target instanceof HTMLButtonElement) return;
          trigger.click();
        });
        grid.append(card);
      }
      if (initialSlide !== undefined) {
        const selected = grid.querySelector<HTMLButtonElement>(`[data-slide-number="${initialSlide}"]`);
        if (selected) selected.click();
        else feedback.textContent = `Slide ${initialSlide} is no longer available. Refresh search results.`;
      }
    } catch (error) {
      if (closed) return;
      count.textContent = "Slides could not be loaded";
      feedback.textContent = errorMessage(error);
      grid.append(button("Retry loading slides", loadManifest));
    }
  }
  void loadManifest();
  return { deckId: deck.deck_id, generationId: deck.generation_id, close };
}
