// The four areas of the app. Each draws one of its pages and registers tap targets as
// [x0, y0, x1, y1, action]; the pager calls tap() with the action of the target hit.

import Toybox.Attention;
import Toybox.Communications;
import Toybox.Graphics;
import Toybox.Lang;
import Toybox.WatchUi;

class Area {
    var color as Number;

    function initialize(c as Number) {
        color = c;
    }

    function pageCount() as Number {
        return 1;
    }

    function startPage() as Number {
        return 0;
    }

    function draw(dc as Graphics.Dc, page as Number, hits as Array) as Void {
    }

    function tap(page as Number, action) as Void {
    }
}

function hit(hits as Array, x as Numeric, y as Numeric, rx as Numeric, ry as Numeric, action) as Void {
    hits.add([x - rx, y - ry, x + rx, y + ry, action]);
}

function buzz() as Void {
    if (Attention has :vibrate) {
        Attention.vibrate([new Attention.VibeProfile(40, 35)]);
    }
}

function loading(dc as Graphics.Dc, color as Number) as Void {
    var w = dc.getWidth();
    Draw.text(dc, w / 2, w / 2, Draw.f(dc, 32), "Lädt ...", color, Draw.C);
}

function minutes(hhmm) as Number {
    if (!(hhmm instanceof String) || hhmm.length() < 5) {
        return -1;
    }
    return hhmm.substring(0, 2).toNumber() * 60 + hhmm.substring(3, 5).toNumber();
}

// --- drinks ------------------------------------------------------------------------------------

class DrinkArea extends Area {
    function initialize() {
        Area.initialize(0x38BDF8);
    }

    function pageCount() as Number {
        return 3;
    }

    function draw(dc as Graphics.Dc, page as Number, hits as Array) as Void {
        var d = getApi().part("drinks") as Dictionary or Null;
        if (d == null) {
            loading(dc, color);
        } else if (page == 0) {
            quick(dc, d, hits);
        } else if (page == 1) {
            caffeine(dc, d);
        } else {
            entries(dc, d, hits);
        }
    }

    private function quick(dc as Graphics.Dc, d as Dictionary, hits as Array) as Void {
        var w = dc.getWidth();
        var k = Draw.s(dc);
        var ml = Util.num(d["ml"]);
        var goal = d["goal_ml"] == null ? 2000 : Util.num(d["goal_ml"]);
        Draw.ring(dc, ml.toFloat() / goal, color);
        Draw.drop(dc, w / 2, w * 0.15, 34 * k, color);
        Draw.text(dc, w / 2, w * 0.33, Draw.big(dc, 96), ml.toString(), Draw.WHITE, Draw.C);
        Draw.text(dc, w / 2, w * 0.455, Draw.f(dc, 30), "von " + Util.thousands(goal) + " ml", Draw.MUTED, Draw.C);
        if (lastOwn() != null) {
            // undo the watch's last drink of today
            dc.setColor(Draw.MUTED, Graphics.COLOR_TRANSPARENT);
            dc.fillRoundedRectangle(w * 0.13, w * 0.33 - 3 * k, 30 * k, 7 * k, 3 * k);
            hit(hits, w * 0.13 + 15 * k, w * 0.33, 40 * k, 40 * k, :undoLast);
        }
        var cups = Prefs.cups();
        var xs = [0.28, 0.5, 0.72];
        for (var i = 0; i < 3; i++) {
            var c = cups[i] as Array;
            var x = w * xs[i];
            var y = w * 0.625;
            Draw.cup(dc, x, y, 40 * k, Draw.WHITE);
            Draw.text(dc, x, y + 46 * k, Draw.f(dc, 28), c[1] + " ml", Draw.WHITE, Draw.C);
            Draw.text(dc, x, y + 72 * k, Draw.f(dc, 22), (DRINK_KINDS[c[0]] as Array)[0] as String, Draw.MUTED, Draw.C);
            hit(hits, x, y + 25 * k, 50 * k, 70 * k, i);
        }
    }

    private function caffeine(dc as Graphics.Dc, d as Dictionary) as Void {
        var w = dc.getWidth();
        var last = d["last_caffeine"];
        Draw.text(dc, w / 2, w * 0.2, Draw.f(dc, 30), "Koffein", color, Draw.C);
        Draw.text(dc, w / 2, w * 0.37, Draw.big(dc, 88), last == null ? "--:--" : last as String, Draw.WHITE, Draw.C);
        Draw.text(dc, w / 2, w * 0.5, Draw.f(dc, 24), last == null ? "heute noch keins" : "letztes Getränk mit Koffein", Draw.MUTED, Draw.C);
        var n = Util.num(d["caffeine_n"]);
        Draw.text(dc, w / 2, w * 0.61, Draw.f(dc, 30), n == 1 ? "1 Getränk" : n + " Getränke", Draw.WHITE, Draw.C);
        var limit = d["caffeine_limit"];
        if (limit != null) {
            var ok = last == null || minutes(last) <= minutes(limit);
            Draw.text(dc, w / 2, w * 0.71, Draw.f(dc, 24), "Grenze " + limit, Draw.MUTED, Draw.C);
            Draw.text(dc, w / 2, w * 0.78, Draw.f(dc, 30), ok ? "eingehalten" : "überschritten", ok ? Draw.OK : Draw.WARN, Draw.C);
        }
    }

    private function entries(dc as Graphics.Dc, d as Dictionary, hits as Array) as Void {
        var w = dc.getWidth();
        var k = Draw.s(dc);
        var list = d["entries"] as Array or Null;
        Draw.text(dc, w / 2, w * 0.15, Draw.f(dc, 30), "Heute", color, Draw.C);
        if (list == null || list.size() == 0) {
            Draw.text(dc, w / 2, w * 0.45, Draw.f(dc, 30), "Noch nichts getrunken", Draw.MUTED, Draw.C);
        } else {
            var n = list.size() < 5 ? list.size() : 5;
            for (var i = 0; i < n; i++) {
                var e = list[list.size() - 1 - i] as Dictionary;
                var y = w * 0.27 + i * 46 * k;
                Draw.text(dc, w * 0.19, y, Draw.f(dc, 24), e["t"] as String, Draw.MUTED, Draw.L);
                Draw.text(dc, w * 0.36, y, Draw.f(dc, 30), kindName(e["drink"] as String, e["caffeine"]), Draw.WHITE, Draw.L);
                Draw.text(dc, w * 0.81, y, Draw.f(dc, 30), Util.num(e["ml"]).toString(), Draw.WHITE, Draw.R);
            }
        }
        Draw.text(dc, w / 2, w * 0.81, Draw.f(dc, 30), "+ eigenes Getränk", color, Draw.C);
        hit(hits, w / 2, w * 0.81, w * 0.3, 32 * k, :custom);
    }

    function tap(page as Number, action) as Void {
        if (action == :undoLast) {
            undo();
        } else if (action == :custom) {
            chooseDrink("Getränk", method(:log));
        } else if (action instanceof Number) {
            var c = Prefs.cups()[action] as Array;
            log(c[0], c[1]);
        }
    }

    // Log a drink: shown at once, sent through the queue
    function log(kind as Number, ml as Number) as Void {
        var k = DRINK_KINDS[kind] as Array;
        var caf = k[2] != null ? k[2] : ("coffee".equals(k[1]) || "tea".equals(k[1]));
        var id = Util.newId();
        var body = {"id" => id, "ts" => Util.now(), "drink" => k[1], "ml" => ml, "source" => "watch"};
        if (k[2] != null) {
            body["caffeine"] = k[2];
        }
        var d = getApi().part("drinks") as Dictionary or Null;
        if (d != null) {
            d["ml"] = Util.num(d["ml"]) + ml;
            var list = d["entries"] as Array or Null;
            if (list != null) {
                list.add({"id" => id, "t" => Util.clock(), "drink" => k[1], "ml" => ml, "caffeine" => caf});
            }
            if (caf) {
                d["caffeine_n"] = Util.num(d["caffeine_n"]) + 1;
                d["last_caffeine"] = Util.clock();
            }
        }
        var own = Util.stored("own", []) as Array;
        own.add([id, Util.day(), ml, caf]);
        Application.Storage.setValue("own", own.size() > 20 ? own.slice(-20, null) : own);
        buzz();
        getApi().enqueue(Communications.HTTP_REQUEST_METHOD_POST, "/api/v1/drinks", body);
    }

    private function lastOwn() as Array or Null {
        var own = Util.stored("own", []) as Array;
        var today = Util.day();
        for (var i = own.size() - 1; i >= 0; i--) {
            if (today.equals((own[i] as Array)[1])) {
                return own[i] as Array;
            }
        }
        return null;
    }

    private function undo() as Void {
        var e = lastOwn();
        if (e == null) {
            return;
        }
        var own = Util.stored("own", []) as Array;
        own.remove(e);
        Application.Storage.setValue("own", own);
        var d = getApi().part("drinks") as Dictionary or Null;
        if (d != null) {
            d["ml"] = Util.num(d["ml"]) - Util.num(e[2]);
            var list = d["entries"] as Array or Null;
            if (list != null) {
                for (var i = 0; i < list.size(); i++) {
                    if ((list[i] as Dictionary)["id"].equals(e[0])) {
                        list.remove(list[i]);
                        break;
                    }
                }
                if (e[3] == true) {
                    d["caffeine_n"] = Util.num(d["caffeine_n"]) - 1;
                    d["last_caffeine"] = null;
                    for (var i = list.size() - 1; i >= 0; i--) {
                        if ((list[i] as Dictionary)["caffeine"] == true) {
                            d["last_caffeine"] = (list[i] as Dictionary)["t"];
                            break;
                        }
                    }
                }
            }
        }
        buzz();
        WatchUi.showToast("Rückgängig: " + e[2] + " ml", null);
        getApi().enqueue(Communications.HTTP_REQUEST_METHOD_DELETE, "/api/v1/drinks/" + e[0], null);
    }
}

function kindName(drink as String, caffeine) as String {
    for (var i = 0; i < DRINK_KINDS.size(); i++) {
        var k = DRINK_KINDS[i] as Array;
        if (drink.equals(k[1]) && (k[2] == null || caffeine == k[2])) {
            return k[0] as String;
        }
    }
    return drink;
}

// --- supplements -------------------------------------------------------------------------------

const SLOTS = ["morning", "noon", "evening", "other"];
const SLOT_NAMES = ["Morgens", "Mittags", "Abends", "Weitere"];

class SupplementArea extends Area {
    function initialize() {
        Area.initialize(0xA78BFA);
    }

    private function items() as Array {
        var list = getApi().part("supplements");
        return list instanceof Array ? list : [];
    }

    // slot indexes with at least one supplement
    private function slots() as Array {
        var out = [];
        var list = items();
        for (var s = 0; s < SLOTS.size(); s++) {
            for (var i = 0; i < list.size(); i++) {
                if (((list[i] as Dictionary)["slots"] as Array).indexOf(SLOTS[s]) >= 0) {
                    out.add(s);
                    break;
                }
            }
        }
        return out;
    }

    private function onPage(page as Number) as Array {
        var s = slots();
        var out = [];
        if (page >= s.size()) {
            return out;
        }
        var list = items();
        for (var i = 0; i < list.size(); i++) {
            if (((list[i] as Dictionary)["slots"] as Array).indexOf(SLOTS[s[page]]) >= 0) {
                out.add(i);
            }
        }
        return out;
    }

    function pageCount() as Number {
        var n = slots().size();
        return n > 0 ? n : 1;
    }

    // the page of the current time of day
    function startPage() as Number {
        var h = Util.hour();
        var want = h < 11 ? 0 : (h < 17 ? 1 : 2);
        var i = slots().indexOf(want);
        return i < 0 ? 0 : i;
    }

    function draw(dc as Graphics.Dc, page as Number, hits as Array) as Void {
        var w = dc.getWidth();
        var k = Draw.s(dc);
        if (getApi().today == null) {
            loading(dc, color);
            return;
        }
        var idx = onPage(page);
        if (idx.size() == 0) {
            Draw.text(dc, w / 2, w / 2, Draw.f(dc, 32), "Keine Supplements", Draw.MUTED, Draw.C);
            return;
        }
        var list = items();
        var done = 0;
        for (var i = 0; i < idx.size(); i++) {
            var s = list[idx[i]] as Dictionary;
            if (Util.num(s["count"]) >= Util.num(s["per_day"])) {
                done++;
            }
        }
        Draw.ring(dc, done.toFloat() / idx.size(), color);
        var slot = SLOT_NAMES[slots()[page]];
        Draw.text(dc, w / 2, w * 0.17, Draw.f(dc, 30), slot + " · " + done + " von " + idx.size(), color, Draw.C);
        var n = idx.size() < 6 ? idx.size() : 6;
        var step = 50 * k;
        var y0 = w * 0.5 - (n - 1) * step / 2;
        for (var i = 0; i < n; i++) {
            var s = list[idx[i]] as Dictionary;
            var y = y0 + i * step;
            var count = Util.num(s["count"]);
            var per = Util.num(s["per_day"]);
            var full = count >= per;
            var quiet = full || s["rest"] == true;
            Draw.text(dc, w * 0.16, y, Draw.f(dc, 30), s["name"] as String, quiet ? Draw.MUTED : Draw.WHITE, Draw.L);
            if (per <= 5) {
                Draw.pips(dc, w * 0.84, y, per, count, quiet && !full ? Draw.DIM : color);
            } else {
                Draw.text(dc, w * 0.84, y, Draw.f(dc, 30), count + "/" + per, full ? color : Draw.WHITE, Draw.R);
            }
            hit(hits, w / 2, y, w * 0.38, step / 2, idx[i]);
        }
    }

    // one more unit; after the last one back to 0 (to undo a wrong tap)
    function tap(page as Number, action) as Void {
        var s = items()[action] as Dictionary;
        var count = Util.num(s["count"]) + 1;
        if (count > Util.num(s["per_day"])) {
            count = 0;
        }
        s["count"] = count;
        buzz();
        getApi().enqueue(Communications.HTTP_REQUEST_METHOD_PUT,
            "/api/v1/supplements/" + s["id"] + "/log/" + Util.day(), {"count" => count});
    }
}

// --- habits ------------------------------------------------------------------------------------

class HabitArea extends Area {
    function initialize() {
        Area.initialize(0x4ADE80);
    }

    private function items() as Array {
        var list = getApi().part("habits");
        return list instanceof Array ? list : [];
    }

    // [kind, indexes]: tick-off lists of 4, one page per counted habit, automatic lists of 3
    private function pages() as Array {
        var list = items();
        var checks = [];
        var counts = [];
        var autos = [];
        for (var i = 0; i < list.size(); i++) {
            var h = list[i] as Dictionary;
            if (h["auto"] == true) {
                autos.add(i);
            } else if ("count".equals(h["kind"])) {
                counts.add(i);
            } else {
                checks.add(i);
            }
        }
        var out = [];
        for (var i = 0; i < checks.size(); i += 4) {
            out.add([:check, checks.slice(i, i + 4 < checks.size() ? i + 4 : null)]);
        }
        for (var i = 0; i < counts.size(); i++) {
            out.add([:count, [counts[i]]]);
        }
        for (var i = 0; i < autos.size(); i += 3) {
            out.add([:auto, autos.slice(i, i + 3 < autos.size() ? i + 3 : null)]);
        }
        return out;
    }

    function pageCount() as Number {
        var n = pages().size();
        return n > 0 ? n : 1;
    }

    function draw(dc as Graphics.Dc, page as Number, hits as Array) as Void {
        var w = dc.getWidth();
        if (getApi().today == null) {
            loading(dc, color);
            return;
        }
        var p = pages();
        if (page >= p.size()) {
            Draw.text(dc, w / 2, w / 2, Draw.f(dc, 32), "Keine Gewohnheiten", Draw.MUTED, Draw.C);
            return;
        }
        var kind = (p[page] as Array)[0];
        var idx = (p[page] as Array)[1] as Array;
        if (kind == :check) {
            checks(dc, idx, hits);
        } else if (kind == :count) {
            counter(dc, idx[0], hits);
        } else {
            autos(dc, idx);
        }
    }

    private function checks(dc as Graphics.Dc, idx as Array, hits as Array) as Void {
        var w = dc.getWidth();
        var k = Draw.s(dc);
        var list = items();
        // done of all habits ticked off by hand, not only this page
        var all = 0;
        var done = 0;
        for (var i = 0; i < list.size(); i++) {
            var h = list[i] as Dictionary;
            if (h["auto"] != true && !"count".equals(h["kind"])) {
                all++;
                if (h["done"] == true) {
                    done++;
                }
            }
        }
        Draw.ring(dc, done.toFloat() / all, color);
        Draw.text(dc, w / 2, w * 0.17, Draw.f(dc, 30), "Heute · " + done + " von " + all, color, Draw.C);
        var step = 62 * k;
        var y0 = w * 0.5 - (idx.size() - 1) * step / 2;
        for (var i = 0; i < idx.size(); i++) {
            var h = list[idx[i]] as Dictionary;
            var y = y0 + i * step;
            var on = h["done"] == true;
            Draw.check(dc, w * 0.2, y, 17 * k, on, color);
            Draw.text(dc, w * 0.27, y, Draw.f(dc, 30), h["name"] as String, on ? Draw.MUTED : Draw.WHITE, Draw.L);
            hit(hits, w / 2, y, w * 0.38, step / 2, idx[i]);
        }
    }

    private function counter(dc as Graphics.Dc, i as Number, hits as Array) as Void {
        var w = dc.getWidth();
        var k = Draw.s(dc);
        var h = items()[i] as Dictionary;
        var value = Util.num(h["value"]);
        var target = Util.num(h["target"]);
        Draw.ring(dc, target > 0 ? value.toFloat() / target : 0.0, color);
        Draw.text(dc, w / 2, w * 0.2, Draw.f(dc, 30), h["name"] as String, color, Draw.C);
        Draw.text(dc, w / 2, w * 0.43, Draw.big(dc, 110), value.toString(), Draw.WHITE, Draw.C);
        var unit = h["unit"] == null ? "" : " " + h["unit"];
        Draw.text(dc, w / 2, w * 0.58, Draw.f(dc, 30), "von " + target + unit, Draw.MUTED, Draw.C);
        // minus and plus
        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        var y = w * 0.43;
        dc.fillRoundedRectangle(w * 0.15, y - 4 * k, 40 * k, 8 * k, 4 * k);
        dc.fillRoundedRectangle(w * 0.85 - 40 * k, y - 4 * k, 40 * k, 8 * k, 4 * k);
        dc.fillRoundedRectangle(w * 0.85 - 24 * k, y - 20 * k, 8 * k, 40 * k, 4 * k);
        hit(hits, w * 0.15 + 20 * k, y, 50 * k, 60 * k, [i, -1]);
        hit(hits, w * 0.85 - 20 * k, y, 50 * k, 60 * k, [i, 1]);
        Draw.text(dc, w / 2, w * 0.74, Draw.f(dc, 24), "Tippen auf +/-", Draw.DIM, Draw.C);
    }

    private function autos(dc as Graphics.Dc, idx as Array) as Void {
        var w = dc.getWidth();
        var k = Draw.s(dc);
        var list = items();
        Draw.text(dc, w / 2, w * 0.17, Draw.f(dc, 30), "Automatisch", color, Draw.C);
        var step = 76 * k;
        var y0 = w * 0.48 - (idx.size() - 1) * step / 2;
        for (var i = 0; i < idx.size(); i++) {
            var h = list[idx[i]] as Dictionary;
            var y = y0 + i * step;
            var on = h["done"] == true;
            Draw.text(dc, w * 0.17, y - 14 * k, Draw.f(dc, 30), h["name"] as String, Draw.WHITE, Draw.L);
            var t = h["text"];
            if (t != null) {
                Draw.text(dc, w * 0.83, y + 22 * k, Draw.f(dc, 24), t as String, Draw.MUTED, Draw.R);
            }
            var frac = h["frac"];
            Draw.bar(dc, w * 0.17, y + 6 * k, w * 0.66, frac == null ? (on ? 1.0 : 0.0) : (frac as Numeric).toFloat(), on ? color : Draw.WARN);
        }
    }

    function tap(page as Number, action) as Void {
        var list = items();
        if (action instanceof Number) {
            var h = list[action] as Dictionary;
            set(h, h["done"] == true ? 0 : 1);
        } else if (action instanceof Array) {
            var h = list[action[0]] as Dictionary;
            var v = Util.num(h["value"]) + (action[1] as Number);
            set(h, v < 0 ? 0 : v);
        }
    }

    private function set(h as Dictionary, value as Number) as Void {
        h["value"] = value;
        h["done"] = "count".equals(h["kind"]) ? value >= Util.num(h["target"]) : value >= 1;
        buzz();
        getApi().enqueue(Communications.HTTP_REQUEST_METHOD_PUT,
            "/api/v1/habits/" + h["id"] + "/log/" + Util.day(), {"value" => value});
    }
}

// --- weight ------------------------------------------------------------------------------------

class WeightArea extends Area {
    function initialize() {
        Area.initialize(0xF472B6);
    }

    function draw(dc as Graphics.Dc, page as Number, hits as Array) as Void {
        var w = dc.getWidth();
        if (getApi().today == null) {
            loading(dc, color);
            return;
        }
        var wt = getApi().part("weight") as Dictionary or Null;
        Draw.text(dc, w / 2, w * 0.18, Draw.f(dc, 30), "Gewicht", color, Draw.C);
        hit(hits, w / 2, w / 2, w * 0.4, w * 0.4, :enter);
        if (wt == null) {
            Draw.text(dc, w / 2, w * 0.45, Draw.f(dc, 32), "Noch kein Wert", Draw.MUTED, Draw.C);
            Draw.text(dc, w / 2, w * 0.8, Draw.f(dc, 24), "Tippen = eintragen", Draw.MUTED, Draw.C);
            return;
        }
        Draw.text(dc, w / 2, w * 0.36, Draw.big(dc, 96), Util.dec(wt["kg"] as Numeric, 1), Draw.WHITE, Draw.C);
        var days = wt["days"] as Array or Null;
        var sub = "kg";
        if (days != null && days.size() > 1) {
            var first = ((days[0] as Array)[1] as Numeric).toFloat();
            var last = ((days[days.size() - 1] as Array)[1] as Numeric).toFloat();
            var diff = last - first;
            sub += " · " + (diff > 0 ? "+" : "") + Util.dec(diff, 1) + " in " + (days[0] as Array)[0] + " Tagen";
            spark(dc, days);
        }
        Draw.text(dc, w / 2, w * 0.49, Draw.f(dc, 24), sub, Draw.MUTED, Draw.C);
        Draw.text(dc, w / 2, w * 0.8, Draw.f(dc, 24), "Tippen = eintragen", Draw.MUTED, Draw.C);
    }

    // [days ago, kg] as a line from 30 days ago to today
    private function spark(dc as Graphics.Dc, days as Array) as Void {
        var w = dc.getWidth();
        var k = Draw.s(dc);
        var lo = 999.0;
        var hi = 0.0;
        for (var i = 0; i < days.size(); i++) {
            var v = ((days[i] as Array)[1] as Numeric).toFloat();
            lo = v < lo ? v : lo;
            hi = v > hi ? v : hi;
        }
        var span = hi - lo < 1 ? 1.0 : hi - lo;
        var x0 = w * 0.22;
        var x1 = w * 0.78;
        var y0 = w * 0.57;
        var y1 = w * 0.70;
        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        dc.setPenWidth((3 * k).toNumber() + 1);
        var px = 0.0;
        var py = 0.0;
        for (var i = 0; i < days.size(); i++) {
            var d = days[i] as Array;
            var x = x1 - ((d[0] as Numeric).toFloat() / 29) * (x1 - x0);
            var y = y1 - ((((d[1] as Numeric).toFloat()) - lo) / span) * (y1 - y0);
            if (i > 0) {
                dc.drawLine(px, py, x, y);
            }
            px = x;
            py = y;
        }
        dc.fillCircle(px, py, 6 * k);
        dc.setPenWidth(1);
    }

    function tap(page as Number, action) as Void {
        var wt = getApi().part("weight") as Dictionary or Null;
        var kg = wt == null ? 70.0 : (wt["kg"] as Numeric).toFloat();
        WatchUi.pushView(new WeightPicker(kg), new WeightDelegate(), WatchUi.SLIDE_LEFT);
    }
}
