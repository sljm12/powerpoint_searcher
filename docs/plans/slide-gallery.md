# Slide gallery

This feature follows the [project constitution](../constitution.md). It changes frontend browsing only and has no constitutional conflicts.

## User behavior

Selecting **View slides and graphics** opens a dedicated gallery for the selected deck. Previews appear automatically. Users choose how many slides appear in each row. On narrow screens the layout keeps previews readable.

Selecting a slide opens a modal with its larger preview, slide download, components, component previews, original embedded pictures, rendered downloads, and applicable warnings. Closing the modal returns to the selected slide. Returning to the library restores the deck controls.

## Design

The frontend continues to use the library service's existing manifest and authenticated asset endpoints. `Deck`, `Manifest`, and `Slide` remain the authoritative data shapes. The gallery owns its display lifetime and image URLs. A native HTML dialog provides modal keyboard focus and Escape handling.

A dedicated page section was selected over a fullscreen gallery dialog. The section gives the gallery room while keeping one modal layer for slide details. Existing rendering and download behavior is reused in the details view. No backend contracts or dependencies change.

## Implementation tasks

- Replace inline component lists with a dedicated preview gallery and row-size control.
- Move slide components into a slide-details modal and preserve download labels and warnings.
- Handle loading, empty decks, unavailable previews, errors, retries, and late image responses after closing.
- Release image URLs on close and invalidate browsing when its deck generation disappears or changes.
- Add focused automated behavior checks and verify the running application with a real extracted deck.

## Verification

Run the frontend's `npm run check`, `npm test`, and `npm run build`. On the running application, open an extracted deck, change row size, open slide details, inspect a component preview, close with Escape, and return to the library. Record actual results after implementation.

Type checking, all seven client tests, and the production build passed on 2026-11-07. The browser fixture passed gallery previews, column changes, modal details, download routes, warnings, retry, focus restoration, and late image cleanup. The gallery and modal were also inspected visually with synthetic slides.

Independent review found that automatic gallery closure could focus a deck button before a refresh removed it. Closure now follows the deck-list rebuild. The added browser assertions for generation changes and deletion remain unverified because computer automation stopped when it could not establish Chrome's current URL. The real authenticated deck workflow was not rechecked after the page reload cleared its in-memory token.
