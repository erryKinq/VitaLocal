// Talks to VitaLocal through Gadgetbridge (Internet Helper): reads /watch/today, writes through an
// offline queue. Writes carry ids generated here, so a request replayed after a lost answer is a
// no-op on the server. The day is kept in Storage so the app opens with the last known state.

import Toybox.Application;
import Toybox.Communications;
import Toybox.Lang;
import Toybox.WatchUi;

class Api {
    var today as Dictionary or Null;
    var status as String = "";
    var onChange as Method or Null;
    private var _busy as Boolean = false;

    function initialize() {
        today = Application.Storage.getValue("today") as Dictionary or Null;
    }

    function pending() as Number {
        return queue().size();
    }

    // A section of the day ("drinks", "supplements", ...) or null before the first sync
    function part(key as String) {
        return today == null ? null : today[key];
    }

    // Queue a write and send it (and anything still waiting).
    function enqueue(verb as Number, path as String, body as Dictionary or Null) as Void {
        var q = queue();
        q.add({"v" => verb, "p" => path, "b" => body});
        Application.Storage.setValue("q", q);
        keep();
        sync();
    }

    // Send the queue, then reload the day.
    function sync() as Void {
        if (_busy) {
            return;
        }
        var q = queue();
        if (q.size() == 0) {
            fetch();
            return;
        }
        var op = q[0] as Dictionary;
        _busy = true;
        Communications.makeWebRequest(API_BASE + op["p"], op["b"], {
            :method => op["v"],
            :headers => authHeaders(op["b"] != null),
            :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
        }, method(:onWritten));
    }

    function onWritten(code as Number, data as Dictionary or String or Null) as Void {
        _busy = false;
        var op = queue()[0] as Dictionary;
        // a DELETE answers 204 without a body, which Connect IQ may report as an unreadable body
        var deleted = op["v"] == Communications.HTTP_REQUEST_METHOD_DELETE && code == Communications.INVALID_HTTP_BODY_IN_NETWORK_RESPONSE;
        if (code >= 200 && code < 300 || deleted || code == 404 || code == 409 || code == 422) {
            // done, or rejected for good (deleted on the phone meanwhile, invalid): drop it
            Application.Storage.setValue("q", queue().slice(1, null));
            status = code >= 400 ? "Abgelehnt (HTTP " + code + ")" : "";
            sync();
        } else {
            status = describe(code);
            changed();
        }
    }

    private function fetch() as Void {
        _busy = true;
        Communications.makeWebRequest(API_BASE + "/api/v1/watch/today", null, {
            :method => Communications.HTTP_REQUEST_METHOD_GET,
            :headers => authHeaders(false),
            :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
        }, method(:onToday));
    }

    function onToday(code as Number, data as Dictionary or String or Null) as Void {
        _busy = false;
        if (pending() > 0) {
            // tapped while the day was loading: this day lacks those writes and would undo what
            // the watch shows; send them first, the reload after them brings the right day
            sync();
            return;
        }
        if (code == 200 && data instanceof Dictionary) {
            today = data;
            keep();
            status = "";
        } else {
            status = describe(code);
        }
        changed();
    }

    // Persist the day and the glance's summary of it
    function keep() as Void {
        Application.Storage.setValue("today", today);
        if (today != null) {
            Application.Storage.setValue("glance", Summary.of(today));
        }
    }

    function changed() as Void {
        if (onChange != null) {
            onChange.invoke();
        }
        WatchUi.requestUpdate();
    }

    private function queue() as Array {
        var q = Application.Storage.getValue("q");
        return q instanceof Array ? q : [];
    }

    private function authHeaders(json as Boolean) as Dictionary {
        var h = {"Authorization" => "Bearer " + API_TOKEN};
        if (json) {
            h["Content-Type"] = Communications.REQUEST_CONTENT_TYPE_JSON;
        }
        return h;
    }

    private function describe(code as Number) as String {
        if (code == 401) { return "Token ungültig"; }
        if (code == 0) { return "Gadgetbridge blockt"; }
        if (code == Communications.BLE_CONNECTION_UNAVAILABLE) { return "Telefon nicht verbunden"; }
        if (code == Communications.NETWORK_REQUEST_TIMED_OUT || code == Communications.BLE_HOST_TIMEOUT) {
            return "Zeitüberschreitung";
        }
        return "Fehler " + code;
    }
}

// What the glance shows, small enough for the background's exit data
(:background :glance)
module Summary {
    function of(today as Dictionary) as Dictionary {
        var d = today["drinks"] as Dictionary or Null;
        var sups = today["supplements"] as Array or Null;
        var done = 0;
        var n = 0;
        if (sups != null) {
            for (var i = 0; i < sups.size(); i++) {
                var s = sups[i] as Dictionary;
                if (s["rest"] != true || Util.num(s["count"]) > 0) {
                    n++;
                    if (Util.num(s["count"]) >= Util.num(s["per_day"])) {
                        done++;
                    }
                }
            }
        }
        return {
            "day" => today["day"],
            "ml" => d == null ? 0 : Util.num(d["ml"]),
            "goal" => d == null ? null : d["goal_ml"],
            "sup_done" => done,
            "sup_n" => n
        };
    }
}
