# Roadmap (draft v0.1, 2026-11-22)

Goal: match macOS Preview's page manipulation and markup, then go beyond it with
tools built for managing digital sheet music. Built on PDF Arranger 1.14.0 (GPL-3.0).

Status key: ☐ not started · ◐ in progress · ☑ done · ❓ needs a decision

---

## Phase 0: Workspace ◐
- ☑ Windows/MSYS2 setup script, PyCharm run configs, dev guide
- ☑ Baseline: 89 unit tests green on upstream 1.14.0
- ☐ Fork created, kit committed, GitHub Actions enabled on the fork
- ☐ Ben runs `setup.ps1` on his desktop and launches the app from PyCharm

## Phase 1: Markup foundation (no UI yet)
The core design. Everything after this depends on getting it right.

- ☐ `Markup` data model: `MarkupItem` subclasses (`Rect`, `Ellipse`, `Line/Arrow`,
  `Ink`, `TextBox`, `Highlight`, `Stamp`) stored in a `Page.markup` list.
  Geometry is in normalized source-page space, plus stroke/fill color, opacity, and width
- ☐ Survives every existing operation: rotate, crop, split, duplicate,
  copy/paste (`serialize`), undo/redo, multiprocessing pickling
- ☐ Rendering: a cairo painter that draws markup over thumbnails and over the future editor canvas
- ☐ Export, in both pikepdf code paths:
  - **Editable mode**: real PDF annotations (`/Square`, `/Circle`, `/Line`,
    `/Ink`, `/FreeText`, `/Highlight`) *with generated appearance streams*, so
    they look the same in Acrobat, Preview, Chrome, forScore, and MobileSheets
  - **Flattened mode**: drawn into the page content. They can't be edited later, but they display everywhere
- ☐ Save and re-open fidelity: a sidecar or embedded project data, so markup stays editable
  in *our* app after export (❓ see decisions)
- ☐ Unit tests for the model and transform invariants, plus export round-trips (re-open with pikepdf/Poppler and check the result)

## Phase 2: Page editor + Preview-parity tools
- ☐ **Page editor view**: double-click a thumbnail to open one page large, with a markup
  toolbar, zoom, and next/previous page (seeded from `DrawingAreaWidget`)
- ☐ Select / move / resize / delete, multi-select, z-order, and nudging with the arrow keys
- ☐ Shapes: rectangle, rounded rectangle, ellipse, line, arrow, with stroke and fill color
  pickers, line width, and opacity
- ☐ Text boxes: font, size, color, alignment, background fill; edit in place
- ☐ Freehand ink (pen and tablet) with smoothing
- ☐ Highlight / underline / strikeout on text-bearing PDFs (Poppler gives text geometry)
- ☐ **Interactive crop**: drag a rectangle on the page and apply it to this page or all selected pages
  (upstream only has the numeric crop dialog)
- ☐ Signature / reusable stamp library (images or saved ink)
- ☐ Preview extras worth matching: page thumbnails sidebar in the editor, notes (popup annotations),
  and adjusting markup color across a selection

## Phase 3: Sheet-music features (❓ prioritize)
Candidate list to pick from and reorder. Not all of these will make it.

**Preparing parts**
- ☐ **Part extractor**: define parts by page range or bookmark (e.g. *Horn 1: pp. 12–15*)
  and batch-export each one with a naming template like `{Title} - {Part}.pdf`
- ☐ **Scan cleanup**: auto-crop margins (upstream has a basic version), deskew,
  despeckle, convert to black-and-white with a threshold, and normalize every page
  to a target size (Letter, 9×12, B4) with centered content
- ☐ **Imposition**: 2-up on tabloid, booklet (saddle-stitch page order), and
  "foldout" spreads for awkward page turns

**Marking up music**
- ☐ **Music symbol stamps**: a palette of breath marks, fermatas, accidentals, dynamics,
  glasses 👓, "V.S.", cut brackets, and vi-de markers, rendered from the SMuFL *Bravura* font (OFL license)
- ☐ **Rehearsal marks and measure numbers**: boxed letters or numbers at a click,
  auto-incrementing
- ☐ **Header stamping**: part name, page *n* of *m*, and "Property of River City Brass"
  library stamp, applied in batches across a set of parts

**Library and performance**
- ☐ **Concert folder builder**: assemble pieces in program order, with an auto-generated
  cover page, a table of contents, and bookmarks
- ☐ **Page-turn planner**: show the spread layout, flag bad turns, and insert blank pages or
  foldouts
- ☐ **Music metadata**: composer, arranger, publisher, instrument, catalog number,
  written to XMP, with batch rename/export driven by those fields
- ☐ **Excerpt builder**: clip regions from several pages and assemble them into an excerpt
  packet (for auditions or sectionals)
- ☐ **Tablet/app export presets**: flattened output plus bookmarks, tuned for
  forScore, MobileSheets, and similar apps

## Phase 4: Stress testing & release hardening
- ☐ Large-file performance: 300+ page scanned scores, 1 GB inputs, memory ceiling,
  and thumbnail render times
- ☐ Many annotations per page, and undo-stack depth and memory use
- ☐ Interop matrix: open exports in Acrobat Reader, Preview, Chrome, Firefox, Edge,
  forScore, and MobileSheets, and compare what each one shows
- ☐ Malformed/encrypted/odd PDFs (fuzzing corpus), and PDFs with existing annotations or forms
- ☐ Property-based tests (Hypothesis) for the geometry invariants: rotate ×4 = identity,
  crop then split preserves the visible area, and so on
- ☐ GUI tests (dogtail) extended for new tools, running in Linux CI
- ☐ Windows installer build from the fork; rebrand (app ID, name, icon, config dir)

---

## Open decisions ❓
1. **Name and branding** for the fork. This only matters before the first release.
2. **Editable vs. flattened as the default export**, and how markup stays editable
   in our app after saving (embedded private data vs. re-reading the PDF annotations we wrote).
3. **Existing annotations in imported PDFs**: preserve them as-is (upstream behavior),
   or import them as editable markup?
4. **Phase 3 priorities**: which three sheet-music features come first?
5. **Upstream contributions**: offer generally useful pieces (interactive crop,
   page editor) back to PDF Arranger, or keep the fork independent?
