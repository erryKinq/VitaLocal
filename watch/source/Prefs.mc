// Watch-side settings (Storage): the three drink cups, visible areas, start area, glance content.

import Toybox.Application;
import Toybox.Lang;

// [label, drink kind, caffeine (null: the server's default, true for coffee and tea)]
const DRINK_KINDS = [
    ["Wasser", "water", null],
    ["Kaffee", "coffee", null],
    ["Tee", "tea", null],
    ["Kräutertee", "tea", false],
    ["Sonstiges", "other", null]
];
const DRINK_AMOUNTS = [100, 150, 200, 250, 300, 330, 400, 500, 750];
const AREA_NAMES = ["Trinken", "Supplements", "Gewohnheiten", "Gewicht"];

module Prefs {
    // [kind index, ml] per cup
    function cups() as Array {
        return Util.stored("cups", [[0, 250], [1, 200], [2, 330]]) as Array;
    }

    function setCup(i as Number, kind as Number, ml as Number) as Void {
        var c = cups();
        c[i] = [kind, ml];
        Application.Storage.setValue("cups", c);
    }

    function cupLabel(i as Number) as String {
        var c = cups()[i] as Array;
        return (DRINK_KINDS[c[0]] as Array)[0] + " " + c[1] + " ml";
    }

    // Area ids (0 drinks, 1 supplements, 2 habits, 3 weight) shown, in order
    function areas() as Array {
        var off = Util.stored("hidden", []) as Array;
        var out = [];
        for (var i = 0; i < AREA_NAMES.size(); i++) {
            if (off.indexOf(i) < 0) {
                out.add(i);
            }
        }
        return out;
    }

    function toggleArea(id as Number) as Boolean {
        var off = Util.stored("hidden", []) as Array;
        if (off.indexOf(id) >= 0) {
            off.remove(id);
        } else if (areas().size() > 1) {
            off.add(id);  // at least one area stays
        } else {
            return false;
        }
        Application.Storage.setValue("hidden", off);
        return true;
    }

    function startArea() as Number {
        return Util.stored("start", 0) as Number;
    }

    // what the glance shows: 0 drinks, 1 supplements
    function glance() as Number {
        return Util.stored("glance_mode", 0) as Number;
    }
}
