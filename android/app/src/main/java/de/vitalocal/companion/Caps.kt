package de.vitalocal.companion

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject

/** What the server offers (`GET /api/v1/capabilities`): areas to show, AI features, setup state.
 *  Kept on the phone so the tabs are right before the server answered (and offline). */
data class Capabilities(
    val modules: Map<String, Boolean> = emptyMap(),
    val ai: Set<String> = emptySet(),
    val setupDone: Boolean = true,
    val hasWatchData: Boolean = true,
    val units: String = "metric",
    /** Language from the profile; null from older servers or before the setup asked for it. */
    val lang: String? = null,
) {
    /** Unknown areas (older server) count as shown. */
    fun on(module: String): Boolean = modules[module] != false

    companion object {
        fun parse(o: JSONObject) = Capabilities(
            modules = o.optJSONObject("modules")?.let { m -> m.keys().asSequence().associateWith { m.optBoolean(it, true) } } ?: emptyMap(),
            ai = o.optJSONObject("ai")?.optJSONObject("features")?.let { f -> f.keys().asSequence().filter { f.optBoolean(it) }.toSet() } ?: emptySet(),
            setupDone = o.optBoolean("setup_done", true),
            hasWatchData = o.optBoolean("has_watch_data", true),
            units = o.optString("units", "metric"),
            // the profile's default is German: only a language the setup asked for counts
            lang = o.optStringOrNull("lang")?.takeIf { o.optBoolean("setup_done", true) },
        )
    }
}

object Caps {
    private const val PREFS = "vitalocal.caps"
    private val _state = MutableStateFlow(Capabilities())
    val state: StateFlow<Capabilities> = _state.asStateFlow()

    fun load(context: Context) {
        val json = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("json", null) ?: return
        runCatching { _state.value = Capabilities.parse(JSONObject(json)) }
    }

    fun storedLang(context: Context): String? = runCatching {
        Capabilities.parse(JSONObject(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("json", null)!!)).lang
    }.getOrNull()

    /** Blocking: call off the main thread. An older server without the endpoint keeps everything shown. */
    fun refresh(context: Context, api: Api) {
        val json = runCatching { api.capabilities() }.getOrNull() ?: return
        _state.value = Capabilities.parse(json)
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("json", json.toString()).apply()
    }
}

/** Body weight in the profile's unit: stored in kg, shown and entered in lb when imperial. */
object BodyUnits {
    private const val LB = 0.45359237
    val imperial: Boolean get() = Caps.state.value.units == "imperial"
    val unit: String get() = if (imperial) "lb" else "kg"
    fun shown(kg: Double): Double = if (imperial) kg / LB else kg
    fun toKg(v: Double): Double = if (imperial) v * LB else v
}
