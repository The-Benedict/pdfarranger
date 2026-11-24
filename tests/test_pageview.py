"""Tests for pdfarranger.pageview. Everything except the widget smoke test runs without a display."""

import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

import cairo

import pdfarranger.core as core
from pdfarranger import pagerender, pageview as pv

HERE = os.path.dirname(os.path.abspath(__file__))
LETTER = core.Dims(612.0, 792.0)


def _page(npage=1, angle=0, scale=1.0, crop=(0, 0, 0, 0), hide=(0, 0, 0, 0), size=LETTER, layers=()):
    p = core.Page(1, npage, 0.25, 'copy', 0, scale, core.Sides(*crop), core.Sides(*hide),
                  size, 'test', list(layers))
    p.rotate(angle)
    return p


def _layer(laypos='OVERLAY', npage=2):
    return core.LayerPage(1, npage, 'copy', 0, 0.5, core.Sides(), core.Sides(0.1, 0.4, 0.1, 0.4),
                          laypos, LETTER)


def _pixels(surface):
    surface.flush()
    return surface.get_width(), surface.get_height(), bytes(surface.get_data())


def _surface(w=10, h=10):
    return cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)


class GeometryTest(unittest.TestCase):
    def test_page_points(self):
        self.assertEqual(pv.page_points(_page()), (612, 792))
        self.assertEqual(pv.page_points(_page(angle=90)), (792, 612))
        self.assertEqual(pv.page_points(_page(scale=2)), (1224, 1584))
        w, h = pv.page_points(_page(crop=(0.1, 0.1, 0.25, 0.25)))
        self.assertAlmostEqual(w, 612 * 0.8)
        self.assertAlmostEqual(h, 792 * 0.5)

    def test_fit_page_portrait_limited_by_height(self):
        z = pv.fit_zoom(612, 792, 1000, 800, pv.FIT_PAGE, margin=0)
        self.assertAlmostEqual(z, 800 / 792)

    def test_fit_page_landscape_limited_by_width(self):
        z = pv.fit_zoom(792, 612, 600, 1000, pv.FIT_PAGE, margin=0)
        self.assertAlmostEqual(z, 600 / 792)

    def test_fit_width_ignores_height(self):
        z = pv.fit_zoom(612, 792, 1000, 300, pv.FIT_WIDTH, margin=0)
        self.assertAlmostEqual(z, 1000 / 612)

    def test_fit_respects_margin(self):
        z = pv.fit_zoom(100, 100, 224, 1000, pv.FIT_PAGE, margin=12)
        self.assertAlmostEqual(z, 2.0)

    def test_fit_result_fits_in_pixels(self):
        """Every fitted page, rotated, cropped or scaled, fits inside the view with margins."""
        pages = [_page(), _page(angle=90), _page(angle=270, crop=(0.1, 0.2, 0.05, 0.3)),
                 _page(scale=1.7), _page(size=core.Dims(2000, 300))]
        for p in pages:
            for view in [(800, 600), (1920, 1080), (333, 977)]:
                with self.subTest(p=p, view=view):
                    z = pv.fit_zoom(*pv.page_points(p), *view, pv.FIT_PAGE)
                    w, h = pv.display_size(p, z)
                    self.assertLessEqual(w, view[0] - 2 * pv.MARGIN + 1)
                    self.assertLessEqual(h, view[1] - 2 * pv.MARGIN + 1)
                    self.assertTrue(abs(w - (view[0] - 2 * pv.MARGIN)) <= 1
                                    or abs(h - (view[1] - 2 * pv.MARGIN)) <= 1)

    def test_display_size_rotates_surface_size(self):
        crop = (0.1, 0.2, 0.05, 0.3)
        w, h = pagerender.pixel_size(_page(crop=crop), 0.5)
        self.assertEqual(pv.display_size(_page(crop=crop), 0.5), (w, h))
        for angle in (90, 180, 270):
            with self.subTest(angle):
                q = _page(crop=crop, angle=angle)
                self.assertEqual(pagerender.pixel_size(q, 0.5), (w, h), "surface stays unrotated")
                expected = (h, w) if angle in (90, 270) else (w, h)
                self.assertEqual(pv.display_size(q, 0.5), expected)
                pw, ph = pv.page_points(q)
                self.assertAlmostEqual(expected[0], pw * 0.5, delta=1)
                self.assertAlmostEqual(expected[1], ph * 0.5, delta=1)

    def test_fit_tiny_view_is_clamped(self):
        self.assertEqual(pv.fit_zoom(612, 792, 0, 0, pv.FIT_PAGE), pv.MIN_ZOOM)
        self.assertEqual(pv.fit_zoom(1, 1, 100000, 100000, pv.FIT_PAGE), pv.MAX_ZOOM)

    def test_fit_rejects_free(self):
        with self.assertRaises(ValueError):
            pv.fit_zoom(1, 1, 1, 1, pv.FREE)

    def test_clamp_and_step(self):
        self.assertEqual(pv.clamp_zoom(100), pv.MAX_ZOOM)
        self.assertEqual(pv.clamp_zoom(0), pv.MIN_ZOOM)
        self.assertAlmostEqual(pv.step_zoom(1.0, 1), pv.ZOOM_STEP)
        self.assertAlmostEqual(pv.step_zoom(1.0, -2), pv.ZOOM_STEP ** -2)
        self.assertAlmostEqual(pv.step_zoom(pv.step_zoom(1.3, 3), -3), 1.3)
        self.assertEqual(pv.step_zoom(pv.MAX_ZOOM, 1), pv.MAX_ZOOM)

    def test_canvas_and_origin_centre_small_page(self):
        canvas = pv.canvas_size((400, 500), (1000, 800), margin=12)
        self.assertEqual(canvas, (1000, 800))
        self.assertEqual(pv.page_origin((400, 500), canvas), (300, 150))

    def test_canvas_grows_for_large_page(self):
        canvas = pv.canvas_size((2000, 500), (1000, 800), margin=12)
        self.assertEqual(canvas, (2024, 800))
        self.assertEqual(pv.page_origin((2000, 500), canvas), (12, 150))

    def test_anchor_scroll_keeps_point_fixed(self):
        old_zoom, new_zoom = 1.0, 2.0
        page = (612, 792)
        view = (800, 600)
        old_px = tuple(int(v * old_zoom) for v in page)
        new_px = tuple(int(v * new_zoom) for v in page)
        old_origin = pv.page_origin(old_px, pv.canvas_size(old_px, view))
        new_origin = pv.page_origin(new_px, pv.canvas_size(new_px, view))
        scroll, pointer = (0, 100), (250, 120)
        for i in range(2):
            new_scroll = pv.anchor_scroll(scroll[i], pointer[i], old_origin[i], old_zoom,
                                          new_origin[i], new_zoom)
            before = (scroll[i] + pointer[i] - old_origin[i]) / old_zoom
            after = (new_scroll + pointer[i] - new_origin[i]) / new_zoom
            self.assertAlmostEqual(before, after)

    def test_anchor_scroll_identity(self):
        self.assertEqual(pv.anchor_scroll(40, 10, 12, 1.5, 12, 1.5), 40)


class KeyTest(unittest.TestCase):
    def test_key_changes_with_pixels(self):
        base = pv.render_key(_page(), 1.0)
        variants = {
            'rotate': lambda p: p.rotate(90),
            'crop': lambda p: setattr(p, 'crop', core.Sides(0.1, 0, 0, 0)),
            'hide': lambda p: setattr(p, 'hide', core.Sides(0, 0.1, 0, 0)),
            'scale': lambda p: setattr(p, 'scale', 1.5),
            'npage': lambda p: setattr(p, 'npage', 2),
            'layer': lambda p: p.layerpages.append(_layer()),
        }
        for name, change in variants.items():
            with self.subTest(name):
                p = _page()
                change(p)
                self.assertNotEqual(pv.render_key(p, 1.0), base)
        self.assertNotEqual(pv.render_key(_page(), 1.5), base)

    def test_layer_edit_changes_key(self):
        p = _page(layers=[_layer()])
        k = pv.render_key(p, 1)
        p.layerpages[0].offset = core.Sides(0.2, 0.3, 0.1, 0.4)
        self.assertNotEqual(pv.render_key(p, 1), k)

    def test_key_stable_for_non_pixel_changes(self):
        p = _page(layers=[_layer()])
        k = pv.render_key(p, 1.25)
        d = p.duplicate()
        d.thumbnail = _surface()
        d.description = 'other'
        d.zoom = 3
        self.assertEqual(pv.render_key(d, 1.25), k)
        self.assertEqual(pv.render_key(p.duplicate(incl_thumbnail=False), 1.25), k)

    def test_rotate_four_times_restores_key(self):
        p = _page(crop=(0.1, 0.2, 0.05, 0.3))
        k = pv.render_key(p, 1)
        for _ in range(4):
            p.rotate(90)
        self.assertEqual(pv.render_key(p, 1), k)

    def test_aspect_guard(self):
        p = _page()
        self.assertTrue(pv._aspect_matches(_surface(612, 792), p))
        self.assertFalse(pv._aspect_matches(_surface(792, 612), p))
        # Thumbnails are unrotated, so a rotated page still matches a portrait thumbnail.
        self.assertTrue(pv._aspect_matches(_surface(612, 792), _page(angle=90)))
        self.assertFalse(pv._aspect_matches(None, p))


class CacheTest(unittest.TestCase):
    def test_lru_eviction(self):
        c = pv.PageCache(max_entries=3)
        for k in 'abc':
            c.put(k, _surface())
        c.get('a')
        c.put('d', _surface())
        self.assertNotIn('b', c)
        for k in 'acd':
            self.assertIn(k, c)
        self.assertEqual(len(c), 3)

    def test_byte_limit_keeps_newest(self):
        one = _surface(100, 100)
        size = one.get_stride() * one.get_height()
        c = pv.PageCache(max_entries=10, max_bytes=int(size * 2.5))
        for k in 'abc':
            c.put(k, _surface(100, 100))
        self.assertEqual(len(c), 2)
        self.assertEqual(c.nbytes, 2 * size)
        c.put('huge', _surface(1000, 1000))
        self.assertEqual(len(c), 1)
        self.assertIn('huge', c)

    def test_replace_does_not_leak_bytes(self):
        c = pv.PageCache()
        c.put('a', _surface(50, 50))
        c.put('a', _surface(10, 10))
        self.assertEqual(c.nbytes, _surface(10, 10).get_stride() * 10)

    def test_find_content_prefers_recent(self):
        c = pv.PageCache(max_entries=5)
        p, q = _page(), _page(npage=2)
        s1, s2, s3 = _surface(), _surface(), _surface()
        c.put(pv.render_key(p, 1.0), s1)
        c.put(pv.render_key(p, 2.0), s2)
        c.put(pv.render_key(q, 1.0), s3)
        self.assertEqual(c.find_content(pv.content_key(p)), (2.0, s2))
        c.get(pv.render_key(p, 1.0))
        self.assertEqual(c.find_content(pv.content_key(p)), (1.0, s1))
        self.assertIsNone(c.find_content(pv.content_key(_page(npage=3))))

    def test_clear(self):
        c = pv.PageCache()
        c.put('a', _surface())
        c.clear()
        self.assertEqual((len(c), c.nbytes), (0, 0))

    def test_concurrent_access(self):
        c = pv.PageCache(max_entries=4)
        errors = []

        def work(n):
            try:
                for i in range(300):
                    c.put((n, i % 7), _surface(4, 4))
                    c.get((n, (i + 3) % 7))
                    c.find_content(n)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(c), 4)
        self.assertEqual(c.nbytes, 4 * _surface(4, 4).get_stride() * 4)


class RendererTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='pdfarranger-test-')
        src = os.path.join(HERE, 'test.pdf')
        cls.pdfqueue = [core.PDFDoc(src, None, None, os.stat(src), cls.tmp, None)]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.results = []
        self.lock = threading.Lock()
        self.renderer = pv.PageRenderer(self.pdfqueue, self._done, schedule=lambda f, *a: f(*a))

    def tearDown(self):
        self.renderer.stop()

    def _done(self, key, surface):
        with self.lock:
            self.results.append((key, surface))

    def test_matches_render_page(self):
        cases = [_page(), _page(npage=2, angle=90), _page(crop=(0.1, 0.05, 0.2, 0.15), angle=270),
                 _page(hide=(0.2, 0.1, 0.15, 0.3), scale=1.3), _page(layers=[_layer()], angle=180)]
        jobs = [(pv.render_key(p, 0.3), p, 0.3) for p in cases]
        self.renderer.request(jobs)
        self.assertTrue(self.renderer.wait_idle())
        self.assertEqual([k for k, _ in self.results], [k for k, _, _ in jobs])
        for (_, surface), p in zip(self.results, cases):
            self.assertEqual(_pixels(surface), _pixels(pagerender.render_page(p, self.pdfqueue, 0.3)))

    def test_order_current_first(self):
        cur, prev, nxt = _page(npage=2), _page(npage=1), _page(npage=1, angle=90)
        jobs = [(pv.render_key(p, 0.2), p, 0.2) for p in (cur, prev, nxt)]
        self.renderer.request(jobs)
        self.assertTrue(self.renderer.wait_idle())
        self.assertEqual([k for k, _ in self.results], [j[0] for j in jobs])

    def test_newer_request_replaces_pending(self):
        gate = threading.Event()
        started = threading.Event()
        real = pagerender.render_page

        def slow(p, q, zoom, cancelled=None):
            started.set()
            gate.wait(5)
            return real(p, q, zoom, cancelled)

        pages = [_page(npage=1), _page(npage=2), _page(npage=1, angle=90)]
        keys = [pv.render_key(p, 0.2) for p in pages]
        with mock.patch.object(pagerender, 'render_page', side_effect=slow):
            self.renderer.request([(keys[0], pages[0], 0.2), (keys[1], pages[1], 0.2)])
            self.assertTrue(started.wait(5))
            # Job 0 is running; replace the queue with job 2 only.
            self.renderer.request([(keys[2], pages[2], 0.2)])
            gate.set()
            self.assertTrue(self.renderer.wait_idle())
        got = [k for k, _ in self.results]
        self.assertEqual(got, [keys[2]], "cancelled job 0 and dropped job 1")

    def test_running_job_kept_if_requested_again(self):
        gate = threading.Event()
        started = threading.Event()
        calls = []
        real = pagerender.render_page

        def slow(p, q, zoom, cancelled=None):
            calls.append(p.npage)
            started.set()
            gate.wait(5)
            return real(p, q, zoom, cancelled)

        a, b = _page(npage=1), _page(npage=2)
        ka, kb = pv.render_key(a, 0.2), pv.render_key(b, 0.2)
        with mock.patch.object(pagerender, 'render_page', side_effect=slow):
            self.renderer.request([(ka, a, 0.2)])
            self.assertTrue(started.wait(5))
            self.renderer.request([(ka, a, 0.2), (kb, b, 0.2)])
            gate.set()
            self.assertTrue(self.renderer.wait_idle())
        self.assertEqual(calls, [1, 2], "job a was not restarted")
        self.assertEqual([k for k, _ in self.results], [ka, kb])

    def test_cancel_drops_result(self):
        started = threading.Event()
        seen_cancel = threading.Event()

        def slow(p, q, zoom, cancelled=None):
            started.set()
            for _ in range(500):
                if cancelled():
                    seen_cancel.set()
                    break
                time.sleep(0.01)
            return _surface()

        p = _page()
        with mock.patch.object(pagerender, 'render_page', side_effect=slow):
            self.renderer.request([(pv.render_key(p, 1), p, 1)])
            self.assertTrue(started.wait(5))
            self.renderer.cancel()
            self.assertTrue(seen_cancel.wait(5))
            self.assertTrue(self.renderer.wait_idle())
        self.assertEqual(self.results, [])

    def test_exception_does_not_kill_thread(self):
        p = _page()
        bad = _page()
        bad.nfile = 99  # not in pdfqueue
        with mock.patch('traceback.print_exc'):
            self.renderer.request([('bad', bad, 0.2), ('good', p, 0.2)])
            self.assertTrue(self.renderer.wait_idle())
        self.assertEqual([k for k, _ in self.results], ['good'])

    def test_stop_joins_and_ignores_requests(self):
        p = _page()
        self.renderer.request([('a', p, 0.2)])
        self.renderer.wait_idle()
        thread = self.renderer._thread
        self.renderer.stop()
        self.assertFalse(thread.is_alive())
        self.renderer.request([('b', p, 0.2)])
        self.assertEqual([k for k, _ in self.results], ['a'])

    def test_no_thread_until_needed(self):
        self.renderer.request([])
        self.assertIsNone(self.renderer._thread)
        self.assertTrue(self.renderer.is_idle())


def _have_display():
    try:
        from gi.repository import Gdk, Gtk
        return Gtk.init_check(None)[0] and Gdk.Display.get_default() is not None
    except Exception:
        return False


@unittest.skipUnless(_have_display(), "needs a display")
class WidgetSmokeTest(unittest.TestCase):
    def test_widget(self):
        from gi.repository import GLib, Gtk
        tmp = tempfile.mkdtemp(prefix='pdfarranger-test-')
        try:
            src = os.path.join(HERE, 'test.pdf')
            pdfqueue = [core.PDFDoc(src, None, None, os.stat(src), tmp, None)]
            win = Gtk.OffscreenWindow()
            win.set_default_size(400, 500)
            view = pv.PageView(pdfqueue)
            win.add(view)
            win.show_all()
            zooms = []
            view.connect('zoom-changed', lambda _v, z: zooms.append(z))
            p = _page()
            p.thumbnail = pagerender.render_page(p, pdfqueue, 0.1)
            view.set_page(p, [_page(npage=2)])

            def spin(cond, timeout=5):
                end = time.monotonic() + timeout
                while not cond() and time.monotonic() < end:
                    while Gtk.events_pending():
                        Gtk.main_iteration_do(False)
                    time.sleep(0.01)
                return cond()

            self.assertTrue(spin(lambda: view.best_surface()[1]), "exact render arrives")
            self.assertEqual(view.fit_mode, pv.FIT_PAGE)
            w, h = pv.display_size(view.page, view.zoom)
            alloc = view.get_allocation()
            self.assertLessEqual(h, alloc.height)
            self.assertLessEqual(w, alloc.width)

            view.zoom_in()
            self.assertEqual(view.fit_mode, pv.FREE)
            self.assertTrue(zooms)
            self.assertFalse(view.best_surface()[1], "placeholder until re-rendered")
            self.assertIsNotNone(view.best_surface()[0])
            self.assertTrue(spin(lambda: view.best_surface()[1]))

            calls = []
            view.add_overlay(lambda cr, page, zoom: calls.append(zoom))
            self.assertTrue(spin(lambda: calls))

            r = _page(angle=90)
            r.thumbnail = pagerender.render_page(r, pdfqueue, 0.1)
            view.set_page(r, keep_scroll=True)
            self.assertIsNotNone(view.best_surface()[0], "rotated page uses its thumbnail")
            self.assertTrue(spin(lambda: view.best_surface()[1]))

            view.set_fit_mode(pv.FIT_WIDTH)
            view.set_page(None)
            self.assertEqual(view.best_surface(), (None, False))
            win.destroy()
            GLib.idle_add(lambda: False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
