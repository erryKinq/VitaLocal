package de.vitalocal.companion

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.IOException

/**
 * One day's journal text: the server's last known [text] and [version], plus an unsaved [draft]
 * written on the phone over version [base]. A draft over an older version than the server's is a
 * conflict: the text was changed elsewhere (WebUI, an agent) since the draft started.
 */
data class JournalDay(val text: String = "", val version: Int = 0, val draft: String? = null, val base: Int = 0, val loaded: Boolean = false) {
    val shown: String get() = draft ?: text
    val conflict: Boolean get() = draft != null && base != version
    val unsaved: Boolean get() = draft != null && draft != text

    fun toJson(): JSONObject = JSONObject().put("text", text).put("version", version).put("base", base).put("loaded", loaded)
        .put("draft", draft ?: JSONObject.NULL)

    companion object {
        fun parse(o: JSONObject) = JournalDay(o.optString("text"), o.optInt("version"),
            o.optStringOrNull("draft"), o.optInt("base"), o.optBoolean("loaded"))
    }
}

data class JournalState(val days: Map<String, JournalDay> = emptyMap(), val busy: Boolean = false, val error: String? = null) {
    fun day(day: String): JournalDay = days[day] ?: JournalDay()
}

/**
 * The journal tab's texts for the last week. Drafts stay on the phone (`files/journal.json`) until
 * they are saved; offline drafts are sent with the next refresh. Saving is compare-and-swap on the
 * version (`PUT /journal/{day}`), so a text changed elsewhere is never overwritten unseen.
 */
object JournalRepo {
    private const val KEEP_DAYS = 8
    private val _state = MutableStateFlow(JournalState())
    val state: StateFlow<JournalState> = _state.asStateFlow()
    private val net = Mutex()
    private var loaded = false

    private fun file(context: Context) = File(context.filesDir, "journal.json")

    @Synchronized
    private fun init(context: Context) {
        if (loaded) return
        loaded = true
        val days = runCatching {
            val o = JSONObject(file(context).readText())
            o.keys().asSequence().associateWith { JournalDay.parse(o.getJSONObject(it)) }
        }.getOrDefault(emptyMap())
        _state.value = JournalState(days)
    }

    /** A keystroke: the text becomes (or stays) a draft over the version it started from. */
    fun edit(context: Context, day: String, text: String) {
        init(context)
        update(context, day) { d -> if (d.draft == null && text == d.text) d else d.copy(draft = text, base = if (d.draft == null) d.version else d.base) }
    }

    /** Conflict: drop the draft and take the server's text. */
    fun discardDraft(context: Context, day: String) = update(context, day) { it.copy(draft = null) }

    /** Conflict: keep the draft and save it over the server's current version. */
    suspend fun keepDraft(context: Context, day: String) {
        update(context, day) { it.copy(base = it.version) }
        save(context, day)
    }

    /** Loads [day] from the server; a draft over the current version is sent along. */
    suspend fun refresh(context: Context, day: String) {
        init(context)
        val settings = Settings(context)
        if (!settings.isConfigured) return
        _state.value = _state.value.copy(busy = true)
        try {
            net.withLock {
                withContext(Dispatchers.IO) {
                    val api = Api(settings.serverUrl, settings.token)
                    // drafts of other days written offline go out first
                    _state.value.days.filter { (k, d) -> k != day && d.unsaved && !d.conflict }.keys.forEach { k -> runCatching { send(context, api, k) } }
                    val o = api.journalDay(day)
                    update(context, day) { d ->
                        val server = d.copy(text = o.optString("text"), version = o.optInt("version"), loaded = true)
                        if (server.draft == server.text) server.copy(draft = null) else server
                    }
                    if (_state.value.day(day).let { it.unsaved && !it.conflict }) send(context, api, day)
                }
            }
            _state.value = _state.value.copy(error = null)
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = if (e is IOException && e !is ApiException) tr("Server nicht erreichbar – der Entwurf bleibt auf dem Handy") else e.describe())
        } finally {
            _state.value = _state.value.copy(busy = false)
        }
    }

    suspend fun save(context: Context, day: String) {
        val settings = Settings(context)
        if (!settings.isConfigured) return
        _state.value = _state.value.copy(busy = true)
        try {
            net.withLock { withContext(Dispatchers.IO) { send(context, Api(settings.serverUrl, settings.token), day) } }
            _state.value = _state.value.copy(error = null)
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = if (e is IOException && e !is ApiException) tr("Server nicht erreichbar – der Entwurf bleibt auf dem Handy") else e.describe())
        } finally {
            _state.value = _state.value.copy(busy = false)
        }
    }

    /** PUT of the draft; on 409 the server's text is stored next to the draft, which makes it a conflict. */
    private fun send(context: Context, api: Api, day: String) {
        val d = _state.value.day(day)
        val draft = d.draft ?: return
        try {
            val o = api.putJournalDay(day, draft, d.base)
            update(context, day) { cur ->
                val saved = cur.copy(text = o.optString("text"), version = o.optInt("version"), loaded = true)
                // typed on while the request was out: that rest stays a draft over the new version
                if (cur.draft == draft) saved.copy(draft = null) else saved.copy(base = saved.version)
            }
        } catch (e: StaleTextException) {
            update(context, day) { it.copy(text = e.current.optString("text"), version = e.current.optInt("version")) }
        }
    }

    @Synchronized
    private fun update(context: Context, day: String, f: (JournalDay) -> JournalDay) {
        val days = _state.value.days.toMutableMap()
        days[day] = f(days[day] ?: JournalDay())
        // keep the last week, and every unsaved draft
        days.keys.sorted().dropLast(KEEP_DAYS).forEach { k -> if (days[k]?.draft == null) days.remove(k) }
        _state.value = _state.value.copy(days = days)
        val o = JSONObject().apply { days.forEach { (k, v) -> put(k, v.toJson()) } }
        runCatching {
            val tmp = File(context.filesDir, "journal.json.tmp")
            tmp.writeText(o.toString())
            tmp.renameTo(file(context))
        }
    }
}
