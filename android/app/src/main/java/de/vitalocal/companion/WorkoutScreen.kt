package de.vitalocal.companion

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import de.vitalocal.companion.ui.CardShape
import de.vitalocal.companion.ui.Muted
import de.vitalocal.companion.ui.RoundCheck
import de.vitalocal.companion.ui.Stepper
import de.vitalocal.companion.ui.VCard
import de.vitalocal.companion.ui.VIcon
import de.vitalocal.companion.ui.Vita
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject
import java.io.File
import java.time.OffsetDateTime
import java.util.UUID

/** What a workout is built from, cached for the gym without signal: the active plan with targets,
 *  the own exercises and the weights one dumbbell can be loaded to. */
data class WorkoutCatalog(val plan: JSONObject?, val exercises: Map<String, JSONObject>, val plates: List<Double>) {
    val days: List<JSONObject> get() = plan?.optJSONArray("days").objects()
    val nextDayId: String? get() = plan?.optStringOrNull("next_day_id")
    val active: List<JSONObject> get() = exercises.values.filter { !it.optBoolean("archived") }.sortedBy { it.optString("name").lowercase() }
}

object WorkoutRepo {
    private val _draft = MutableStateFlow<Workout?>(null)
    val draft: StateFlow<Workout?> = _draft.asStateFlow()
    private val _catalog = MutableStateFlow(WorkoutCatalog(null, emptyMap(), emptyList()))
    val catalog: StateFlow<WorkoutCatalog> = _catalog.asStateFlow()
    private var loaded = false

    private fun draftFile(c: Context) = File(c.filesDir, "workout_draft.json")
    private fun catalogFile(c: Context) = File(c.filesDir, "workout_catalog.json")

    /** Draft and catalog from the phone (once per process). */
    fun init(context: Context) {
        if (loaded) return
        loaded = true
        _draft.value = runCatching { Workout.parse(JSONObject(draftFile(context).readText())) }.getOrNull()
        runCatching { parseCatalog(JSONObject(catalogFile(context).readText())) }.getOrNull()?.let { _catalog.value = it }
    }

    private fun parseCatalog(o: JSONObject) = WorkoutCatalog(
        o.optJSONObject("plan"),
        o.optJSONArray("exercises").objects().associateBy { it.getString("id") },
        o.optJSONObject("plates")?.optJSONArray("weights").doubles(),
    )

    /** Called by the training tab's load (on the IO thread, with the server reachable). */
    fun refreshCatalog(context: Context, api: Api) {
        val o = JSONObject().put("plan", api.currentPlan() ?: JSONObject.NULL).put("exercises", api.exercises())
            .put("plates", runCatching { api.plates() }.getOrNull() ?: JSONObject.NULL)
        runCatching { catalogFile(context).writeText(o.toString()) }
        _catalog.value = parseCatalog(o)
    }

    fun start(context: Context, w: Workout) = save(context, w)

    fun update(context: Context, f: (Workout) -> Workout) {
        save(context, _draft.value?.let(f) ?: return)
    }

    /** Every change goes to the phone at once: the draft survives the app being closed mid-workout. */
    private fun save(context: Context, w: Workout) {
        _draft.value = w
        runCatching { draftFile(context).writeText(w.toJson().toString()) }
    }

    /** Ticks a set off (or back on) and starts the rest timer after a ticked set. */
    fun toggleSet(context: Context, key: String, setId: String) {
        val w = _draft.value ?: return
        val e = w.exercise(key)
        val done = !(e.sets.first { it.id == setId }.done)
        val rest = if (done) System.currentTimeMillis() + e.restS * 1000L else null
        update(context) { it.withSet(key, setId) { s -> s.copy(done = done) }.copy(restUntil = rest ?: it.restUntil, restAfter = if (done) e.name else it.restAfter) }
        if (rest != null) RestTimer.schedule(context, rest, e.name)
    }

    fun skipRest(context: Context, extraS: Int = 0) {
        val w = _draft.value ?: return
        val until = if (extraS > 0) maxOf(w.restUntil ?: 0L, System.currentTimeMillis()) + extraS * 1000L else null
        update(context) { it.copy(restUntil = until) }
        if (until != null) RestTimer.schedule(context, until, w.restAfter ?: "") else RestTimer.cancel(context)
    }

    /** Puts the workout into the outbox (sent now or once the server is reachable) and clears the draft. */
    fun finish(context: Context) {
        val w = _draft.value ?: return
        DayRepo.addWorkout(context, w.toManual(OffsetDateTime.now()), w.title)
        discard(context)
    }

    fun discard(context: Context) {
        RestTimer.cancel(context)
        _draft.value = null
        draftFile(context).delete()
    }
}

/** The end of a rest: a notification with sound and vibration, also with the screen off. */
object RestTimer {
    private const val CHANNEL = "rest_timer"
    private const val NID = 4711

    fun createChannel(context: Context) {
        val channel = NotificationChannel(CHANNEL, tr("Pausentimer"), NotificationManager.IMPORTANCE_HIGH)
            .apply { description = tr("Ende der Satzpause beim Training ohne Uhr"); enableVibration(true) }
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun intent(context: Context, name: String = "") = PendingIntent.getBroadcast(
        context, NID, Intent(context, RestTimerReceiver::class.java).putExtra("name", name),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun schedule(context: Context, at: Long, name: String) {
        val am = context.getSystemService(AlarmManager::class.java)
        NotificationManagerCompat.from(context).cancel(NID)
        if (Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, intent(context, name))
        else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, intent(context, name))
    }

    fun cancel(context: Context) {
        context.getSystemService(AlarmManager::class.java).cancel(intent(context))
        NotificationManagerCompat.from(context).cancel(NID)
    }

    fun notify(context: Context, name: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val open = PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(tr("Pause vorbei"))
            .setContentText(if (name.isNotEmpty()) tr("Pause nach {0} vorbei – weiter mit dem nächsten Satz", name) else tr("Weiter mit dem nächsten Satz"))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setTimeoutAfter(120_000)
            .build()
        NotificationManagerCompat.from(context).notify(NID, n)
    }
}

class RestTimerReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = RestTimer.notify(context, intent.getStringExtra("name").orEmpty())
}

// --- UI ---------------------------------------------------------------------------------------------------

private val RATING_CHOICES = listOf("easy" to tr("zu leicht"), "good" to tr("gut"), "hard" to tr("zu schwer"))

private fun clock(ms: Long): String {
    val s = (ms / 1000).coerceAtLeast(0)
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, s / 60 % 60, s % 60) else "%d:%02d".format(s / 60, s % 60)
}

/** Start a workout without the watch: the plan day due next (or another one), or a free workout. */
@Composable
fun StartWorkoutCard(catalog: WorkoutCatalog) {
    val ctx = LocalContext.current
    var menu by remember { mutableStateOf(false) }
    val days = catalog.days
    val next = days.firstOrNull { it.getString("id") == catalog.nextDayId } ?: days.firstOrNull()
    fun start(day: JSONObject?) {
        val now = OffsetDateTime.now()
        WorkoutRepo.start(ctx, if (day == null) Workout.empty(now)
            else Workout.fromPlanDay(day, catalog.plan?.optString("name"), catalog.exercises, catalog.plates, now))
    }
    VCard(tr("Training ohne Uhr"), R.drawable.ic_t_player_play, tint = Vita.colors.train) {
        Muted(tr("Sätze und Wiederholungen direkt hier erfassen; die Ziele kommen aus deinem Plan."))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            if (next != null) Button(onClick = { start(next) }) { Text(tr("{0} starten", next.optString("name"))) }
            Column {
                OutlinedButton(onClick = { menu = true }) { Text(if (next != null) tr("Anderes …") else tr("Starten …")) }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    days.forEach { d -> DropdownMenuItem(text = { Text(d.optString("name")) }, onClick = { menu = false; start(d) }) }
                    DropdownMenuItem(text = { Text(tr("Freies Training")) }, onClick = { menu = false; start(null) })
                }
            }
        }
    }
}

/** The running workout: sets to tick off with reps and weight, rest timer, rating per exercise. */
@Composable
fun WorkoutView(w: Workout, catalog: WorkoutCatalog) {
    val ctx = LocalContext.current
    val view = LocalView.current
    DisposableEffect(Unit) {
        view.keepScreenOn = true
        onDispose { view.keepScreenOn = false }
    }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { now = System.currentTimeMillis(); delay(1000) } }
    var picking by remember { mutableStateOf(false) }
    var confirmEnd by remember { mutableStateOf(false) }
    var confirmDiscard by remember { mutableStateOf(false) }
    val started = remember(w.start) { OffsetDateTime.parse(w.start).toInstant().toEpochMilli() }

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(w.title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                Muted(tr("läuft seit {0} · {1} {2} erledigt", clock(now - started), w.doneSets, if (w.doneSets == 1) tr("Satz") else tr("Sätze")))
            }
            TextButton(onClick = { confirmDiscard = true }) { Text(tr("Verwerfen")) }
        }
        // always there, so ticking a set off never moves the rows under the finger
        val rest = w.restUntil?.let { it - now }
        val resting = rest != null && rest > 0
        val ended = rest != null && rest in -60_000..0
        Card(Modifier.fillMaxWidth(), shape = CardShape, colors = CardDefaults.cardColors(containerColor = when {
            resting -> MaterialTheme.colorScheme.primaryContainer
            ended -> Vita.colors.okSoft
            else -> Vita.colors.surface2
        })) {
            Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(start = 16.dp, end = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                VIcon(R.drawable.ic_t_clock, size = 18.dp)
                Text(when {
                    resting -> tr("  Pause {0}", clock(rest!!))
                    ended -> tr("  Pause vorbei")
                    else -> tr("  Pause startet beim Abhaken")
                }, Modifier.weight(1f), style = if (resting || ended) MaterialTheme.typography.titleMedium else MaterialTheme.typography.bodyMedium,
                    color = if (resting || ended) MaterialTheme.colorScheme.onSurface else Vita.colors.muted)
                if (resting) TextButton(onClick = { WorkoutRepo.skipRest(ctx, 30) }) { Text("+30 s") }
                if (resting || ended) TextButton(onClick = { WorkoutRepo.skipRest(ctx) }) { Text(if (resting) tr("Überspringen") else "OK") }
            }
        }
        w.exercises.forEach { e -> ExerciseCard(w, e) }
        if (w.exercises.isEmpty()) Muted(tr("Noch keine Übung – füge eine hinzu."))
        OutlinedButton(onClick = { picking = true }, Modifier.fillMaxWidth()) { Text(tr("+ Übung")) }
        Button(onClick = { confirmEnd = true }, Modifier.fillMaxWidth(), enabled = w.doneSets > 0) { Text(tr("Training abschließen")) }
    }

    if (picking) ExercisePicker(catalog, onDismiss = { picking = false }) { ex ->
        picking = false
        WorkoutRepo.update(ctx) { it.copy(exercises = it.exercises + Workout.exerciseFrom(ex, catalog.plates)) }
    }
    if (confirmEnd) AlertDialog(
        onDismissRequest = { confirmEnd = false },
        title = { Text(tr("Training abschließen?")) },
        text = { Text(tr("{0} erledigte Sätze werden gespeichert (offline: sobald der Server erreichbar ist). Nicht abgehakte Sätze fallen weg.", w.doneSets)) },
        confirmButton = {
            TextButton(onClick = { confirmEnd = false; WorkoutRepo.finish(ctx) }) { Text(tr("Abschließen")) }
        },
        dismissButton = { TextButton(onClick = { confirmEnd = false }) { Text(tr("Weiter trainieren")) } },
    )
    if (confirmDiscard) AlertDialog(
        onDismissRequest = { confirmDiscard = false },
        title = { Text(tr("Training verwerfen?")) },
        text = { Text(tr("Alle erfassten Sätze dieses Trainings gehen verloren.")) },
        confirmButton = { TextButton(onClick = { confirmDiscard = false; WorkoutRepo.discard(ctx) }) { Text(tr("Verwerfen")) } },
        dismissButton = { TextButton(onClick = { confirmDiscard = false }) { Text(tr("Abbrechen")) } },
    )
}

@Composable
private fun ExerciseCard(w: Workout, e: WExercise) {
    val ctx = LocalContext.current
    val accent = Vita.colors.train
    val upd = { f: (WExercise) -> WExercise -> WorkoutRepo.update(ctx) { it.withExercise(e.key, f) } }
    VCard(e.name, tint = accent, trailing = {
        TextButton(onClick = { WorkoutRepo.update(ctx) { it.copy(exercises = it.exercises.filterNot { x -> x.key == e.key }) } }) {
            VIcon(R.drawable.ic_t_trash, size = 16.dp)
        }
    }) {
        e.targetText?.let { Muted(tr("Ziel: {0} · Pause {1} s", it, e.restS)) }
        var n = 0
        e.sets.forEach { s ->
            if (!s.warmup) n++
            SetRow(e, s, if (s.warmup) "A" else "${n}")
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = {
                upd { x ->
                    val last = x.sets.lastOrNull { !it.warmup }
                    x.copy(sets = x.sets + WSet(UUID.randomUUID().toString(), last?.reps ?: x.targetReps, last?.weightKg ?: x.targetWeight,
                        if (x.timed) last?.durationS ?: x.targetDuration ?: 30 else null))
                }
            }) { Text(tr("+ Satz")) }
            TextButton(onClick = {
                upd { x ->
                    val first = x.sets.firstOrNull { !it.warmup }
                    val w0 = first?.weightKg?.let { x.stepWeight(x.stepWeight(it, false), false) }
                    x.copy(sets = listOf(WSet(UUID.randomUUID().toString(), first?.reps ?: x.targetReps, w0,
                        if (x.timed) first?.durationS else null, warmup = true)) + x.sets)
                }
            }) { Text(tr("+ Aufwärmsatz")) }
            val removable = e.sets.lastOrNull { !it.done }
            if (removable != null) TextButton(onClick = { upd { x -> x.copy(sets = x.sets.filterNot { it.id == removable.id }) } }) { Text(tr("− Satz")) }
        }
        if (e.workDone.isNotEmpty()) {
            // a suggestion only once the planned sets are done: after one set "too hard" would be noise
            val complete = e.workDone.size >= e.targetSets
            val suggested = if (complete) e.suggestedRating() else null
            val chosen = e.rating ?: suggested
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                RATING_CHOICES.forEach { (k, label) ->
                    FilterChip(selected = chosen == k, onClick = { upd { it.copy(rating = k) } }, label = { Text(label) })
                }
            }
            if (suggested != null && chosen == suggested) Muted(tr("Aus deinen Sätzen vorgeschlagen – tippe, wenn es sich anders angefühlt hat."))
        }
    }
    // a suggestion counts once the exercise is complete; the user may still change it
    LaunchedEffect(e.workDone.size) {
        if (e.rating == null && e.workDone.size >= e.targetSets) e.suggestedRating()?.let { r -> upd { it.copy(rating = r) } }
    }
}

@Composable
private fun SetRow(e: WExercise, s: WSet, label: String) {
    val ctx = LocalContext.current
    val accent = Vita.colors.train
    val upd = { f: (WSet) -> WSet -> WorkoutRepo.update(ctx) { it.withSet(e.key, s.id, f) } }
    Row(Modifier.fillMaxWidth().heightIn(min = 44.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.width(24.dp), style = MaterialTheme.typography.titleSmall,
            color = if (s.warmup) Vita.colors.muted else MaterialTheme.colorScheme.onSurface)
        if (e.timed) {
            Stepper("${s.durationS ?: 0} s", s.done, (s.durationS ?: 0) > 0,
                onMinus = { upd { it.copy(durationS = maxOf(0, (it.durationS ?: 0) - 5)) } },
                onPlus = { upd { it.copy(durationS = (it.durationS ?: 0) + 5) } }, accent)
        } else {
            Stepper("${s.reps ?: 0}×", s.done, (s.reps ?: 0) > 0,
                onMinus = { upd { it.copy(reps = maxOf(0, (it.reps ?: 0) - 1)) } },
                onPlus = { upd { it.copy(reps = (it.reps ?: 0) + 1) } }, accent)
        }
        Text("  ", Modifier.weight(1f))
        Stepper("${if (e.bodyweight) "+" else ""}${formatKg(s.weightKg ?: 0.0)} kg", s.done, (s.weightKg ?: 0.0) > 0,
            onMinus = { upd { it.copy(weightKg = e.stepWeight(it.weightKg, false)) } },
            onPlus = { upd { it.copy(weightKg = e.stepWeight(it.weightKg, true)) } }, accent)
        Text(" ", Modifier.width(8.dp))
        RoundCheck(s.done, accent) { WorkoutRepo.toggleSet(ctx, e.key, s.id) }
    }
}

/** The own exercises (offline from the cache), searchable; new ones come from the library in the WebUI. */
@Composable
private fun ExercisePicker(catalog: WorkoutCatalog, onDismiss: () -> Unit, onPick: (JSONObject) -> Unit) {
    var q by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(tr("Übung hinzufügen")) },
        text = {
            Column {
                OutlinedTextField(q, { q = it }, Modifier.fillMaxWidth(), singleLine = true, placeholder = { Text(tr("Suchen …")) })
                Column(Modifier.heightIn(max = 360.dp).verticalScroll(rememberScrollState())) {
                    val list = catalog.active.filter { it.optString("name").contains(q.trim(), ignoreCase = true) }
                    list.forEach { ex ->
                        Text(ex.optString("name"), Modifier.fillMaxWidth().clickable { onPick(ex) }.padding(vertical = 10.dp))
                    }
                    if (list.isEmpty()) Muted(tr("Keine Übung gefunden. Neue Übungen legst du in der WebUI an (Bibliothek)."))
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text(tr("Schließen")) } },
    )
}

/** The training tab's content while a workout runs, else the start card (needs the cached catalog). */
@Composable
fun WorkoutSection() {
    val ctx = LocalContext.current
    remember { WorkoutRepo.init(ctx) }
    val draft by WorkoutRepo.draft.collectAsState()
    val catalog by WorkoutRepo.catalog.collectAsState()
    val w = draft
    if (w != null) WorkoutView(w, catalog)
    else if (catalog.exercises.isNotEmpty()) StartWorkoutCard(catalog)
}
