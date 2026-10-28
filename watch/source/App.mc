// VitaLocal on the Venu 4: drinks, supplements, habits and weight of the day, synced with
// VitaLocal through Gadgetbridge (Internet Helper), plus a glance and a background sync.
// API_BASE and API_TOKEN come from Secrets.mc (written by build.sh).

import Toybox.Application;
import Toybox.Background;
import Toybox.Lang;
import Toybox.Math;
import Toybox.System;
import Toybox.Time;
import Toybox.WatchUi;

const SYNC_MINUTES = 15;

(:background :glance)
class VitaLocalApp extends Application.AppBase {
    var api = null;  // Api, foreground only

    function initialize() {
        AppBase.initialize();
    }

    (:typecheck(disableBackgroundCheck))
    function getInitialView() as [WatchUi.Views] or [WatchUi.Views, WatchUi.InputDelegates] {
        Math.srand(System.getTimer());
        api = new Api();
        if (Background has :registerForTemporalEvent) {
            Background.registerForTemporalEvent(new Time.Duration(SYNC_MINUTES * 60));
        }
        var view = new PagerView();
        return [view, new PagerDelegate(view)];
    }

    (:glance)
    function getGlanceView() as [WatchUi.GlanceView] or [WatchUi.GlanceView, WatchUi.GlanceViewDelegate] or Null {
        return [new VitaGlance()];
    }

    function getServiceDelegate() as [System.ServiceDelegate] {
        return [new SyncService()];
    }

    function onBackgroundData(data as Application.PersistableType) as Void {
        if (data instanceof Dictionary) {
            glanceData = data;
            try {
                Application.Storage.setValue("glance", data);
            } catch (e) {
                // the glance may not write Storage; it shows glanceData meanwhile
            }
            WatchUi.requestUpdate();
        }
    }
}

(:typecheck(disableBackgroundCheck))
function getApi() as Api {
    return (Application.getApp() as VitaLocalApp).api as Api;
}
