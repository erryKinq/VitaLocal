// Two-way navigation: swipe up/down changes the area (dots on the left), swipe left/right its
// pages (dots at the bottom). The bar at the right edge or holding the lower button opens the
// settings; the upper button goes back to the area's start page; swiping right on the first page
// or the lower button leaves the app.

import Toybox.Graphics;
import Toybox.Lang;
import Toybox.System;
import Toybox.WatchUi;

class PagerView extends WatchUi.View {
    var areas as Array = [];
    var ids as Array = [];
    var current as Number = 0;
    var page as Number = 0;
    private var _hits as Array = [];

    function initialize() {
        View.initialize();
        rebuild();
        var start = ids.indexOf(Prefs.startArea());
        current = start < 0 ? 0 : start;
        page = (areas[current] as Area).startPage();
        getApi().onChange = method(:changed);
    }

    // Areas as set in the settings (called again after they changed)
    function rebuild() as Void {
        var keep = ids.size() > 0 ? ids[current] : null;
        ids = Prefs.areas();
        areas = [];
        for (var i = 0; i < ids.size(); i++) {
            var id = ids[i];
            areas.add(id == 0 ? new DrinkArea() : id == 1 ? new SupplementArea() : id == 2 ? new HabitArea() : new WeightArea());
        }
        var i = keep == null ? -1 : ids.indexOf(keep);
        if (i < 0) {
            current = 0;
            page = (areas[0] as Area).startPage();
        } else {
            current = i;
        }
    }

    function onShow() as Void {
        rebuild();
        getApi().sync();
    }

    function changed() as Void {
        WatchUi.requestUpdate();
    }

    function onUpdate(dc as Graphics.Dc) as Void {
        dc.setColor(Graphics.COLOR_BLACK, Graphics.COLOR_BLACK);
        dc.clear();
        if (dc has :setAntiAlias) {
            dc.setAntiAlias(true);
        }
        var area = areas[current] as Area;
        var n = area.pageCount();
        if (page >= n) {
            page = n - 1;
        }
        _hits = [];
        area.draw(dc, page, _hits);
        Draw.dots(dc, areas.size(), current, area.color, true);
        Draw.dots(dc, n, page, area.color, false);
        Draw.menuHint(dc);
        var api = getApi();
        var note = api.status.length() > 0 ? api.status : (api.pending() > 0 ? api.pending() + " ausstehend" : "");
        if (note.length() > 0) {
            Draw.text(dc, dc.getWidth() / 2, dc.getHeight() * 0.065, Draw.f(dc, 24), note, Draw.WARN, Draw.C);
        }
    }

    function moveArea(step as Number) as Void {
        current = (current + step + areas.size()) % areas.size();
        page = (areas[current] as Area).startPage();
        WatchUi.requestUpdate();
    }

    // false: no page in that direction (swiping right on the first page leaves the app)
    function movePage(step as Number) as Boolean {
        var n = (areas[current] as Area).pageCount();
        var next = page + step;
        if (next < 0 || next >= n) {
            return step > 0;  // past the last page: stay
        }
        page = next;
        WatchUi.requestUpdate();
        return true;
    }

    function home() as Void {
        page = (areas[current] as Area).startPage();
        WatchUi.requestUpdate();
    }

    function tap(x as Number, y as Number) as Void {
        for (var i = 0; i < _hits.size(); i++) {
            var h = _hits[i] as Array;
            if (x >= h[0] && x <= h[2] && y >= h[1] && y <= h[3]) {
                (areas[current] as Area).tap(page, h[4]);
                WatchUi.requestUpdate();
                return;
            }
        }
    }
}

// A plain InputDelegate: a BehaviorDelegate turns screen taps into onSelect without coordinates
// on the Venu 4 (seen on the watch), so taps, swipes and keys are handled here directly.
class PagerDelegate extends WatchUi.InputDelegate {
    private var _view as PagerView;

    function initialize(view as PagerView) {
        InputDelegate.initialize();
        _view = view;
    }

    function onSwipe(evt as WatchUi.SwipeEvent) as Boolean {
        var dir = evt.getDirection();
        if (dir == WatchUi.SWIPE_UP) {
            _view.moveArea(1);
        } else if (dir == WatchUi.SWIPE_DOWN) {
            _view.moveArea(-1);
        } else if (dir == WatchUi.SWIPE_LEFT) {
            _view.movePage(1);
        } else if (!_view.movePage(-1)) {
            leave();  // swiping right on the first page leaves the app, as everywhere
        }
        return true;
    }

    function onTap(evt as WatchUi.ClickEvent) as Boolean {
        var xy = evt.getCoordinates();
        var w = System.getDeviceSettings().screenWidth;
        var k = w / 454.0;
        // the bar at the right edge, generously: edge touches are easily missed
        if (xy[0] > w * 0.8 && (xy[1] - w / 2).abs() < 100 * k) {
            openSettings();
        } else {
            _view.tap(xy[0], xy[1]);
        }
        return true;
    }

    // holding a finger on the screen also opens the settings
    function onHold(evt as WatchUi.ClickEvent) as Boolean {
        openSettings();
        return true;
    }

    // upper button: start page of the area; lower button: leave; holding it: settings
    function onKey(evt as WatchUi.KeyEvent) as Boolean {
        var key = evt.getKey();
        if (key == WatchUi.KEY_ENTER || key == WatchUi.KEY_START) {
            _view.home();
            return true;
        }
        if (key == WatchUi.KEY_MENU) {
            openSettings();
            return true;
        }
        if (key == WatchUi.KEY_ESC) {
            leave();
            return true;
        }
        return false;
    }

    private function leave() as Void {
        WatchUi.popView(WatchUi.SLIDE_RIGHT);
    }
}
