# Copyright (C) 2026 pdfarranger contributors
#
# pdfarranger is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""A single page drawn large, with fit modes, zoom and background rendering.

See docs/design/page-view.md. The module has three layers:

* Pure functions for the fit/zoom/scroll math and the cache key.
* ``PageCache`` and ``PageRenderer`` (a worker thread around
  ``pagerender.render_page``). Neither needs a display.
* ``PageView``, the GTK widget that ties them together.

Zoom here means the same thing as in ``pagerender``: logical pixels per PDF
point, before ``Page.scale`` is applied. The widget renders at
``zoom * scale_factor`` so pages stay sharp with display scaling.

Like thumbnails, rendered surfaces are in *unrotated* page orientation
(``pagerender.pixel_size``); the page's rotation is applied when painting, the
same way ``iconview.CellRendererImage`` does it. ``display_size`` is the size
on screen, after rotation.

Run ``python -m pdfarranger.pageview file.pdf`` for a bare demo window.
"""

__all__ = [
    "FIT_PAGE", "FIT_WIDTH", "FREE", "MIN_ZOOM", "MAX_ZOOM", "ZOOM_STEP", "MARGIN",
    "page_points", "display_size", "fit_zoom", "clamp_zoom", "step_zoom", "canvas_size", "page_origin",
    "anchor_scroll", "content_key", "render_key", "PageCache", "PageRenderer", "PageView",
]

import collections
import threading
import traceback
from math import pi

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, GObject, Gdk, Gtk  # noqa: E402

import cairo  # noqa: E402

from . import pagerender  # noqa: E402

FIT_PAGE = "fit-page"
FIT_WIDTH = "fit-width"
FREE = "free"
FIT_MODES = (FIT_PAGE, FIT_WIDTH, FREE)

MIN_ZOOM = 0.05
MAX_ZOOM = 8.0
ZOOM_STEP = 1.2
MARGIN = 12
"""Logical pixels kept free around the page."""

RENDER_DELAY_MS = 150
"""Wait this long after a zoom or resize before rendering the new size."""

BACKGROUND = (0.42, 0.42, 0.42)


# --- Geometry ---------------------------------------------------------------

def page_points(p):
    """Displayed width and height of ``p`` in PDF points (rotation, scale and crop applied)."""
    s = p.size_in_points()
    return float(s.width), float(s.height)


def _rotation(angle):
    return round((int(angle) % 360) / 90) * 90


def display_size(p, zoom):
    """On-screen width and height in pixels of ``p`` at ``zoom``, after rotation.

    This is ``pagerender.pixel_size`` (the unrotated surface size) with the axes
    swapped for 90° and 270°.
    """
    w, h = pagerender.pixel_size(p, zoom)
    return (h, w) if _rotation(p.angle) in (90, 270) else (w, h)


def clamp_zoom(zoom):
    return min(MAX_ZOOM, max(MIN_ZOOM, zoom))


def step_zoom(zoom, steps):
    """Zoom in (``steps`` > 0) or out (``steps`` < 0) by ``ZOOM_STEP`` per step."""
    return clamp_zoom(zoom * ZOOM_STEP ** steps)


def fit_zoom(page_w, page_h, avail_w, avail_h, mode, margin=MARGIN):
    """Zoom that fits a ``page_w`` × ``page_h`` point page into the available pixels."""
    if mode not in (FIT_PAGE, FIT_WIDTH):
        raise ValueError(f"not a fit mode: {mode!r}")
    w = max(1, avail_w - 2 * margin)
    h = max(1, avail_h - 2 * margin)
    zoom = w / page_w
    if mode == FIT_PAGE:
        zoom = min(zoom, h / page_h)
    return clamp_zoom(zoom)


def canvas_size(page_px, view_px, margin=MARGIN):
    """Size of the scrollable canvas: the view, or the page plus margins if larger."""
    return tuple(max(v, p + 2 * margin) for p, v in zip(page_px, view_px))


def page_origin(page_px, canvas_px):
    """Top-left of the page on the canvas: centred, in whole pixels."""
    return tuple((c - p) // 2 for p, c in zip(page_px, canvas_px))


def anchor_scroll(scroll, pointer, old_origin, old_zoom, new_origin, new_zoom):
    """New scroll offset (one axis) that keeps the page point under ``pointer`` fixed.

    ``scroll`` is the current scroll offset, ``pointer`` the pointer position
    relative to the visible area, and the origins are the page's top-left on the
    canvas before and after the zoom.
    """
    u = (scroll + pointer - old_origin) / old_zoom
    return new_origin + u * new_zoom - pointer


def content_key(p):
    """Everything about ``p`` that changes its pixels, except the zoom."""
    layers = tuple(
        (lp.nfile, lp.npage, lp.angle, lp.scale, tuple(lp.crop), tuple(lp.offset), lp.laypos)
        for lp in p.layerpages
    )
    return (p.nfile, p.npage, p.angle, p.scale, tuple(p.crop), tuple(p.hide), layers)


def render_key(p, device_zoom):
    return content_key(p), round(device_zoom, 6)


def _aspect_matches(surface, p, tolerance=0.03):
    """True if ``surface`` has the unrotated aspect ratio of ``p`` (guards against stale thumbnails)."""
    if surface is None:
        return False
    w, h = pagerender.pixel_size(p, 100)
    sw, sh = surface.get_width(), surface.get_height()
    if min(w, h, sw, sh) <= 0:
        return False
    return abs((sw / sh) / (w / h) - 1) <= tolerance


# --- Cache ------------------------------------------------------------------

class PageCache:
    """Thread-safe LRU of rendered surfaces keyed by ``render_key``.

    Bounded by entry count and by bytes; the newest entry is always kept.
    """

    def __init__(self, max_entries=3, max_bytes=256 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._items = collections.OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def _size(surface):
        return surface.get_stride() * surface.get_height()

    def __len__(self):
        with self._lock:
            return len(self._items)

    def __contains__(self, key):
        with self._lock:
            return key in self._items

    @property
    def nbytes(self):
        with self._lock:
            return self._bytes

    def get(self, key):
        with self._lock:
            surface = self._items.get(key)
            if surface is not None:
                self._items.move_to_end(key)
            return surface

    def put(self, key, surface):
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._bytes -= self._size(old)
            self._items[key] = surface
            self._bytes += self._size(surface)
            while len(self._items) > 1 and (
                len(self._items) > self.max_entries or self._bytes > self.max_bytes
            ):
                _, evicted = self._items.popitem(last=False)
                self._bytes -= self._size(evicted)

    def find_content(self, ckey):
        """Most recently used ``(device_zoom, surface)`` for ``content_key`` ``ckey``, or None."""
        with self._lock:
            for (c, zoom), surface in reversed(self._items.items()):
                if c == ckey:
                    return zoom, surface
        return None

    def clear(self):
        with self._lock:
            self._items.clear()
            self._bytes = 0


# --- Background renderer ----------------------------------------------------

class _Job:
    __slots__ = ("key", "page", "zoom", "cancelled")

    def __init__(self, key, page, zoom):
        self.key = key
        self.page = page
        self.zoom = zoom
        self.cancelled = False


class PageRenderer:
    """Renders pages on one worker thread and hands the results to ``on_done(key, surface)``.

    ``request()`` replaces the pending queue. A job that is running when a
    request without its key arrives is cancelled and its result dropped.
    Results are delivered through ``schedule`` (``GLib.idle_add`` by default, so
    ``on_done`` runs in the GTK main loop). Pages given to ``request()`` must not
    be modified afterwards; pass duplicates.
    """

    def __init__(self, pdfqueue, on_done, schedule=None):
        self.pdfqueue = pdfqueue
        self.on_done = on_done
        self.schedule = schedule or (lambda f, *a: GLib.idle_add(f, *a, priority=GLib.PRIORITY_LOW))
        self._cond = threading.Condition()
        self._jobs = []
        self._current = None
        self._quit = False
        self._thread = None

    def request(self, jobs):
        """Render ``jobs`` (``(key, page, device_zoom)`` tuples) in order, dropping older requests."""
        with self._cond:
            if self._quit:
                return
            new = [_Job(*j) for j in jobs]
            cur = self._current
            if cur is not None and not cur.cancelled:
                if any(j.key == cur.key for j in new):
                    new = [j for j in new if j.key != cur.key]
                else:
                    cur.cancelled = True
            self._jobs = new
            if self._thread is None and new:
                self._thread = threading.Thread(target=self._run, name="PageRenderer", daemon=True)
                self._thread.start()
            self._cond.notify_all()

    def cancel(self):
        self.request([])

    def stop(self, join=True, timeout=5):
        with self._cond:
            self._quit = True
            self._jobs = []
            if self._current is not None:
                self._current.cancelled = True
            self._cond.notify_all()
            thread = self._thread
        if join and thread is not None:
            thread.join(timeout)

    def is_idle(self):
        with self._cond:
            return not self._jobs and self._current is None

    def wait_idle(self, timeout=10):
        """Block until no job is pending or running. Returns False on timeout."""
        with self._cond:
            return self._cond.wait_for(lambda: not self._jobs and self._current is None, timeout)

    def _run(self):
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._jobs or self._quit)
                if self._quit:
                    self._current = None
                    self._cond.notify_all()
                    return
                job = self._current = self._jobs.pop(0)
            surface = None
            try:
                surface = pagerender.render_page(
                    job.page, self.pdfqueue, job.zoom, lambda: job.cancelled or self._quit)
            except Exception:  # A bad page must not kill the thread
                traceback.print_exc()
            with self._cond:
                ok = surface is not None and not job.cancelled and not self._quit
                if ok:
                    self.schedule(self._deliver, job.key, surface)
                self._current = None
                self._cond.notify_all()

    def _deliver(self, key, surface):
        if not self._quit:
            self.on_done(key, surface)
        return False


# --- Widget -----------------------------------------------------------------

class PageView(Gtk.ScrolledWindow):
    """Shows one ``Page`` large. See the module docstring.

    Overlays added with ``add_overlay(func)`` are called as
    ``func(cr, page, zoom)`` after the page is drawn. ``cr`` is in the same space
    as the rendered surface: unrotated, cropped page orientation, the rectangle
    (0, 0) – ``pagerender.pixel_size(page, zoom)`` in logical pixels. Overlays
    stored in normalized unrotated coordinates therefore rotate with the page.
    """

    __gsignals__ = {
        "zoom-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
    }

    def __init__(self, pdfqueue, cache=None):
        super().__init__()
        self.pdfqueue = pdfqueue
        self.cache = cache if cache is not None else PageCache()
        self.renderer = PageRenderer(pdfqueue, self._on_rendered)
        self.page = None
        self.neighbours = []
        self.fit_mode = FIT_PAGE
        self.zoom = 1.0
        self.margin = MARGIN
        self._overlays = []
        self._view_size = (1, 1)
        self._canvas = (1, 1)
        self._page_px = (1, 1)
        self._surface_px = (1, 1)
        self._origin = (0, 0)
        self._pending_scroll = None
        self._render_source = None

        self.da = Gtk.DrawingArea()
        self.da.connect("draw", self._on_draw)
        self.da.connect("size-allocate", self._on_da_size_allocate)
        self.add(self.da)
        viewport = self.get_child()
        if isinstance(viewport, Gtk.Viewport):
            viewport.set_shadow_type(Gtk.ShadowType.NONE)
        self.connect("size-allocate", self._on_size_allocate)
        self.connect("scroll-event", self._on_scroll_event)
        self.connect("destroy", self._on_destroy)

    # Public API

    def set_page(self, page, neighbours=(), keep_scroll=False):
        """Show ``page`` (or nothing if None) and prefetch ``neighbours``. Both are copied."""
        self.page = page.duplicate() if page is not None else None
        self.neighbours = [n.duplicate(incl_thumbnail=False) for n in neighbours if n is not None]
        if not keep_scroll:
            self._pending_scroll = (0, 0)
        self._relayout()
        self._schedule_render(0)

    def set_fit_mode(self, mode):
        if mode not in FIT_MODES:
            raise ValueError(f"unknown fit mode: {mode!r}")
        self.fit_mode = mode
        self._relayout(anchor=self._center_anchor())
        self._schedule_render()

    def set_zoom(self, zoom, anchor=None):
        """Set a free zoom. ``anchor`` is a pointer position in the visible area (default: centre)."""
        self.fit_mode = FREE
        self.zoom = clamp_zoom(zoom)
        self._relayout(anchor=anchor if anchor is not None else self._center_anchor())
        self._schedule_render()

    def zoom_in(self, steps=1, anchor=None):
        self.set_zoom(step_zoom(self.zoom, steps), anchor)

    def zoom_out(self, steps=1, anchor=None):
        self.set_zoom(step_zoom(self.zoom, -steps), anchor)

    def add_overlay(self, func):
        self._overlays.append(func)
        self.da.queue_draw()

    def remove_overlay(self, func):
        self._overlays.remove(func)
        self.da.queue_draw()

    def device_zoom(self, zoom=None):
        return (self.zoom if zoom is None else zoom) * self.get_scale_factor()

    # Layout

    def _available(self):
        w, h = self._view_size
        if self.fit_mode == FIT_WIDTH and not self.get_overlay_scrolling():
            w -= self.get_vscrollbar().get_preferred_width()[1]
        return w, h

    def _zoom_for(self, p):
        if self.fit_mode == FREE:
            return self.zoom
        return fit_zoom(*page_points(p), *self._available(), self.fit_mode, self.margin)

    def _center_anchor(self):
        return self._view_size[0] / 2, self._view_size[1] / 2

    def _relayout(self, anchor=None):
        old_zoom, old_origin = self.zoom, self._origin
        scroll = (self.get_hadjustment().get_value(), self.get_vadjustment().get_value())
        if self.page is None:
            self._page_px = self._surface_px = (0, 0)
            self._canvas = self._view_size
            self._origin = (0, 0)
        else:
            self.zoom = self._zoom_for(self.page)
            self._surface_px = pagerender.pixel_size(self.page, self.zoom)
            self._page_px = display_size(self.page, self.zoom)
            self._canvas = canvas_size(self._page_px, self._view_size, self.margin)
            self._origin = page_origin(self._page_px, self._canvas)
            if anchor is not None and self._pending_scroll is None and old_zoom != self.zoom:
                self._pending_scroll = tuple(
                    anchor_scroll(scroll[i], anchor[i], old_origin[i], old_zoom,
                                  self._origin[i], self.zoom)
                    for i in range(2))
        if tuple(self.da.get_size_request()) != tuple(self._canvas):
            self.da.set_size_request(*self._canvas)
        else:
            self._apply_pending_scroll()
        self.da.queue_draw()
        if self.zoom != old_zoom:
            self.emit("zoom-changed", self.zoom)

    def _on_size_allocate(self, _widget, alloc):
        size = (alloc.width, alloc.height)
        if size != self._view_size:
            self._view_size = size
            GLib.idle_add(self._idle_relayout)

    def _idle_relayout(self):
        anchor = None if self.fit_mode != FREE else self._center_anchor()
        self._relayout(anchor)
        self._schedule_render()
        return False

    def _on_da_size_allocate(self, _widget, _alloc):
        if self._pending_scroll is not None:
            GLib.idle_add(self._apply_pending_scroll)

    def _apply_pending_scroll(self):
        if self._pending_scroll is None:
            return False
        x, y = self._pending_scroll
        self._pending_scroll = None
        for adj, v in ((self.get_hadjustment(), x), (self.get_vadjustment(), y)):
            adj.set_value(min(max(adj.get_lower(), v), adj.get_upper() - adj.get_page_size()))
        return False

    # Input

    def _on_scroll_event(self, _widget, event):
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        if event.direction == Gdk.ScrollDirection.UP:
            steps = 1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            steps = -1
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            _, _, dy = event.get_scroll_deltas()
            steps = -dy
            if steps == 0:
                return True
        else:
            return False
        anchor = None
        if Gtk.get_event_widget(event) is self.da:
            # event.x/y are canvas coordinates; convert to the visible area
            anchor = (event.x - self.get_hadjustment().get_value(),
                      event.y - self.get_vadjustment().get_value())
        self.zoom_in(steps, anchor)
        return True

    # Rendering

    def _schedule_render(self, delay=RENDER_DELAY_MS):
        if self._render_source is not None:
            GLib.source_remove(self._render_source)
            self._render_source = None
        if delay <= 0:
            self._start_render()
        else:
            self._render_source = GLib.timeout_add(delay, self._start_render)

    def _start_render(self):
        self._render_source = None
        jobs = []
        if self.page is not None:
            for p in [self.page] + self.neighbours:
                zoom = self.device_zoom(self._zoom_for(p))
                key = render_key(p, zoom)
                if key not in self.cache:
                    jobs.append((key, p, zoom))
        self.renderer.request(jobs)
        return False

    def _on_rendered(self, key, surface):
        self.cache.put(key, surface)
        if self.page is not None and key == render_key(self.page, self.device_zoom()):
            self.da.queue_draw()

    def best_surface(self):
        """Best image for the current page: ``(surface, exact)`` or ``(None, False)``.

        In order: the render at the current zoom, an older render of the same
        page at another zoom, then the page's thumbnail or preview.
        """
        p = self.page
        if p is None:
            return None, False
        surface = self.cache.get(render_key(p, self.device_zoom()))
        if surface is not None:
            return surface, True
        found = self.cache.find_content(content_key(p))
        if found is not None:
            return found[1], False
        for s in (p.thumbnail, p.preview):
            if _aspect_matches(s, p):
                return s, False
        return None, False

    def _on_draw(self, _da, cr):
        cr.set_source_rgb(*BACKGROUND)
        cr.paint()
        if self.page is None:
            return
        (ox, oy), (pw, ph) = self._origin, self._page_px
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.rectangle(ox + 2, oy + 2, pw, ph)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(ox, oy, pw, ph)
        cr.fill()

        # Switch to unrotated surface space, like CellRendererImage.
        sw, sh = self._surface_px
        cr.save()
        cr.translate(ox + pw / 2, oy + ph / 2)
        cr.rotate(_rotation(self.page.angle) * pi / 180)
        cr.translate(-sw / 2, -sh / 2)

        surface, exact = self.best_surface()
        if surface is not None:
            cr.save()
            cr.rectangle(0, 0, sw, sh)
            cr.clip()
            cr.scale(sw / surface.get_width(), sh / surface.get_height())
            cr.set_source_surface(surface, 0, 0)
            if not exact:
                cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
            cr.restore()

        for func in self._overlays:
            cr.save()
            try:
                func(cr, self.page, self.zoom)
            except Exception:
                traceback.print_exc()
            cr.restore()
        cr.restore()

    def _on_destroy(self, _widget):
        if self._render_source is not None:
            GLib.source_remove(self._render_source)
            self._render_source = None
        self.renderer.stop(join=False)


# --- Demo -------------------------------------------------------------------

def _demo(argv):  # pragma: no cover - manual check only
    import os
    import tempfile
    from . import core

    if len(argv) < 2:
        print("usage: python -m pdfarranger.pageview FILE.pdf")
        return 2
    filename = os.path.abspath(argv[1])
    tmp = tempfile.mkdtemp(prefix="pdfarranger-pageview-")
    pdfqueue = [core.PDFDoc(filename, None, None, os.stat(filename), tmp, None)]
    doc = pdfqueue[0]
    pages = []
    for n in range(doc.document.get_n_pages()):
        size = core.Dims(*doc.document.get_page(n).get_size())
        p = core.Page(1, n + 1, 0.2, doc.copyname, 0, 1.0, core.Sides(), core.Sides(), size,
                      f"page {n + 1}", [])
        p.thumbnail = pagerender.render_page(p, pdfqueue, 0.2)
        pages.append(p)

    win = Gtk.Window(title=f"PageView – {os.path.basename(filename)}")
    win.set_default_size(800, 1000)
    view = PageView(pdfqueue)
    win.add(view)
    state = {"i": 0}

    def show(i):
        state["i"] = i = max(0, min(len(pages) - 1, i))
        view.set_page(pages[i], [pages[j] for j in (i - 1, i + 1) if 0 <= j < len(pages)])
        win.set_title(f"PageView – page {i + 1}/{len(pages)} – zoom {view.zoom:.2f}")

    def on_key(_w, event):
        k = event.keyval
        if k in (Gdk.KEY_Page_Down, Gdk.KEY_Right):
            show(state["i"] + 1)
        elif k in (Gdk.KEY_Page_Up, Gdk.KEY_Left):
            show(state["i"] - 1)
        elif k == Gdk.KEY_1:
            view.set_fit_mode(FIT_PAGE)
        elif k == Gdk.KEY_2:
            view.set_fit_mode(FIT_WIDTH)
        elif k == Gdk.KEY_0:
            view.set_zoom(1.0)
        elif k in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            view.zoom_in()
        elif k in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            view.zoom_out()
        else:
            return False
        return True

    view.connect("zoom-changed", lambda _v, z: win.set_title(
        f"PageView – page {state['i'] + 1}/{len(pages)} – zoom {z:.2f}"))
    win.connect("key-press-event", on_key)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    show(0)
    Gtk.main()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_demo(sys.argv))
