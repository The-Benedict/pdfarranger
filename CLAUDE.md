# CLAUDE.md

Fork of PDF Arranger 1.14.0 (Python, GTK 3, Poppler for rendering, pikepdf for export, GPL-3.0).
Goal: batch stamping, music symbols (Bravura/SMuFL), rehearsal marks, part extractor,
merge with bookmarks, then Preview-style markup. The plan lives in `ROADMAP.md`;
setup steps and the code map live in `DEVELOPING.md`. Read those before exploring the code.

## Owner's environment
- Windows 11, PyCharm, repo at `C:\Development\pdfarranger`
- Interpreter: MSYS2 MINGW64 Python via `.venv` (created by `dev\windows\setup.ps1`)
- Never `pip install` compiled packages; runtime dependencies come from `pacman -S mingw-w64-x86_64-python-<name>`
  and must be added to `$Packages` in `dev/windows/setup.ps1`

## Commands (run from the repo root)
- Run: `python -m pdfarranger tests/test.pdf`
- Build translations: `python setup.py build`
- Tests: `python -m unittest -v tests.test_core tests.test_exporter` and
  `python -m pytest -q tests/test_exporter_outlines.py`
- GUI tests (`tests.test`, dogtail) are Linux-only

## Rules
- Keep the Python package name `pdfarranger`. Put new features in new modules
  (e.g. `pdfarranger/stamps.py`) and keep edits to upstream files small, so `git merge upstream/main` stays easy.
- Output is **flattened**: stamps and markup are drawn into page content. Don't produce editable PDF annotations.
- Model data on `Page` must be picklable and copied in `Page.duplicate()` (undo uses snapshots),
  and must survive `rotate()`, `split()`, and `serialize()`.
- Geometry is stored normalized (0–1) in unrotated source-page space, like `Sides` crop.
- Exporter changes must cover both paths: `_copy_n_transform` and `_transform_job`.
- Every feature ships with unit tests that run without a display.
- Commit messages: imperative summary line; keep feature work on `feature/<name>` branches.
