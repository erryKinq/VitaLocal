package de.vitalocal.companion

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

enum class Source(val key: String, val title: String) {
    GADGETBRIDGE("gadgetbridge", "Gadgetbridge"),
    SHARED("shared", tr("Geteilte Dateien")),
    HEALTH_CONNECT("health_connect", "Health Connect"),
}

enum class Level { INFO, WARN, ERROR }

data class LogEntry(val ts: Long, val level: Level, val source: Source?, val message: String)

/** Last known state per source, shown on the status screen. */
data class SourceState(
    val lastCheck: Long = 0,
    val lastFile: String? = null,
    val lastUpload: Long = 0,
    val batchId: Long? = null,
    val status: String? = null,
    val error: String? = null,
)

/** A local file whose hash is known, so unchanged files are not read again. */
data class IndexEntry(val size: Long, val modified: Long, val sha256: String)

data class AppState(
    val sources: Map<Source, SourceState> = emptyMap(),
    val log: List<LogEntry> = emptyList(),
    val lastServerCheck: Long = 0,
    val serverReachable: Boolean? = null,
)

/**
 * App state persisted as one small JSON file. Workers and UI share the process, so the UI
 * observes [state] directly.
 */
object Store {
    private const val MAX_LOG = 200
    private const val MAX_INDEX = 100
    private lateinit var file: File
    private val index = LinkedHashMap<String, IndexEntry>()
    private val _state = MutableStateFlow(AppState())
    val state: StateFlow<AppState> = _state.asStateFlow()

    @Synchronized
    fun init(context: Context) {
        if (::file.isInitialized) return
        file = File(context.filesDir, "state.json")
        runCatching { load(JSONObject(file.readText())) }
    }

    @Synchronized
    fun log(level: Level, source: Source?, message: String) {
        val entry = LogEntry(System.currentTimeMillis(), level, source, message)
        _state.value = _state.value.copy(log = (listOf(entry) + _state.value.log).take(MAX_LOG))
        save()
    }

    @Synchronized
    fun updateSource(source: Source, f: (SourceState) -> SourceState) {
        val current = _state.value.sources[source] ?: SourceState()
        _state.value = _state.value.copy(sources = _state.value.sources + (source to f(current)))
        save()
    }

    @Synchronized
    fun serverChecked(reachable: Boolean) {
        _state.value = _state.value.copy(lastServerCheck = System.currentTimeMillis(), serverReachable = reachable)
        save()
    }

    @Synchronized
    fun indexed(key: String, size: Long, modified: Long): String? =
        index[key]?.takeIf { it.size == size && it.modified == modified }?.sha256

    @Synchronized
    fun putIndex(key: String, entry: IndexEntry) {
        index.remove(key)
        index[key] = entry
        while (index.size > MAX_INDEX) index.remove(index.keys.first())
        save()
    }

    @Synchronized
    fun clearLog() {
        _state.value = _state.value.copy(log = emptyList())
        save()
    }

    private fun save() {
        val s = _state.value
        val o = JSONObject()
            .put("server_checked", s.lastServerCheck)
            .put("server_reachable", s.serverReachable ?: JSONObject.NULL)
            .put("sources", JSONObject().apply {
                s.sources.forEach { (src, st) ->
                    put(src.key, JSONObject()
                        .put("last_check", st.lastCheck)
                        .put("last_file", st.lastFile ?: JSONObject.NULL)
                        .put("last_upload", st.lastUpload)
                        .put("batch_id", st.batchId ?: JSONObject.NULL)
                        .put("status", st.status ?: JSONObject.NULL)
                        .put("error", st.error ?: JSONObject.NULL))
                }
            })
            .put("log", JSONArray().apply {
                s.log.forEach {
                    put(JSONObject().put("ts", it.ts).put("level", it.level.name)
                        .put("source", it.source?.key ?: JSONObject.NULL).put("msg", it.message))
                }
            })
            .put("index", JSONObject().apply {
                index.forEach { (k, v) ->
                    put(k, JSONObject().put("size", v.size).put("modified", v.modified).put("sha256", v.sha256))
                }
            })
        val tmp = File(file.parentFile, "state.json.tmp")
        tmp.writeText(o.toString())
        tmp.renameTo(file)
    }

    private fun load(o: JSONObject) {
        val sources = mutableMapOf<Source, SourceState>()
        o.optJSONObject("sources")?.let { so ->
            Source.entries.forEach { src ->
                so.optJSONObject(src.key)?.let {
                    sources[src] = SourceState(
                        lastCheck = it.optLong("last_check"),
                        lastFile = it.optStringOrNull("last_file"),
                        lastUpload = it.optLong("last_upload"),
                        batchId = if (it.isNull("batch_id")) null else it.optLong("batch_id"),
                        status = it.optStringOrNull("status"),
                        error = it.optStringOrNull("error"),
                    )
                }
            }
        }
        val log = o.optJSONArray("log")?.let { arr ->
            List(arr.length()) { i ->
                val e = arr.getJSONObject(i)
                LogEntry(
                    e.getLong("ts"),
                    Level.valueOf(e.getString("level")),
                    e.optStringOrNull("source")?.let { k -> Source.entries.firstOrNull { it.key == k } },
                    e.getString("msg"),
                )
            }
        } ?: emptyList()
        o.optJSONObject("index")?.let { io ->
            io.keys().forEach { k ->
                val e = io.getJSONObject(k)
                index[k] = IndexEntry(e.getLong("size"), e.getLong("modified"), e.getString("sha256"))
            }
        }
        _state.value = AppState(
            sources = sources,
            log = log,
            lastServerCheck = o.optLong("server_checked"),
            serverReachable = if (o.isNull("server_reachable")) null else o.optBoolean("server_reachable"),
        )
    }
}


/** Exception text for the log and the UI, also through coroutine wrapping. */
fun Throwable.describe(): String = when (this) {
    is kotlinx.coroutines.TimeoutCancellationException -> tr("Zeitüberschreitung")
    else -> "${javaClass.simpleName}: ${message ?: cause?.message ?: tr("ohne Meldung")}"
}
