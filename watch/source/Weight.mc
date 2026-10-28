import Toybox.Communications;
import Toybox.Graphics;
import Toybox.Lang;
import Toybox.WatchUi;

// One picker column: whole numbers from `first` to `last`, or digits after the comma.
class RangeFactory extends WatchUi.PickerFactory {
    private var _first as Number;
    private var _last as Number;
    private var _prefix as String;

    function initialize(first as Number, last as Number, prefix as String) {
        PickerFactory.initialize();
        _first = first;
        _last = last;
        _prefix = prefix;
    }

    function getSize() as Number {
        return _last - _first + 1;
    }

    function getValue(index as Number) as Object? {
        return _first + index;
    }

    function getDrawable(index as Number, selected as Boolean) as WatchUi.Drawable? {
        return new WatchUi.Text({
            :text => _prefix + (_first + index),
            :color => Graphics.COLOR_WHITE,
            :font => Graphics.FONT_NUMBER_MEDIUM,
            :locX => WatchUi.LAYOUT_HALIGN_CENTER,
            :locY => WatchUi.LAYOUT_VALIGN_CENTER
        });
    }
}

class WeightPicker extends WatchUi.Picker {
    function initialize(kg as Float) {
        var whole = kg.toNumber();
        var tenth = ((kg - whole) * 10 + 0.5).toNumber() % 10;
        Picker.initialize({
            :title => new WatchUi.Text({:text => "Gewicht (kg)", :font => Graphics.FONT_SMALL,
                :locX => WatchUi.LAYOUT_HALIGN_CENTER, :locY => WatchUi.LAYOUT_VALIGN_BOTTOM,
                :color => Graphics.COLOR_WHITE}),
            :pattern => [new RangeFactory(30, 200, ""), new RangeFactory(0, 9, ",")],
            :defaults => [whole - 30, tenth]
        });
    }
}

class WeightDelegate extends WatchUi.PickerDelegate {
    function initialize() {
        PickerDelegate.initialize();
    }

    function onAccept(values as Array) as Boolean {
        var kg = (values[0] as Number) + (values[1] as Number) / 10.0;
        var today = getApi().today;
        if (today != null) {
            var wt = today["weight"] as Dictionary or Null;
            if (wt == null) {
                today["weight"] = {"kg" => kg, "days" => [[0, kg]]};
            } else {
                wt["kg"] = kg;
            }
        }
        getApi().enqueue(Communications.HTTP_REQUEST_METHOD_POST, "/api/v1/body", {
            "id" => Util.newId(), "ts" => Util.now(), "metric" => "weight", "value" => kg, "source" => "watch"
        });
        WatchUi.popView(WatchUi.SLIDE_RIGHT);
        WatchUi.showToast(Util.dec(kg, 1) + " kg gespeichert", null);
        return true;
    }

    function onCancel() as Boolean {
        WatchUi.popView(WatchUi.SLIDE_RIGHT);
        return true;
    }
}
