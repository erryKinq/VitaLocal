// Settings behind the bar at the right edge: the three cups, areas, start area, glance, sync.

import Toybox.Application;
import Toybox.Lang;
import Toybox.WatchUi;

function openSettings() as Void {
    var menu = new WatchUi.Menu2({:title => "Einstellungen"});
    for (var i = 0; i < 3; i++) {
        menu.addItem(new WatchUi.MenuItem("Becher " + (i + 1), Prefs.cupLabel(i), i, null));
    }
    menu.addItem(new WatchUi.MenuItem("Bereiche", areasLabel(), :areas, null));
    menu.addItem(new WatchUi.MenuItem("Startbereich", AREA_NAMES[Prefs.startArea()] as String, :start, null));
    menu.addItem(new WatchUi.MenuItem("Glance zeigt", Prefs.glance() == 0 ? "Trinken" : "Supplements", :glance, null));
    var api = getApi();
    var st = api.status.length() > 0 ? api.status : (api.pending() > 0 ? api.pending() + " ausstehend" : "synchron");
    menu.addItem(new WatchUi.MenuItem("Synchronisieren", st, :sync, null));
    WatchUi.pushView(menu, new SettingsDelegate(), WatchUi.SLIDE_LEFT);
}

function areasLabel() as String {
    var ids = Prefs.areas();
    return ids.size() == AREA_NAMES.size() ? "alle" : ids.size() + " von " + AREA_NAMES.size();
}

class SettingsDelegate extends WatchUi.Menu2InputDelegate {
    function initialize() {
        Menu2InputDelegate.initialize();
    }

    function onSelect(item as WatchUi.MenuItem) as Void {
        var id = item.getId();
        if (id instanceof Number) {
            chooseDrink("Becher " + (id + 1), new CupSetter(id, item).method(:done));
        } else if (id == :areas) {
            var m = new WatchUi.Menu2({:title => "Bereiche"});
            var shown = Prefs.areas();
            for (var i = 0; i < AREA_NAMES.size(); i++) {
                m.addItem(new WatchUi.ToggleMenuItem(AREA_NAMES[i] as String, null, i, shown.indexOf(i) >= 0, null));
            }
            WatchUi.pushView(m, new AreasDelegate(item), WatchUi.SLIDE_LEFT);
        } else if (id == :start) {
            var m = new WatchUi.Menu2({:title => "Startbereich"});
            for (var i = 0; i < AREA_NAMES.size(); i++) {
                m.addItem(new WatchUi.MenuItem(AREA_NAMES[i] as String, null, i, null));
            }
            WatchUi.pushView(m, new ChoiceDelegate("start", item, AREA_NAMES), WatchUi.SLIDE_LEFT);
        } else if (id == :glance) {
            var m = new WatchUi.Menu2({:title => "Glance zeigt"});
            m.addItem(new WatchUi.MenuItem("Trinken", null, 0, null));
            m.addItem(new WatchUi.MenuItem("Supplements", null, 1, null));
            WatchUi.pushView(m, new ChoiceDelegate("glance_mode", item, ["Trinken", "Supplements"]), WatchUi.SLIDE_LEFT);
        } else if (id == :sync) {
            getApi().status = "";
            item.setSubLabel("lädt ...");
            getApi().sync();
        }
    }
}

class CupSetter {
    private var _i as Number;
    private var _item as WatchUi.MenuItem;

    function initialize(i as Number, item as WatchUi.MenuItem) {
        _i = i;
        _item = item;
    }

    function done(kind as Number, ml as Number) as Void {
        Prefs.setCup(_i, kind, ml);
        _item.setSubLabel(Prefs.cupLabel(_i));
    }
}

class AreasDelegate extends WatchUi.Menu2InputDelegate {
    private var _parent as WatchUi.MenuItem;

    function initialize(parent as WatchUi.MenuItem) {
        Menu2InputDelegate.initialize();
        _parent = parent;
    }

    function onSelect(item as WatchUi.MenuItem) as Void {
        if (!Prefs.toggleArea(item.getId() as Number)) {
            (item as WatchUi.ToggleMenuItem).setEnabled(true);  // the last area stays
            WatchUi.showToast("Ein Bereich bleibt", null);
        }
        _parent.setSubLabel(areasLabel());
    }
}

// Stores the chosen index under `key` and goes back
class ChoiceDelegate extends WatchUi.Menu2InputDelegate {
    private var _key as String;
    private var _parent as WatchUi.MenuItem;
    private var _names as Array;

    function initialize(key as String, parent as WatchUi.MenuItem, names as Array) {
        Menu2InputDelegate.initialize();
        _key = key;
        _parent = parent;
        _names = names;
    }

    function onSelect(item as WatchUi.MenuItem) as Void {
        var i = item.getId() as Number;
        Application.Storage.setValue(_key, i);
        _parent.setSubLabel(_names[i] as String);
        WatchUi.popView(WatchUi.SLIDE_RIGHT);
    }
}
