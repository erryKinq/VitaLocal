// The line in Garmin's glance list (swipe down on the watch face): drunk / goal, or supplements.
// Shows the summary the app or the background sync stored last; a new day starts at 0.

import Toybox.Application;
import Toybox.Graphics;
import Toybox.Lang;
import Toybox.WatchUi;

(:glance)
var glanceData as Dictionary or Null = null;  // from onBackgroundData while the glance is shown

(:glance)
class VitaGlance extends WatchUi.GlanceView {
    function initialize() {
        GlanceView.initialize();
    }

    function onUpdate(dc as Graphics.Dc) as Void {
        var w = dc.getWidth();
        var h = dc.getHeight();
        var g = glanceData != null ? glanceData : Application.Storage.getValue("glance") as Dictionary or Null;
        var mode = Application.Storage.getValue("glance_mode");
        var fresh = g != null && Util.day().equals(g["day"]);
        dc.setColor(Graphics.COLOR_TRANSPARENT, Graphics.COLOR_TRANSPARENT);
        dc.clear();
        if (dc has :setAntiAlias) {
            dc.setAntiAlias(true);
        }
        var color = mode == 1 ? 0xA78BFA : 0x38BDF8;
        var title = mode == 1 ? "SUPPLEMENTS" : "TRINKEN";
        var value;
        var frac = 0.0;
        if (mode == 1) {
            var n = g == null ? 0 : Util.num(g["sup_n"]);
            var done = fresh ? Util.num(g["sup_done"]) : 0;
            value = n == 0 ? "--" : done + " von " + n;
            frac = n == 0 ? 0.0 : done.toFloat() / n;
        } else {
            var ml = fresh ? Util.num(g["ml"]) : 0;
            var goal = g == null || g["goal"] == null ? 2000 : Util.num(g["goal"]);
            value = Util.thousands(ml) + " / " + Util.thousands(goal) + " ml";
            frac = ml.toFloat() / goal;
        }
        dc.setColor(0xA1A1AA, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, h * 0.22, Graphics.FONT_XTINY, title, Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
        dc.setColor(0xFAFAFA, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, h * 0.55, Graphics.FONT_TINY, value, Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
        var bh = h * 0.08 < 4 ? 4 : h * 0.08;
        dc.setColor(0x27272A, Graphics.COLOR_TRANSPARENT);
        dc.fillRoundedRectangle(0, h * 0.84, w * 0.9, bh, bh / 2);
        if (frac > 0) {
            dc.setColor(color, Graphics.COLOR_TRANSPARENT);
            dc.fillRoundedRectangle(0, h * 0.84, w * 0.9 * (frac > 1 ? 1 : frac), bh, bh / 2);
        }
    }
}
