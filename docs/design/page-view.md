# Design: single-page view with thumbnail sidebar

Status: proposed (2026-11-23). Roadmap: Phase 2, "Page view". This view later becomes
the canvas for the page editor, stamps, and rehearsal marks.

## Goal
A Preview/Okular-style mode: one page large in the main area, a vertical strip of
thumbnails on the left. The user switches between it and the existing grid at any time,
and every existing edit (rotate, crop, delete, undo…) keeps working in both.

## Decisions
| Question | Decision | Why |
|---|---|---|
| Separate window or a mode of the main window? | **A mode of the main window.** A `Gtk.Stack` holds the existing grid and the new page view | Shares the model, selection, undo, and menus; no syncing between windows |
| Single page or continuous scroll? | **Single page in v1**, flipped with PageUp/PageDown, arrow keys, the sidebar, or scrolling past the page edge | Natural for sheet music; avoids rendering many large pages at once. Continuous scroll can come later |
| Sidebar data | **A second `Gtk.IconView` on the same `ListStore` model**, reusing `CellRendererImage` and the existing thumbnails | No duplicate rendering; edits show up in both views automatically |
| Reordering in the sidebar | **Not in v1.** The sidebar handles navigation and selection only | The grid's drag-and-drop code is large; reuse it in a later slice |
| How the big page is drawn | **Extract the compositing code from `PDFRenderer.update()`** into a reusable `draw_page(cr, page, pdfqueue, zoom)` used by both thumbnails and the page view | One place applies crop, rotation, hide, and layers, and later stamps |
| Render timing | A background thread renders the current page at screen resolution; until it's ready, the existing thumbnail is shown scaled up. Cache the current page ±1 | No blank flashes; bounded memory |
| HiDPI | Render at `widget.get_scale_factor()` × zoom | Sharp output with Windows display scaling |

## UI
- Enter: double-click a thumbnail, press Enter, View → Page view, or `Ctrl+2`.
- Leave: `Escape`, View → Grid view, or `Ctrl+1`. The grid scrolls to the current page.
- Zoom in the page view: fit page (default), fit width, `Ctrl` + scroll, or `Ctrl` `+`/`-`.
- The current page is always the single selected item in the model, so the existing actions
  (rotate, crop, delete, export selection, context menu) work unchanged.
- After the model changes (delete, undo, paste, reorder), the current page is re-resolved:
  the same page if it still exists, otherwise the nearest index.

## Code layout (keep upstream edits small)
- `pdfarranger/pagerender.py` (new): `draw_page()` plus size/zoom helpers, all pure cairo
  plus Poppler. `core.PDFRenderer.update()` becomes a thin call to it.
- `pdfarranger/pageview.py` (new): `PageView` widget (a `DrawingArea` in a `ScrolledWindow`,
  zoom modes, render thread, cache, an overlay-draw hook for future tools), and
  `PageViewMode`, which builds the `Gtk.Paned` (sidebar + `PageView`).
- `pdfarranger/pdfarranger.py`: only wiring. Wrap the grid in the `Gtk.Stack`, add the actions
  and accelerators, and hook up the double-click.
- `pdfarranger/config.py`: default accelerators for the new actions.
- `data/menu.ui`: View menu entries.

## Slices (one commit each, tests passing)
1. **Refactor:** extract `draw_page()`. Test: rendering a page through the old and new
   paths gives identical pixels, including for rotated, cropped, and layered pages
   (`tests/test_pagerender.py`, no display needed).
2. **`PageView` widget, standalone:** shows one `Page`, with fit modes, zoom, the background
   render and cache, and the scaled-thumbnail placeholder. Unit-test the zoom and fit math.
3. **Mode switching:** the Stack, sidebar `IconView` on the shared model, actions and accelerators,
   double-click and Escape.
4. **Navigation and sync:** keyboard, sidebar clicks, scrolling past the edge; current page ↔
   selection; re-resolving after model changes and undo.
5. **Editing from the page view:** verify the existing actions and context menu work on the current page;
   rotate and crop re-render immediately.
6. **Stress check:** a 300-page scanned score, flipping quickly through 100 pages (watch memory),
   a 4K monitor at 150% scaling, and switching modes during an export.

## Out of scope for v1
Continuous scroll, two-page spreads, reordering in the sidebar, and markup tools. The overlay-draw hook
exists so those tools can plug in later.
