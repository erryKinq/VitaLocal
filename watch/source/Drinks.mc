// Choosing a drink and amount (own drink, cup settings): kind menu, then amount menu, then
// `done.invoke(kind index, ml)`.

import Toybox.Lang;
import Toybox.WatchUi;

function chooseDrink(title as String, done as Method) as Void {
    var menu = new WatchUi.Menu2({:title => title});
    for (var i = 0; i < DRINK_KINDS.size(); i++) {
        menu.addItem(new WatchUi.MenuItem((DRINK_KINDS[i] as Array)[0] as String, null, i, null));
    }
    WatchUi.pushView(menu, new DrinkKindDelegate(done), WatchUi.SLIDE_LEFT);
}

class DrinkKindDelegate extends WatchUi.Menu2InputDelegate {
    private var _done as Method;

    function initialize(done as Method) {
        Menu2InputDelegate.initialize();
        _done = done;
    }

    function onSelect(item as WatchUi.MenuItem) as Void {
        var kind = item.getId() as Number;
        var menu = new WatchUi.Menu2({:title => (DRINK_KINDS[kind] as Array)[0] as String});
        for (var i = 0; i < DRINK_AMOUNTS.size(); i++) {
            menu.addItem(new WatchUi.MenuItem(DRINK_AMOUNTS[i] + " ml", null, DRINK_AMOUNTS[i], null));
        }
        WatchUi.pushView(menu, new DrinkAmountDelegate(kind, _done), WatchUi.SLIDE_LEFT);
    }
}

class DrinkAmountDelegate extends WatchUi.Menu2InputDelegate {
    private var _kind as Number;
    private var _done as Method;

    function initialize(kind as Number, done as Method) {
        Menu2InputDelegate.initialize();
        _kind = kind;
        _done = done;
    }

    function onSelect(item as WatchUi.MenuItem) as Void {
        WatchUi.popView(WatchUi.SLIDE_IMMEDIATE);
        WatchUi.popView(WatchUi.SLIDE_RIGHT);
        _done.invoke(_kind, item.getId() as Number);
    }
}
