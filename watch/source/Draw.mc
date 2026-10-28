// Drawing helpers for the round AMOLED screen. Positions are fractions of the screen so the 41 mm
// (390 px) and 45 mm (454 px) Venu 4 look alike; `s` scales pixel sizes from the 454 px design.

import Toybox.Graphics;
import Toybox.Lang;
import Toybox.Math;

module Draw {
    const WHITE = 0xFAFAFA;
    const MUTED = 0xA1A1AA;
    const DIM = 0x3F3F46;
    const TRACK = 0x1C1C20;
    const OK = 0x4ADE80;
    const WARN = 0xFBBF24;
    const C = Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER;
    const L = Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER;
    const R = Graphics.TEXT_JUSTIFY_RIGHT | Graphics.TEXT_JUSTIFY_VCENTER;

    var _fonts as Dictionary = {};

    // Scalable font with every glyph (comma, umlauts) at `px` on the 454 px design; the built-in
    // number font where the device has no vector fonts
    function big(dc as Graphics.Dc, px as Number) {
        var size = (px * s(dc)).toNumber();
        if (!_fonts.hasKey(size)) {
            var f = null;
            if (Graphics has :getVectorFont) {
                f = Graphics.getVectorFont({:face => ["RobotoCondensedBold", "BionicBold", "RobotoRegular", "RobotoCondensedRegular"], :size => size});
            }
            _fonts[size] = f == null ? Graphics.FONT_NUMBER_MEDIUM : f;
        }
        return _fonts[size];
    }

    // Regular text at `px` on the 454 px design (the built-in fonts run large on the Venu 4)
    function f(dc as Graphics.Dc, px as Number) {
        var size = (px * s(dc)).toNumber();
        var key = -size;
        if (!_fonts.hasKey(key)) {
            var font = null;
            if (Graphics has :getVectorFont) {
                font = Graphics.getVectorFont({:face => ["RobotoRegular", "RobotoCondensedRegular", "BionicSemiBold"], :size => size});
            }
            _fonts[key] = font == null ? (px >= 30 ? Graphics.FONT_TINY : Graphics.FONT_XTINY) : font;
        }
        return _fonts[key];
    }

    function s(dc as Graphics.Dc) as Float {
        return dc.getWidth() / 454.0;
    }

    function text(dc as Graphics.Dc, x as Numeric, y as Numeric, font, str as String, color as Number, just as Number) as Void {
        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        dc.drawText(x, y, font, str, just);
    }

    // Progress ring along the edge, starting at 12 o'clock
    function ring(dc as Graphics.Dc, frac as Float, color as Number) as Void {
        var w = dc.getWidth();
        var r = w / 2 - 9 * s(dc);
        dc.setPenWidth((8 * s(dc)).toNumber());
        dc.setColor(TRACK, Graphics.COLOR_TRANSPARENT);
        dc.drawCircle(w / 2, w / 2, r);
        if (frac <= 0) {
            return;
        }
        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        if (frac >= 1) {
            dc.drawCircle(w / 2, w / 2, r);
        } else {
            dc.drawArc(w / 2, w / 2, r, Graphics.ARC_CLOCKWISE, 90, 90 - frac * 360);
        }
        dc.setPenWidth(1);
    }

    // Area dots on the left (vertical) and page dots at the bottom (horizontal)
    function dots(dc as Graphics.Dc, n as Number, active as Number, color as Number, vertical as Boolean) as Void {
        if (n < 2) {
            return;
        }
        var k = s(dc);
        var gap = 17 * k;
        for (var i = 0; i < n; i++) {
            var off = (i - (n - 1) / 2.0) * gap;
            var x = vertical ? 32 * k : dc.getWidth() / 2 + off;
            var y = vertical ? dc.getHeight() / 2 + off : dc.getHeight() - 36 * k;
            dc.setColor(i == active ? color : DIM, Graphics.COLOR_TRANSPARENT);
            dc.fillCircle(x, y, (i == active ? 5.5 : 4) * k);
        }
    }

    // Garmin's menu hint: a short bar at the right edge
    function menuHint(dc as Graphics.Dc) as Void {
        var k = s(dc);
        dc.setColor(MUTED, Graphics.COLOR_TRANSPARENT);
        dc.fillRoundedRectangle(dc.getWidth() - 30 * k, dc.getHeight() / 2 - 32 * k, 6 * k, 64 * k, 3 * k);
    }

    function drop(dc as Graphics.Dc, x as Numeric, y as Numeric, size as Numeric, color as Number) as Void {
        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        dc.fillCircle(x, y + size * 0.25, size * 0.5);
        dc.fillPolygon([[x, y - size * 0.75], [x - size * 0.46, y + size * 0.1], [x + size * 0.46, y + size * 0.1]]);
    }

    // Garmin-like cup outline with a plus inside
    function cup(dc as Graphics.Dc, x as Numeric, y as Numeric, size as Numeric, color as Number) as Void {
        var top = size * 0.5;
        var bot = size * 0.36;
        var h = size * 0.62;
        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        dc.setPenWidth((size / 14).toNumber() + 1);
        dc.drawLine(x - top, y - h, x + top, y - h);
        dc.drawLine(x + top, y - h, x + bot, y + h);
        dc.drawLine(x + bot, y + h, x - bot, y + h);
        dc.drawLine(x - bot, y + h, x - top, y - h);
        var p = size * 0.2;
        dc.drawLine(x - p, y, x + p, y);
        dc.drawLine(x, y - p, x, y + p);
        dc.setPenWidth(1);
    }

    function check(dc as Graphics.Dc, x as Numeric, y as Numeric, r as Numeric, on as Boolean, color as Number) as Void {
        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        dc.setPenWidth((r / 5).toNumber() + 1);
        if (on) {
            dc.fillCircle(x, y, r);
            dc.setColor(Graphics.COLOR_BLACK, Graphics.COLOR_TRANSPARENT);
            dc.drawLine(x - r * 0.45, y, x - r * 0.1, y + r * 0.35);
            dc.drawLine(x - r * 0.1, y + r * 0.35, x + r * 0.5, y - r * 0.35);
        } else {
            dc.drawCircle(x, y, r);
        }
        dc.setPenWidth(1);
    }

    // `n` small circles ending at xRight, the first `filled` filled
    function pips(dc as Graphics.Dc, xRight as Numeric, y as Numeric, n as Number, filled as Number, color as Number) as Void {
        var k = s(dc);
        var r = 7 * k;
        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        dc.setPenWidth((2 * k).toNumber() + 1);
        for (var i = 0; i < n; i++) {
            var x = xRight - (n - 1 - i) * 19 * k;
            if (i < filled) {
                dc.fillCircle(x, y, r);
            } else {
                dc.drawCircle(x, y, r);
            }
        }
        dc.setPenWidth(1);
    }

    function bar(dc as Graphics.Dc, x as Numeric, y as Numeric, w as Numeric, frac as Float, color as Number) as Void {
        var k = s(dc);
        dc.setColor(TRACK, Graphics.COLOR_TRANSPARENT);
        dc.fillRoundedRectangle(x, y, w, 7 * k, 3 * k);
        if (frac > 0) {
            dc.setColor(color, Graphics.COLOR_TRANSPARENT);
            dc.fillRoundedRectangle(x, y, w * (frac > 1 ? 1 : frac), 7 * k, 3 * k);
        }
    }
}
