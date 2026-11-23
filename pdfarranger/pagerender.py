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

"""Draw a model Page (crop, rotation, hide, layers) with cairo and Poppler.

This is the single place that composites a page for display. Thumbnails
(core.PDFRenderer) and the page view both use it. It depends only on cairo and
on the pdfqueue documents, not on GTK, so it can be tested without a display.

Every function takes an optional ``cancelled`` callable. When it returns True,
drawing stops early and the result is incomplete (the caller discards it).
"""

__all__ = ["pixel_size", "draw_page", "render_page"]

from math import pi

import cairo


def _never():
    return False


def _rotation(angle):
    """Angle snapped to a multiple of 90 in [0, 360)."""
    return round((int(angle) % 360) / 90) * 90


def _unrotated_pixel_size(p, zoom):
    """Pixel size of the cropped page before the page rotation is applied."""
    wpoi = p.size.width * (1 - p.crop.left - p.crop.right)
    hpoi = p.size.height * (1 - p.crop.top - p.crop.bottom)
    wpix = max(1, int(0.5 + wpoi * p.scale * zoom))
    hpix = max(1, int(0.5 + hpoi * p.scale * zoom))
    return wpoi, hpoi, wpix, hpix


def pixel_size(p, zoom):
    """Width and height in pixels of ``p`` drawn at ``zoom``."""
    _, _, wpix, hpix = _unrotated_pixel_size(p, zoom)
    return (wpix, hpix) if p.angle in [0, 180] else (hpix, wpix)


def _render_poppler(cr, p, pdfqueue, cancelled):
    if cancelled():
        return
    pdfdoc = pdfqueue[p.nfile - 1]
    page = pdfdoc.get_page(p.npage - 1)
    with pdfdoc.render_lock:
        page.render(cr)


def _draw_layers(cr, p, pdfqueue, layer, cancelled):
    layerpages = p.layerpages if layer == 'OVERLAY' else reversed(p.layerpages)
    for lp in layerpages:
        if cancelled():
            return
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
        rotation = _rotation(lp.angle)
        if rotation > 0:
            cr.translate(*lp.size.scaled(0.5))
            cr.rotate(rotation * pi / 180)
            cr.translate(*lp.size_orig.scaled(-0.5))
        _render_poppler(cr, lp, pdfqueue, cancelled)
        cr.restore()


def draw_page(cr, p, pdfqueue, zoom, cancelled=None):
    """Draw ``p`` onto ``cr`` at ``zoom``.

    The page fills the rectangle (0, 0) – ``pixel_size(p, zoom)`` in the current
    user space of ``cr``. The context's state is restored on return.
    """
    cancelled = cancelled or _never
    wpoi, hpoi, wpix, hpix = _unrotated_pixel_size(p, zoom)
    wpix0, hpix0 = (wpix, hpix) if p.angle in [0, 180] else (hpix, wpix)
    rotation = _rotation(p.angle)

    cr.save()
    if rotation > 0:
        cr.translate(wpix0 / 2, hpix0 / 2)
        cr.rotate(-rotation * pi / 180)
        cr.translate(-wpix / 2, -hpix / 2)
    cr.scale(wpix / wpoi, hpix / hpoi)
    cr.translate(-p.crop.left * p.size.width, -p.crop.top * p.size.height)
    _draw_layers(cr, p, pdfqueue, 'UNDERLAY', cancelled)
    cr.save()
    if rotation > 0:
        cr.translate(*p.size.scaled(0.5))
        cr.rotate(rotation * pi / 180)
        cr.translate(*p.size_orig.scaled(-0.5))
    _render_poppler(cr, p, pdfqueue, cancelled)
    cr.restore()
    _draw_layers(cr, p, pdfqueue, 'OVERLAY', cancelled)

    if tuple(p.hide) != (0, 0, 0, 0) and not cancelled():
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(0, 0, p.size.width, p.size.height)
        x = p.size.width * p.hide.left
        y = p.size.height * p.hide.top
        w = p.size.width * (1 - p.hide.left - p.hide.right)
        h = p.size.height * (1 - p.hide.top - p.hide.bottom)
        cr.rectangle(x, y, w, h)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.fill()
    cr.restore()


def render_page(p, pdfqueue, zoom, cancelled=None):
    """Return a new ARGB32 ``cairo.ImageSurface`` of ``p`` drawn at ``zoom``."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, *pixel_size(p, zoom))
    cr = cairo.Context(surface)
    draw_page(cr, p, pdfqueue, zoom, cancelled)
    return surface
