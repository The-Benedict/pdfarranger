"""pagerender.draw_page must give the same pixels as the pre-refactor thumbnail code.

``_legacy_render`` below is a frozen copy of the compositing that lived in
``core.PDFRenderer.update()`` / ``add_layers()`` / ``render()`` before it moved to
``pagerender``. Do not "fix" it: it is the reference.

Runs without a display.
"""

import os
import shutil
import tempfile
import unittest
from math import pi
from unittest import mock

import cairo

import pdfarranger.core as core
from pdfarranger import pagerender

HERE = os.path.dirname(os.path.abspath(__file__))
LETTER = core.Dims(612.0, 792.0)


# --- Frozen reference (pdfarranger 1.14.0 core.PDFRenderer) -----------------

def _legacy_poppler(cr, p, pdfqueue):
    pdfdoc = pdfqueue[p.nfile - 1]
    page = pdfdoc.get_page(p.npage - 1)
    with pdfdoc.render_lock:
        page.render(cr)


def _legacy_add_layers(cr, p, pdfqueue, layer):
    layerpages = p.layerpages if layer == 'OVERLAY' else reversed(p.layerpages)
    for lp in layerpages:
        if layer != lp.laypos:
            continue
        cr.save()
        cr.translate(p.size.width * lp.offset.left, p.size.height * lp.offset.top)
        cr.scale(lp.scale / p.scale, lp.scale / p.scale)
        x = lp.size.width * lp.crop.left
        y = lp.size.height * lp.crop.top
        w = lp.size.width * (1 - lp.crop.left - lp.crop.right)
        h = lp.size.height * (1 - lp.crop.top - lp.crop.bottom)
        cr.translate(-x, -y)
        cr.rectangle(x, y, w, h)
        cr.clip()
        rotation = round((int(lp.angle) % 360) / 90) * 90
        if rotation > 0:
            cr.translate(*lp.size.scaled(0.5))
            cr.rotate(rotation * pi / 180)
            cr.translate(*lp.size_orig.scaled(-0.5))
        _legacy_poppler(cr, lp, pdfqueue)
        cr.restore()


def _legacy_render(p, pdfqueue, zoom):
    wpoi = p.size.width * (1 - p.crop.left - p.crop.right)
    hpoi = p.size.height * (1 - p.crop.top - p.crop.bottom)
    wpix = max(1, int(0.5 + wpoi * p.scale * zoom))
    hpix = max(1, int(0.5 + hpoi * p.scale * zoom))
    wpix0, hpix0 = (wpix, hpix) if p.angle in [0, 180] else (hpix, wpix)
    rotation = round((int(p.angle) % 360) / 90) * 90

    thumbnail = cairo.ImageSurface(cairo.FORMAT_ARGB32, wpix0, hpix0)
    cr = cairo.Context(thumbnail)
    if rotation > 0:
        cr.translate(wpix0 / 2, hpix0 / 2)
        cr.rotate(-rotation * pi / 180)
        cr.translate(-wpix / 2, -hpix / 2)
    cr.scale(wpix / wpoi, hpix / hpoi)
    cr.translate(-p.crop.left * p.size.width, -p.crop.top * p.size.height)
    _legacy_add_layers(cr, p, pdfqueue, layer='UNDERLAY')
    cr.save()
    if rotation > 0:
        cr.translate(*p.size.scaled(0.5))
        cr.rotate(rotation * pi / 180)
        cr.translate(*p.size_orig.scaled(-0.5))
    _legacy_poppler(cr, p, pdfqueue)
    cr.restore()
    _legacy_add_layers(cr, p, pdfqueue, layer='OVERLAY')

    if p.hide != core.Sides():
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(0, 0, p.size.width, p.size.height)
        x = p.size.width * p.hide.left
        y = p.size.height * p.hide.top
        w = p.size.width * (1 - p.hide.left - p.hide.right)
        h = p.size.height * (1 - p.hide.top - p.hide.bottom)
        cr.rectangle(x, y, w, h)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.fill()
    return thumbnail


# --- Fixtures ---------------------------------------------------------------

def _page(npage=1, zoom=0.25, scale=1.0, crop=(0, 0, 0, 0), hide=(0, 0, 0, 0), layers=()):
    return core.Page(1, npage, zoom, 'copy', 0, scale, core.Sides(*crop), core.Sides(*hide),
                     LETTER, 'test', [lp.duplicate() for lp in layers])


def _layer(laypos, npage=2, angle=0, scale=1.0, crop=(0, 0, 0, 0), offset=(0, 0, 0, 0)):
    return core.LayerPage(1, npage, 'copy', angle, scale, core.Sides(*crop),
                          core.Sides(*offset), laypos, LETTER)


def _rotated(p, angle):
    p.rotate(angle)
    return p


def _pixels(surface):
    surface.flush()
    return surface.get_width(), surface.get_height(), surface.get_stride(), bytes(surface.get_data())


def _cases():
    over = _layer('OVERLAY', scale=0.4, crop=(0.1, 0.2, 0.05, 0.3), offset=(0.3, 0.1, 0.5, 0.1))
    under = _layer('UNDERLAY', angle=90, scale=0.5, crop=(0, 0.1, 0.2, 0), offset=(0.05, 0.4, 0.1, 0.3))
    under2 = _layer('UNDERLAY', npage=1, angle=270, scale=0.3, offset=(0.6, 0.1, 0.6, 0.1))
    layered = (under, over, under2)
    return {
        'plain': _page(),
        'page 2': _page(npage=2),
        'odd zoom': _page(zoom=0.3137),
        'scale': _page(scale=1.7, zoom=0.2),
        'rotate 90': _rotated(_page(), 90),
        'rotate 180': _rotated(_page(), 180),
        'rotate 270': _rotated(_page(), 270),
        'crop': _page(crop=(0.1, 0.05, 0.2, 0.15)),
        'crop + rotate 90': _rotated(_page(crop=(0.1, 0.05, 0.2, 0.15)), 90),
        'hide': _page(hide=(0.2, 0.1, 0.15, 0.3)),
        'hide + rotate 270': _rotated(_page(hide=(0.2, 0.1, 0.15, 0.3)), 270),
        'layers': _page(layers=layered),
        'layers + rotate 90': _rotated(_page(layers=layered), 90),
        'everything': _rotated(_page(scale=1.3, crop=(0.05, 0.1, 0.02, 0.08),
                                     hide=(0.1, 0.0, 0.0, 0.2), layers=layered), 180),
        'everything + rotate 90 twice': _rotated(_rotated(
            _page(crop=(0.05, 0.1, 0.02, 0.08), hide=(0.1, 0, 0, 0.2), layers=layered), 90), 90),
    }


class PageRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='pdfarranger-test-')
        src = os.path.join(HERE, 'test.pdf')
        cls.pdfqueue = [core.PDFDoc(src, None, None, os.stat(src), cls.tmp, None)]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_identical_to_legacy(self):
        for name, p in _cases().items():
            with self.subTest(name):
                expected = _pixels(_legacy_render(p, self.pdfqueue, p.zoom))
                got = _pixels(pagerender.render_page(p, self.pdfqueue, p.zoom))
                self.assertEqual(expected[:3], got[:3])
                self.assertTrue(expected[3] == got[3], f"{name}: pixels differ")
                self.assertTrue(any(got[3]), f"{name}: rendered nothing")

    def test_cases_are_not_all_the_same(self):
        """Guard against a fixture that renders blank, which would make the test vacuous."""
        cases = _cases()
        renders = {name: _pixels(pagerender.render_page(p, self.pdfqueue, p.zoom))
                   for name, p in cases.items()}
        for name in ['page 2', 'crop', 'hide', 'layers']:
            self.assertNotEqual(renders['plain'], renders[name], name)

    def test_pixel_size(self):
        for name, p in _cases().items():
            with self.subTest(name):
                s = _legacy_render(p, self.pdfqueue, p.zoom)
                self.assertEqual(pagerender.pixel_size(p, p.zoom), (s.get_width(), s.get_height()))

    def test_draw_page_with_offset_context(self):
        """draw_page draws in the caller's user space and leaves the context as it was."""
        p = _cases()['everything']
        w, h = pagerender.pixel_size(p, p.zoom)
        big = cairo.ImageSurface(cairo.FORMAT_ARGB32, w + 20, h + 30)
        cr = cairo.Context(big)
        cr.translate(20, 30)
        matrix = cr.get_matrix()
        pagerender.draw_page(cr, p, self.pdfqueue, p.zoom)
        self.assertEqual(tuple(cr.get_matrix()), tuple(matrix))

        crop = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr2 = cairo.Context(crop)
        cr2.set_source_surface(big, -20, -30)
        cr2.paint()
        self.assertEqual(_pixels(crop), _pixels(_legacy_render(p, self.pdfqueue, p.zoom)))

    def test_cancelled_draws_nothing(self):
        p = _cases()['everything']
        s = pagerender.render_page(p, self.pdfqueue, p.zoom, cancelled=lambda: True)
        self.assertEqual((s.get_width(), s.get_height()), pagerender.pixel_size(p, p.zoom))
        self.assertFalse(any(_pixels(s)[3]))

    def test_pdfrenderer_update_uses_draw_page(self):
        """The thumbnail thread emits the same surface the legacy code produced."""
        renderer = core.PDFRenderer([], self.pdfqueue, (0, 0), 1)
        for name, p in _cases().items():
            with self.subTest(name), mock.patch.object(core.GObject, 'idle_add') as idle_add:
                size = renderer.update(p, None, p.zoom, False)
                thumbnail = idle_add.call_args.args[3]
                self.assertEqual(size, (thumbnail.get_width(), thumbnail.get_height()))
                self.assertEqual(_pixels(thumbnail),
                                 _pixels(_legacy_render(p, self.pdfqueue, p.zoom)))


if __name__ == '__main__':
    unittest.main()
