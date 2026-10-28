package de.vitalocal.companion

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** One step of a live cycle as shown on the Sync tab. */
data class LiveStep(val name: String, val outcome: String, val ms: Long, val ok: Boolean = true)

/** The last cycle: when, why and how each step went. */
data class CycleRecord(val startedAt: Long, val trigger: String, val steps: List<LiveStep>, val ok: Boolean, val message: String)

data class LiveData(
    val nextAt: Long = 0,
    val last: CycleRecord? = null,
    val lastSuccessAt: Long = 0,
    val lastUploadAt: Long = 0,
    /** How long Gadgetbridge took to report a finished sync, last cycles (ms). */
    val syncDurations: List<Long> = emptyList(),
    /** "Sync finished" arrived at least once: its absence then means "nothing new". */
    val finishWorks: Boolean = false,
    val watchSilent: Int = 0,
    val gbSilent: Int = 0,
    val backoff: Int = 1,
    /** (start ms, duration ms) of the foreground service, last 24 h. */
    val runtime: List<Pair<Long, Long>> = emptyList(),
    /** The day's foreground-service budget is used up: no cycles until the app is opened. */
    val paused: Boolean = false,
    val reportedError: String? = null,
    /** A ZIP export was asked for at this time and is not picked up yet (0 = none). */
    val zipRequestedAt: Long = 0,
    val lastZipRequestAt: Long = 0,
    val lastZipAt: Long = 0,
)

/** Live sync state, persisted in `files/live.json` (the store keeps only state changes in its log). */
object LiveState {
    private lateinit var file: File
    private val _state = MutableStateFlow(LiveData())
    val state: StateFlow<LiveData> = _state.asStateFlow()

    @Synchronized
    fun init(context: Context) {
        if (::file.isInitialized) return
        file = File(context.filesDir, "live.json")
        runCatching { _state.value = fromJson(JSONObject(file.readText())) }
    }

    @Synchronized
    fun update(change: (LiveData) -> LiveData) {
        _state.value = change(_state.value)
        if (::file.isInitialized) {
            val tmp = File(file.parentFile, "live.json.tmp")
            tmp.writeText(toJson(_state.value).toString())
            if (!tmp.renameTo(file)) {
                file.delete()
                tmp.renameTo(file)
            }
        }
    }

    fun recordRuntime(start: Long, ms: Long) = update { s ->
        val now = System.currentTimeMillis()
        val runtime = (s.runtime + (start to ms)).filter { it.first > now - 86_400_000L }
        val paused = LiveSchedule.budgetFactor(LiveSchedule.usedLast24h(runtime, now)) == 0
        s.copy(runtime = runtime, paused = s.paused || paused)
    }

    /** The app was opened: Android's foreground-service budget starts over. */
    fun resume() = update { it.copy(paused = false, runtime = emptyList()) }

    internal fun toJson(s: LiveData): JSONObject = JSONObject()
        .put("nextAt", s.nextAt)
        .put("lastSuccessAt", s.lastSuccessAt)
        .put("lastUploadAt", s.lastUploadAt)
        .put("syncDurations", JSONArray(s.syncDurations))
        .put("finishWorks", s.finishWorks)
        .put("watchSilent", s.watchSilent)
        .put("gbSilent", s.gbSilent)
        .put("backoff", s.backoff)
        .put("runtime", JSONArray().apply { s.runtime.forEach { put(JSONArray().put(it.first).put(it.second)) } })
        .put("paused", s.paused)
        .put("reportedError", s.reportedError ?: JSONObject.NULL)
        .put("zipRequestedAt", s.zipRequestedAt)
        .put("lastZipRequestAt", s.lastZipRequestAt)
        .put("lastZipAt", s.lastZipAt)
        .put("last", s.last?.let { c ->
            JSONObject().put("startedAt", c.startedAt).put("trigger", c.trigger).put("ok", c.ok).put("message", c.message)
                .put("steps", JSONArray().apply {
                    c.steps.forEach { put(JSONObject().put("name", it.name).put("outcome", it.outcome).put("ms", it.ms).put("ok", it.ok)) }
                })
        } ?: JSONObject.NULL)

    internal fun fromJson(o: JSONObject): LiveData {
        fun longs(a: JSONArray?) = List(a?.length() ?: 0) { a!!.getLong(it) }
        val rt = o.optJSONArray("runtime")
        val last = o.optJSONObject("last")?.let { c ->
            val steps = c.optJSONArray("steps")
            CycleRecord(
                c.getLong("startedAt"), c.optString("trigger"),
                List(steps?.length() ?: 0) { i ->
                    val st = steps!!.getJSONObject(i)
                    LiveStep(st.getString("name"), st.getString("outcome"), st.getLong("ms"), st.optBoolean("ok", true))
                },
                c.optBoolean("ok"), c.optString("message"),
            )
        }
        return LiveData(
            nextAt = o.optLong("nextAt"),
            last = last,
            lastSuccessAt = o.optLong("lastSuccessAt"),
            lastUploadAt = o.optLong("lastUploadAt"),
            syncDurations = longs(o.optJSONArray("syncDurations")),
            finishWorks = o.optBoolean("finishWorks"),
            watchSilent = o.optInt("watchSilent"),
            gbSilent = o.optInt("gbSilent"),
            backoff = o.optInt("backoff", 1).coerceAtLeast(1),
            runtime = List(rt?.length() ?: 0) { rt!!.getJSONArray(it).let { p -> p.getLong(0) to p.getLong(1) } },
            paused = o.optBoolean("paused"),
            reportedError = o.optString("reportedError").takeIf { it.isNotEmpty() && it != "null" },
            zipRequestedAt = o.optLong("zipRequestedAt"),
            lastZipRequestAt = o.optLong("lastZipRequestAt"),
            lastZipAt = o.optLong("lastZipAt"),
        )
    }
}
