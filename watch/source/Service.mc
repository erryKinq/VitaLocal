// Background sync every 15 minutes (also while the app is closed): fetches the day and keeps the
// glance's summary current. Runs with 64 KB, so it passes on only the summary.

import Toybox.Application;
import Toybox.Background;
import Toybox.Communications;
import Toybox.Lang;
import Toybox.System;

(:background)
class SyncService extends System.ServiceDelegate {
    function initialize() {
        ServiceDelegate.initialize();
    }

    function onTemporalEvent() as Void {
        Communications.makeWebRequest(API_BASE + "/api/v1/watch/today", null, {
            :method => Communications.HTTP_REQUEST_METHOD_GET,
            :headers => {"Authorization" => "Bearer " + API_TOKEN},
            :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
        }, method(:onDone));
    }

    function onDone(code as Number, data as Dictionary or String or Null) as Void {
        if (code == 200 && data instanceof Dictionary) {
            var summary = Summary.of(data);
            try {
                Application.Storage.setValue("glance", summary);
            } catch (e) {
                // older firmware: the app stores it in onBackgroundData
            }
            Background.exit(summary);
        } else {
            Background.exit(null);
        }
    }
}
