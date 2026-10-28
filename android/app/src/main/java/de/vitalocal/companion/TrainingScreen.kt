package de.vitalocal.companion

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import de.vitalocal.companion.ui.CardShape
import de.vitalocal.companion.ui.Muted
import de.vitalocal.companion.ui.ScreenHeader
import de.vitalocal.companion.ui.VCard
import de.vitalocal.companion.ui.VIcon
import de.vitalocal.companion.ui.Vita
import de.vitalocal.companion.ui.WebLink

// --- model ------------------------------------------------------------------------------------------

/** One entry of a plan day: current target, the progression's suggestion (when it differs), last time. */
data class PlanEntry(
    val name: String, val current: String, val next: String?, val nextReason: String?, val last: String?, val lastRating: String?,
    val deload: Boolean = false,
)
/** The plan's deload: the week (until [until]) or a suggestion with its reasons. */
data class Deload(val active: Boolean, val until: String?, val suggested: Boolean, val reasons: List<String>)
/** The day of the active plan due next (shown while there is no workout today). */
data class NextDay(val plan: String, val day: String, val entries: List<PlanEntry>, val deload: Deload? = null)
/** [suggestion]: the rating the sets suggest against the plan ([planned]), with its reason. */
data class SessionExercise(
    val exerciseId: String, val planExerciseId: String?, val name: String, val text: String, val rating: String?,
    val planned: String? = null, val suggestion: String? = null, val why: String? = null,
)
/** One factor of the readiness: its value and the points it costs ([impact] ≤ 0). */
data class ReadinessPart(val key: String, val value: Double?, val unit: String?, val impact: Int)
/** VitaLocal's training readiness (null score: not enough data) and the watch's own value;
 *  [garminFactors]: the watch's rating per factor in % (100 = no limit), keyed like [parts]. */
data class Readiness(
    val score: Int?, val level: String?, val parts: List<ReadinessPart>, val garmin: Int?, val garminLevel: Int?,
    val garminFactors: Map<String, Int> = emptyMap(),
)
data class SessionView(
    val id: Long, val start: String, val title: String, val planDay: String?, val reviewed: Boolean,
    val unassigned: Int, val exercises: List<SessionExercise>,
)

/** A workout still contributing to a muscle's fatigue ([remaining] share left to recover). */
data class MuscleSession(val id: Long, val ts: String, val exercises: List<String>, val sets: Double, val remaining: Double, val provisional: Boolean)
/** Recovery of one muscle as the server estimates it ([state]: ready, recovering, fatigued). */
data class MuscleState(
    val key: String, val label: String, val fatigue: Double, val state: String, val readyInH: Double,
    val lastTrained: String?, val hardSets7d: Double, val sessions: List<MuscleSession>,
)
data class MuscleMap(val at: String, val muscles: List<MuscleState>, val garminRecoveryH: Double?)

internal fun parseMuscles(o: JSONObject?): MuscleMap? = o?.let {
    // entries with "alias" stand for the old coarse chest, shoulders and back (apps up to 1.7.0)
    MuscleMap(it.optString("at"), it.optJSONArray("muscles").objects().filter { m -> !m.optBoolean("alias") }.map { m ->
        MuscleState(m.getString("key"), m.optString("label"), m.optDouble("fatigue", 0.0), m.optString("state", "ready"),
            m.optDouble("ready_in_h", 0.0), m.optStringOrNull("last_trained"), m.optDouble("hard_sets_7d", 0.0),
            m.optJSONArray("sessions").objects().map { s ->
                MuscleSession(s.optLong("id"), s.optString("ts"), s.optJSONArray("exercises").let { a -> (0 until (a?.length() ?: 0)).map { i -> a!!.getString(i) } },
                    s.optDouble("sets", 0.0), s.optDouble("remaining", 0.0), s.optBoolean("provisional"))
            })
    }, if (it.isNull("garmin_recovery_h")) null else it.optDouble("garmin_recovery_h"))
}

data class TrainingState(
    /** Today's workout (the newest one, if it started today). */
    val current: SessionView? = null,
    val next: NextDay? = null,
    val readiness: Readiness? = null,
    val muscles: MuscleMap? = null,
    val loadedAt: Long = 0,
    val loading: Boolean = false,
    val error: String? = null,
    /** The skip just made, for undo. */
    val skipped: Pair<String, String>? = null,
)

private fun parseEntry(e: JSONObject): PlanEntry {
    val next = e.optJSONObject("next")
    val last = e.optJSONObject("last")
    return PlanEntry(
        e.getString("exercise"), e.getString("current"),
        if (e.optBoolean("differs") || e.optBoolean("deload")) next?.optStringOrNull("text") else null,
        next?.optStringOrNull("reason"),
        last?.optJSONArray("sets")?.let { a -> List(a.length()) { a.getString(it) }.joinToString(", ") },
        last?.optStringOrNull("rating"),
        e.optBoolean("deload"),
    )
}

/** `GET /training/next`. */
internal fun parseNext(o: JSONObject?): NextDay? = o?.let {
    NextDay(it.optString("plan"), it.optString("plan_day"), it.optJSONArray("exercises").objects().map(::parseEntry),
        it.optJSONObject("deload")?.let { d ->
            Deload(d.optBoolean("active"), d.optStringOrNull("until"), d.optBoolean("suggested"),
                d.optJSONArray("reasons").objects().map { r ->
                    when (r.optString("key")) {
                        "scheduled" -> tr("{0} Wochen seit dem letzten Deload", r.optInt("weeks"))
                        "stalls" -> tr("zweimal in Folge zu schwer: ") + r.optJSONArray("exercises").let { a -> List(a?.length() ?: 0) { a!!.getString(it) } }.joinToString(", ")
                        "hard_ratings" -> tr("{0} von {1} Bewertungen in zwei Wochen zu schwer", r.optInt("hard"), r.optInt("of"))
                        else -> r.optString("key")
                    }
                })
        })
}

/** `GET /readiness`. */
internal fun parseReadiness(o: JSONObject?): Readiness? = o?.let {
    val g = it.optJSONObject("garmin")
    Readiness(
        score = if (it.isNull("score")) null else it.optInt("score"),
        level = it.optStringOrNull("level"),
        parts = it.optJSONArray("components").objects().map { c ->
            ReadinessPart(c.getString("key"), if (c.isNull("value")) null else c.optDouble("value"), c.optStringOrNull("unit"),
                c.optInt("impact"))
        },
        garmin = g?.optInt("score"),
        garminLevel = g?.let { x -> if (x.isNull("level")) null else x.optInt("level") },
        garminFactors = g?.optJSONObject("factors")?.let { f -> f.keys().asSequence().associateWith { k -> f.optInt(k) } } ?: emptyMap(),
    )
}

/** Local calendar day of a session's start. */
internal fun sessionDay(s: SessionView, zone: ZoneId = ZoneId.systemDefault()): LocalDate? =
    runCatching { OffsetDateTime.parse(s.start).atZoneSameInstant(zone).toLocalDate() }.getOrNull()

internal fun parseSession(o: JSONObject): SessionView = SessionView(
    id = o.getLong("activity_id"),
    start = o.getJSONObject("activity").getString("start_ts"),
    title = o.optString("display_title", "Training"),
    planDay = o.optStringOrNull("plan_day"),
    reviewed = o.optBoolean("reviewed"),
    unassigned = o.optInt("unassigned_sets"),
    exercises = o.optJSONArray("exercises").objects().map {
        val sug = it.optJSONObject("suggestion")
        SessionExercise(it.getString("exercise_id"), it.optStringOrNull("plan_exercise_id"), it.optString("exercise", "?"),
            it.optString("text"), it.optStringOrNull("rating"), it.optStringOrNull("planned"),
            sug?.optStringOrNull("rating"), sug?.optStringOrNull("why"))
    },
)

/** The full rating state of a session with one exercise changed (tapping the chosen rating clears it). */
internal fun ratingsWith(s: SessionView, target: SessionExercise, rating: String): JSONArray = JSONArray().apply {
    s.exercises.forEach { e ->
        val r = if (e.exerciseId == target.exerciseId && e.planExerciseId == target.planExerciseId) {
            rating.takeIf { it != e.rating }
        } else e.rating
        if (r != null) put(JSONObject().put("exercise_id", e.exerciseId).put("plan_exercise_id", e.planExerciseId ?: JSONObject.NULL).put("rating", r))
    }
}

// --- repository ---------------------------------------------------------------------------------------

/**
 * Today's workout for rating (earlier ones are rated in the WebUI) or else the next plan day,
 * cached so the tab shows the last state offline.
 */
object TrainingRepo {
    private val _state = MutableStateFlow(TrainingState())
    val state: StateFlow<TrainingState> = _state.asStateFlow()
    private var cacheLoaded = false

    private fun file(context: Context) = File(context.filesDir, "training.json")

    private fun fromCache(context: Context) {
        if (cacheLoaded) return
        cacheLoaded = true
        runCatching {
            val o = JSONObject(file(context).readText())
            _state.value = _state.value.copy(
                current = o.optJSONObject("current")?.let(::parseSession)?.takeIf { sessionDay(it) == LocalDate.now() },
                next = parseNext(o.optJSONObject("next")),
                readiness = parseReadiness(o.optJSONObject("readiness")),
                muscles = parseMuscles(o.optJSONObject("muscles")),
                loadedAt = o.optLong("at"),
            )
        }
    }

    suspend fun load(context: Context) {
        fromCache(context)
        val settings = Settings(context)
        if (!settings.isConfigured) return
        _state.value = _state.value.copy(loading = true)
        try {
            withContext(Dispatchers.IO) {
                val api = Api(settings.serverUrl, settings.token)
                val next = api.nextWorkout()
                val readiness = runCatching { api.readiness() }.getOrNull()  // older servers: none
                val muscles = runCatching { api.muscleState() }.getOrNull()  // older servers: none
                runCatching { WorkoutRepo.refreshCatalog(context, api) }
                    .onFailure { Store.log(Level.WARN, null, tr("Trainingsdaten für das Training ohne Uhr nicht geladen: {0}", it.message)) }
                // the history mixes in skipped plan days (kind "skip", uuid id): only sessions have a workout
                val newest = api.trainingHistory(5).optJSONArray("items").objects().firstOrNull { it.optString("kind", "session") == "session" }
                val current = newest?.let { api.trainingSession(it.getLong("id")) }
                    ?.takeIf { sessionDay(parseSession(it)) == LocalDate.now() }
                val at = System.currentTimeMillis()
                runCatching {
                    file(context).writeText(JSONObject().put("current", current ?: JSONObject.NULL)
                        .put("next", next ?: JSONObject.NULL).put("readiness", readiness ?: JSONObject.NULL)
                        .put("muscles", muscles ?: JSONObject.NULL)
                        .put("at", at).toString())
                }
                _state.value = _state.value.copy(current = current?.let(::parseSession), next = parseNext(next),
                    readiness = parseReadiness(readiness), muscles = parseMuscles(muscles),
                    loadedAt = at, error = null)
            }
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = if (e is IOException && e !is ApiException) tr("Server nicht erreichbar") else e.describe())
        } finally {
            _state.value = _state.value.copy(loading = false)
        }
    }

    /** Skips the next plan day (needs the server); the rotation moves on, undo stays offered. */
    suspend fun skip(context: Context, note: String?) {
        val settings = Settings(context)
        val day = _state.value.next?.day ?: return
        try {
            val id = withContext(Dispatchers.IO) { Api(settings.serverUrl, settings.token).skipNext(note?.takeIf { it.isNotBlank() }) }
            _state.value = _state.value.copy(skipped = id to day, error = null)
            load(context)
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = tr("Nicht übersprungen: ") +
                if (e is IOException && e !is ApiException) tr("Server nicht erreichbar") else e.describe())
        }
    }

    suspend fun undoSkip(context: Context) {
        val (id, _) = _state.value.skipped ?: return
        val settings = Settings(context)
        try {
            withContext(Dispatchers.IO) { Api(settings.serverUrl, settings.token).undoSkip(id) }
            _state.value = _state.value.copy(skipped = null, error = null)
            load(context)
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = tr("Nicht rückgängig gemacht: ") +
                if (e is IOException && e !is ApiException) tr("Server nicht erreichbar") else e.describe())
        }
    }

    fun dismissSkip() { _state.value = _state.value.copy(skipped = null) }

    /** Starts ([start]) or ends the active plan's deload week (needs the server). */
    suspend fun deload(context: Context, start: Boolean) {
        val settings = Settings(context)
        try {
            withContext(Dispatchers.IO) { Api(settings.serverUrl, settings.token).deload(start) }
            load(context)
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = tr("Deload nicht geändert: ") +
                if (e is IOException && e !is ApiException) tr("Server nicht erreichbar") else e.describe())
        }
    }

    /** Saves a rating right away (needs the server); the plan's suggestions follow from it. */
    suspend fun rate(context: Context, session: SessionView, exercise: SessionExercise, rating: String) {
        val settings = Settings(context)
        try {
            val updated = withContext(Dispatchers.IO) {
                Api(settings.serverUrl, settings.token).putRatings(session.id, ratingsWith(session, exercise, rating))
            }
            _state.value = _state.value.copy(current = parseSession(updated), error = null)
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = tr("Bewertung nicht gespeichert: ") +
                if (e is IOException && e !is ApiException) tr("Server nicht erreichbar") else e.describe())
        }
    }
}

// --- UI -------------------------------------------------------------------------------------------------

private val RATINGS = listOf("easy" to tr("zu leicht"), "good" to tr("gut"), "hard" to tr("zu schwer"))
private fun ratingText(r: String?) = RATINGS.firstOrNull { it.first == r }?.second
private val WHY = mapOf(
    "fewer sets" to tr("weniger Sätze als geplant"), "target missed" to tr("Ziel nicht erreicht"),
    "lighter weight" to tr("leichter als geplant"), "target met" to tr("Ziel erreicht"), "target exceeded" to tr("Ziel deutlich übertroffen"),
)
// Garmin's bands: poor 0–24, low 25–49, moderate 50–74, high 75–94, prime 95–100
private val LEVELS = mapOf("prime" to tr("Optimal"), "high" to tr("Hoch"), "moderate" to tr("Mäßig"), "low" to tr("Niedrig"), "poor" to tr("Schlecht"))
private val GARMIN_LEVELS = mapOf(1 to tr("schlecht"), 2 to tr("niedrig"), 3 to tr("mäßig"), 4 to tr("hoch"), 5 to tr("optimal"))

@Composable
fun TrainingScreen() {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    val state by TrainingRepo.state.collectAsState()
    var asking by remember { mutableStateOf(false) }
    LifecycleResumeEffect(Unit) {
        scope.launch { TrainingRepo.load(ctx) }
        onPauseOrDispose { }
    }
    PullToRefreshBox(isRefreshing = state.loading, onRefresh = { scope.launch { TrainingRepo.load(ctx) } }) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            ScreenHeader(tr("Training"), if (state.loadedAt > 0) tr("Stand {0}", formatTime(state.loadedAt)) else null, R.drawable.ic_t_barbell, Vita.colors.train)
            state.error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
            state.skipped?.let { (_, day) ->
                Card(Modifier.fillMaxWidth(), shape = CardShape, colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer)) {
                    Row(Modifier.padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        VIcon(R.drawable.ic_t_player_skip_forward, size = 18.dp)
                        Text(tr("  {0} übersprungen", day), Modifier.weight(1f))
                        TextButton(onClick = { scope.launch { TrainingRepo.undoSkip(ctx) } }) { Text(tr("Rückgängig")) }
                        IconButton(onClick = { TrainingRepo.dismissSkip() }) { VIcon(R.drawable.ic_t_x, size = 18.dp) }
                    }
                }
            }
            val workout by WorkoutRepo.draft.collectAsState()
            // a workout just finished (or discarded): send it and show the updated plan and stats
            var hadWorkout by remember { mutableStateOf(false) }
            LaunchedEffect(workout == null) {
                if (workout != null) hadWorkout = true
                else if (hadWorkout) {
                    hadWorkout = false
                    DayRepo.flush(ctx)
                    TrainingRepo.load(ctx)
                }
            }
            if (workout != null) {
                WorkoutSection()
                return@Column
            }
            state.readiness?.let { ReadinessCard(it) }
            WorkoutSection()
            val current = state.current
            when {
                current != null -> {
                    SessionCard(current) { e, r -> scope.launch { TrainingRepo.rate(ctx, current, e, r) } }
                }
                state.loadedAt > 0 -> {
                    state.next?.let { NextCard(it, onSkip = { asking = true }, onDeload = { start -> scope.launch { TrainingRepo.deload(ctx, start) } }) }
                        ?: Muted(tr("Kein aktiver Plan. Pläne legst du in der WebUI unter Training an."))
                    Muted(tr("Heute noch kein Training aufgezeichnet. Frühere Trainings bewertest du in der WebUI unter Training."))
                }
            }
            state.muscles?.let { MuscleCard(it) }
        }
    }
    if (asking) SkipDialog(state.next?.day.orEmpty(), onDismiss = { asking = false }) { note ->
        asking = false
        scope.launch { TrainingRepo.skip(ctx, note) }
    }
}

private val MUSCLE_STATES = mapOf("fatigued" to tr("ermüdet"), "recovering" to tr("regeneriert"), "ready" to tr("bereit"))

private fun hoursText(h: Double): String =
    if (h < 36) tr("ca. {0} h", maxOf(1, kotlin.math.round(h).toInt())) else tr("ca. {0} Tage", "%.1f".format(I18n.locale, h / 24).removeSuffix(",0").removeSuffix(".0"))

private fun daysSince(ts: String?, at: String): Long? = runCatching {
    java.time.Duration.between(OffsetDateTime.parse(ts), OffsetDateTime.parse(at)).toDays()
}.getOrNull()

private fun shortTime(ts: String): String = runCatching {
    OffsetDateTime.parse(ts).atZoneSameInstant(ZoneId.systemDefault())
        .format(java.time.format.DateTimeFormatter.ofPattern(I18n.pattern("EE d. MMM, HH:mm"), I18n.locale))
}.getOrDefault(ts)

/** Recovery per muscle on the body map (or how long each muscle has gone untrained); a tapped
 *  muscle shows its state, time until ready and the workouts still in it. */
@Composable
private fun MuscleCard(map: MuscleMap) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val settings = remember { Settings(ctx) }
    var neglected by remember { mutableStateOf(false) }
    var picked by remember { mutableStateOf<String?>(null) }
    var figure by remember { mutableStateOf(settings.bodyFigure) }
    val colors = Vita.colors
    val byKey = map.muscles.associateBy { it.key }
    val fill: (String) -> androidx.compose.ui.graphics.Color? = { m ->
        val x = byKey[m]
        if (neglected) {
            val d = daysSince(x?.lastTrained, map.at)
            when {
                d == null || d > 14 -> colors.fatigued
                d > 7 -> colors.recovering
                d > 3 -> colors.ok.copy(alpha = 0.45f)
                else -> colors.ok
            }
        } else when {
            x == null || x.lastTrained == null || x.fatigue <= 0.02 -> null
            x.state == "fatigued" -> colors.fatigued
            x.state == "recovering" -> colors.recovering
            else -> colors.ok.copy(alpha = 0.45f)
        }
    }
    VCard(tr("Muskeln"), R.drawable.ic_t_body_scan, tint = colors.train, trailing = {
        TextButton(onClick = { figure = if (figure == "male") "female" else "male"; settings.bodyFigure = figure }) {
            Text(if (figure == "female") tr("weiblich") else tr("männlich"), style = MaterialTheme.typography.bodySmall)
        }
    }) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(selected = !neglected, onClick = { neglected = false }, label = { Text(tr("Regeneration")) })
            FilterChip(selected = neglected, onClick = { neglected = true }, label = { Text(tr("Vernachlässigt")) })
        }
        de.vitalocal.companion.ui.BodyMap(fill, figure = figure, selected = picked, onPick = { picked = if (picked == it) null else it })
        Muted(if (neglected) tr("Grün: ≤ 3 Tage · gelb: 8–14 Tage · rot: > 14 Tage oder nie")
              else tr("Rot: ermüdet · gelb: regeneriert · hellgrün: bereit (kürzlich trainiert)"))
        if (!neglected) map.garminRecoveryH?.let { Muted(tr("Erholungszeit laut Garmin: ") + if (it > 0) hoursText(it) else "erholt") }
        val x = picked?.let { byKey[it] }
        if (x == null) Muted(tr("Tippe auf einen Muskel für Details."))
        else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(x.label, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                Text(MUSCLE_STATES[x.state] ?: x.state, style = MaterialTheme.typography.bodySmall,
                    color = if (x.state == "ready") colors.ok else colors.warn)
            }
            Muted(listOfNotNull(
                if (x.readyInH > 0) tr("bereit in {0}", hoursText(x.readyInH)) else null,
                x.lastTrained?.let { "zuletzt ${shortTime(it)}" } ?: tr("in den letzten 6 Wochen nicht trainiert"),
                tr("{0} harte Sätze in 7 Tagen", "%.1f".format(I18n.locale, x.hardSets7d).removeSuffix(",0").removeSuffix(".0")),
            ).joinToString(" · "))
            x.sessions.forEach { s ->
                Column(Modifier.padding(top = 4.dp)) {
                    Text(s.exercises.joinToString(", ").ifEmpty { tr("Training") }, style = MaterialTheme.typography.bodyMedium)
                    Muted(listOfNotNull(shortTime(s.ts), tr("noch {0} % zu regenerieren", (s.remaining * 100).toInt()),
                        if (s.provisional) tr("noch nicht bestätigt") else null).joinToString(" · "))
                }
            }
        }
    }
}

/** Skip the next plan day with an optional note (ill, travelling …). */
@Composable
private fun SkipDialog(day: String, onDismiss: () -> Unit, onSkip: (String) -> Unit) {
    var note by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = { VIcon(R.drawable.ic_t_player_skip_forward) },
        title = { Text(tr("{0} überspringen?", day)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Muted(tr("Die Rotation geht mit dem nächsten Tag weiter; der übersprungene Tag steht im Verlauf."))
                OutlinedTextField(note, { note = it.take(500) }, label = { Text(tr("Notiz (optional)")) }, placeholder = { Text(tr("z. B. krank, unterwegs")) },
                    singleLine = true)
            }
        },
        confirmButton = { TextButton(onClick = { onSkip(note) }) { Text(tr("Überspringen")) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(tr("Abbrechen")) } },
    )
}

/** The readiness at a glance: score, band and Garmin's value; the factors are in the WebUI's overview. */
@Composable
private fun ReadinessCard(r: Readiness) {
    VCard(tr("Trainingsbereitschaft"), R.drawable.ic_t_gauge, tint = Vita.colors.train, trailing = {
        Text(r.score?.toString() ?: "–", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold,
            color = when {
                r.score == null -> MaterialTheme.colorScheme.onSurfaceVariant
                r.score >= 75 -> MaterialTheme.colorScheme.primary
                r.score >= 50 -> MaterialTheme.colorScheme.tertiary
                else -> MaterialTheme.colorScheme.error
            })
    }) {
        Muted(listOfNotNull(
            LEVELS[r.level] ?: if (r.score == null) tr("zu wenig Daten") else null,
            r.garmin?.let { g -> tr("Garmin: {0}{1}", g, r.garminLevel?.let { GARMIN_LEVELS[it] }?.let { " (${it})" } ?: "") },
        ).joinToString(" · "))
        WebLink(tr("Aufschlüsselung in der WebUI"), "#/")
    }
}

@Composable
private fun NextCard(n: NextDay, onSkip: () -> Unit, onDeload: (Boolean) -> Unit = {}) {
    VCard(tr("Nächstes Training"), R.drawable.ic_t_player_play, tint = Vita.colors.train) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            n.deload?.let { d -> DeloadNotice(d, onDeload) }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(n.day, style = MaterialTheme.typography.headlineSmall)
                    Muted(n.plan)
                }
                OutlinedButton(onClick = onSkip) {
                    VIcon(R.drawable.ic_t_player_skip_forward, size = 16.dp)
                    Text(tr("  Überspringen"))
                }
            }
            if (n.entries.isEmpty()) Muted(tr("Keine Übungen im Plantag"))
            n.entries.forEach { e ->
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(e.name, Modifier.weight(1f))
                        Text(e.current, style = MaterialTheme.typography.titleSmall)
                        if (e.next != null) {
                            VIcon(R.drawable.ic_t_arrow_right, Modifier.padding(horizontal = 4.dp), size = 14.dp, tint = Vita.colors.muted)
                            Text(e.next, style = MaterialTheme.typography.titleSmall, color = if (e.deload) Vita.colors.muted else Vita.colors.ok)
                        }
                    }
                    e.last?.let { last -> Muted(tr("zuletzt: {0}", last) + (ratingText(e.lastRating)?.let { " (${it})" } ?: "")) }
                }
            }
        }
    }
}

/** In a deload week: until when, and ending it early; a suggested one: why, and starting it. */
@Composable
private fun DeloadNotice(d: Deload, onDeload: (Boolean) -> Unit) {
    if (!d.active && !d.suggested) return
    val until = d.until?.let { runCatching { LocalDate.parse(it).format(java.time.format.DateTimeFormatter.ofPattern(I18n.pattern("EE d. MMM"), I18n.locale)) }.getOrDefault(it) }
    Card(Modifier.fillMaxWidth(), shape = CardShape,
        colors = CardDefaults.cardColors(containerColor = if (d.active) MaterialTheme.colorScheme.secondaryContainer else Vita.colors.warnSoft)) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            if (d.active) {
                Text(tr("Deload-Woche bis {0}", until), fontWeight = FontWeight.SemiBold)
                Muted(tr("Ein Satz weniger bei gleichem Gewicht, Wiederholungen am unteren Ende; zählt nicht für die Progression."))
                TextButton(onClick = { onDeload(false) }) { Text(tr("Deload beenden")) }
            } else {
                Text(tr("Deload-Woche empfohlen"), fontWeight = FontWeight.SemiBold)
                Muted(d.reasons.joinToString("; "))
                TextButton(onClick = { onDeload(true) }) { Text(tr("Deload-Woche starten")) }
            }
        }
    }
}

@Composable
private fun SessionCard(s: SessionView, onRate: (SessionExercise, String) -> Unit) {
    VCard(tr("Heutiges Training"), R.drawable.ic_t_barbell, tint = Vita.colors.train) {
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(s.title, style = MaterialTheme.typography.titleMedium)
            Muted(listOfNotNull(s.planDay?.let { tr("Plantag {0}", it) }, tr("bewerte jede Übung: das steuert das nächste Ziel")).joinToString(" · "))
            if (!s.reviewed) Muted(tr("Noch nicht geprüft – Sätze und Plantag in der WebUI bestätigen, dann hier bewerten."))
            else if (s.unassigned > 0) Muted(tr("{0} Sätze ohne Übung (WebUI → Training)", s.unassigned))
            s.exercises.forEach { e ->
                Column {
                    Text(e.name)
                    Muted(e.text + (e.planned?.let { tr(" · geplant {0}", it) } ?: ""))
                    if (e.suggestion != null && e.suggestion != e.rating) {
                        Muted(tr("Vorschlag aus den Sätzen: {0}{1}", ratingText(e.suggestion), WHY[e.why]?.let { " (${it})" } ?: "") +
                            if (e.rating == null) tr(" – antippen zum Übernehmen") else "")
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        RATINGS.forEach { (k, label) ->
                            FilterChip(selected = e.rating == k, onClick = { onRate(e, k) },
                                label = { Text(if (e.rating == null && e.suggestion == k) "${label}?" else label) })
                        }
                    }
                }
            }
            if (s.exercises.isNotEmpty()) DebriefSection(s.id)
        }
    }
}
