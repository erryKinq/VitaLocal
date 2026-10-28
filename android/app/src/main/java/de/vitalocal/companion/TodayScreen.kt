package de.vitalocal.companion

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import de.vitalocal.companion.ui.CardShape
import de.vitalocal.companion.ui.FoldToggle
import de.vitalocal.companion.ui.Glyph
import de.vitalocal.companion.ui.LocalOpenSettings
import de.vitalocal.companion.ui.Muted
import de.vitalocal.companion.ui.RoundCheck
import de.vitalocal.companion.ui.ScreenHeader
import de.vitalocal.companion.ui.StatTile
import de.vitalocal.companion.ui.StatusPill
import de.vitalocal.companion.ui.Stepper
import de.vitalocal.companion.ui.VCard
import de.vitalocal.companion.ui.VIcon
import de.vitalocal.companion.ui.DayArrows
import de.vitalocal.companion.ui.WebLink
import de.vitalocal.companion.ui.dayLabel
import de.vitalocal.companion.ui.openWeb
import de.vitalocal.companion.ui.Vita
import kotlinx.coroutines.launch
import java.text.NumberFormat
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val MOOD = listOf(tr("sehr schlecht"), tr("schlecht"), tr("okay"), tr("gut"), tr("sehr gut"))
private val ENERGY = listOf(tr("erschöpft"), tr("müde"), tr("normal"), tr("frisch"), tr("energiegeladen"))

/**
 * Overview of one of the last 7 days: what is still to do (habits to tick and supplements in one
 * list), how you feel, and the day's key values. Drinks and weight are on the nutrition tab,
 * automatic habits and rewards on the habits tab, the details in the WebUI.
 */
@Composable
fun TodayScreen() {
    val ctx = LocalContext.current
    val settings = remember { Settings(ctx) }
    val state by DayRepo.state.collectAsState()
    // Past days for catching up; back to today on resume, so the screen also moves on after midnight.
    var day by remember { mutableStateOf(DayRepo.today) }
    val scope = rememberCoroutineScope()
    val d = state.view(day)
    val caps by Caps.state.collectAsState()
    LaunchedEffect(day) { DayRepo.refresh(ctx, day) }
    LaunchedEffect(Unit) { AppUpdate.check(ctx) }
    // keyed on Unit: a key on [day] would restart this on every day change and jump back at once
    LifecycleResumeEffect(Unit) {
        if (day != DayRepo.today) day = DayRepo.today
        scope.launch { DayRepo.refresh(ctx, day) }
        onPauseOrDispose { }
    }

    PullToRefreshBox(isRefreshing = state.refreshing, onRefresh = { scope.launch { DayRepo.refresh(ctx, day) } }) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            val isToday = day == DayRepo.today
            val date = LocalDate.parse(day).format(DateTimeFormatter.ofPattern(I18n.pattern("EEEE, d. MMMM"), I18n.locale))
            // "Heute · Sonntag, 25. Oktober", "Gestern · …", older days just the date
            val recent = !LocalDate.parse(day).isBefore(LocalDate.now().minusDays(1))
            ScreenHeader(tr("Übersicht"), if (recent) "${dayLabel(day)} · ${date}" else date, R.drawable.ic_t_home, Vita.colors.day) {
                DayArrows(day) { day = it }
            }
            StatusLine(d, state, settings.isConfigured)
            if (isToday) {
                UpdateBanner()
                if (!caps.setupDone && settings.isConfigured) SetupHint()
            }
            TodoCard(d, day, isToday)
            if (caps.on("journal")) CheckinCard(day)
            MetricsCard(d, day, watch = caps.on("watch"))
        }
    }
}

/** Sync state as a pill (tap: sync status), plus waiting entries and errors. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun StatusLine(d: TodayData, state: DayState, configured: Boolean) {
    val openSettings = LocalOpenSettings.current
    if (!configured) {
        StatusPill(tr("Server noch nicht eingerichtet"), ok = false) { openSettings(null) }
        return
    }
    val waiting = state.pending.count { it !is PendingOp.Unknown }
    val watch = d.lastWatchData
    val fresh = watch != null && runCatching {
        java.time.Duration.between(OffsetDateTime.parse(watch).toInstant(), java.time.Instant.now()).toMinutes() < 60
    }.getOrDefault(false)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp),
        itemVerticalAlignment = Alignment.CenterVertically) {
        StatusPill(watch?.let { tr("Uhr {0}", watchAge(it)) } ?: tr("Noch keine Uhr-Daten"), ok = fresh) { openSettings("sync") }
        if (waiting > 0) Muted(if (waiting == 1) tr("1 Eintrag wartet auf Übertragung") else tr("{0} Einträge warten auf Übertragung", waiting))
        else if (d.fetchedAt > 0) Muted(tr("Stand {0}", formatTime(d.fetchedAt)))
    }
    state.error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
}

/** Shown when the server offers a newer build of the app. */
@Composable
fun UpdateBanner(showUpToDate: Boolean = false) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val update by AppUpdate.state.collectAsState()
    val text: String
    var action: (() -> Unit)? = null
    var label = ""
    when (val s = update) {
        is AppUpdate.State.Available -> {
            text = tr("Update auf {0} verfügbar ({1})", s.app.version, formatBytes(s.app.size))
            label = tr("Installieren"); action = { scope.launch { AppUpdate.install(ctx, s.app) } }
        }
        is AppUpdate.State.Downloading -> text = tr("Lädt {0} … {1} von {2}", s.app.version, formatBytes(s.done), formatBytes(s.app.size))
        is AppUpdate.State.Installing -> text = tr("Installiere {0} – bitte im Systemdialog bestätigen", s.app.version)
        is AppUpdate.State.Failed -> {
            text = tr("Update fehlgeschlagen: {0}", s.message)
            s.app?.let { app -> label = tr("Erneut"); action = { scope.launch { AppUpdate.install(ctx, app) } } }
        }
        AppUpdate.State.Idle -> if (showUpToDate) text = tr("App ist aktuell ({0})", BuildConfig.VERSION_NAME) else return
    }
    Card(Modifier.fillMaxWidth(), shape = CardShape, colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            VIcon(R.drawable.ic_t_download, size = 18.dp)
            Spacer(Modifier.width(8.dp))
            Text(text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            action?.let { a -> FilledTonalButton(onClick = a) { Text(label) } }
        }
    }
}

// --- To do: habits and supplements ----------------------------------------------------------

private val FORMS = mapOf("capsule" to tr("Kapseln"), "tablet" to tr("Tabletten"), "drops" to tr("Tropfen"), "powder" to tr("Portionen"), "other" to tr("Einheiten"))

/** Open habits (to tick or count; automatic ones are on the habits tab) and supplements; what is done folds away below. */
@Composable
private fun TodoCard(d: TodayData, day: String, isToday: Boolean) {
    val manual = d.habits.filter { !it.automatic }
    val open = manual.filter { it.done != true } to d.supplements.filter { !it.settled }
    val doneHabits = manual.filter { it.done == true }
    val doneSupps = d.supplements.filter { it.settled }
    val total = manual.size + d.supplements.size
    val left = open.first.size + open.second.size
    var showDone by rememberSaveable { mutableStateOf(false) }
    VCard(tr("Zu erledigen"), R.drawable.ic_t_list_check, tint = Vita.colors.habit, trailing = {
        if (total > 0) Text(if (left == 0) tr("alles erledigt") else tr("{0} offen", left), style = MaterialTheme.typography.labelMedium,
            color = if (left == 0) Vita.colors.ok else MaterialTheme.colorScheme.onSurfaceVariant)
    }) {
        if (total == 0) Muted(tr("Noch keine Gewohnheiten oder Supplements – lege sie im Tab Gewohnheiten an."))
        open.first.forEach { HabitRow(it, day) }
        open.second.forEach { SupplementRow(it, day) }
        if (left == 0 && total > 0) Muted(if (isToday) tr("Für heute ist alles abgehakt.") else tr("Für diesen Tag ist alles abgehakt."))
        if (doneHabits.isNotEmpty() || doneSupps.isNotEmpty()) {
            TextButton(onClick = { showDone = !showDone }, contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)) {
                Text(if (showDone) tr("Erledigte ausblenden") else tr("Erledigt ({0})", doneHabits.size + doneSupps.size))
            }
            if (showDone) {
                doneHabits.forEach { HabitRow(it, day) }
                doneSupps.forEach { SupplementRow(it, day) }
            }
        }
    }
}

@Composable
internal fun HabitRow(h: Habit, day: String) {
    val ctx = LocalContext.current
    val c = Vita.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        Glyph(h.icon, size = 20.dp, tint = c.habit)
        if (h.icon != null) Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(h.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
            val sub = listOfNotNull(
                if (h.streak > 0) tr("{0} {1} in Folge", h.streak, if (h.streakUnit == "weeks") tr("Wo.") else "T.") else null,
                if (h.jokers > 0) tr("{0} Joker", h.jokers) else null, if (h.hard) tr("schwer ×2") else null,
            ).joinToString(" · ")
            if (sub.isNotEmpty()) Row(verticalAlignment = Alignment.CenterVertically) {
                if (h.streak > 0) { VIcon(R.drawable.ic_t_flame, size = 12.dp, tint = c.flame); Spacer(Modifier.width(3.dp)) }
                Muted(sub)
            }
        }
        when {
            h.automatic -> Text(automaticStatus(h), style = MaterialTheme.typography.bodySmall,
                color = if (h.done == true) c.ok else MaterialTheme.colorScheme.onSurfaceVariant)
            h.kind == "count" -> {
                val v = h.value ?: 0.0
                Stepper("${v.toInt()}/${h.target?.toInt() ?: 1}${h.unit?.let { " ${it}" } ?: ""}", done = h.done == true, minusEnabled = v > 0,
                    onMinus = { DayRepo.adjustHabit(ctx, day, h.id, -1.0) }, onPlus = { DayRepo.adjustHabit(ctx, day, h.id, 1.0) }, color = c.habit)
            }
            else -> RoundCheck(h.done == true, c.habit) { DayRepo.logHabit(ctx, day, h.id, if (h.done == true) 0.0 else 1.0) }
        }
    }
}

@Composable
private fun SupplementRow(s: Supplement, day: String) {
    val ctx = LocalContext.current
    val c = Vita.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        VIcon(R.drawable.ic_t_pill, size = 20.dp, tint = c.health)
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(s.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Muted(if (s.perWeek >= 7) tr("{0} {1} am Tag", s.perDay, FORMS[s.form] ?: tr("Einheiten"))
                else tr("{0} {1} · {2}× pro Woche ({3}/{4})", s.perDay, FORMS[s.form] ?: tr("Einheiten"), s.perWeek, s.weekCount, s.perWeek))
        }
        Stepper("${s.count}/${s.perDay}", done = s.done, minusEnabled = s.count > 0,
            onMinus = { DayRepo.adjustSupplement(ctx, day, s.id, -1) }, onPlus = { DayRepo.adjustSupplement(ctx, day, s.id, 1) }, color = c.health)
    }
}

// --- Check-in -----------------------------------------------------------------------------------

/**
 * Mood and energy in one tap each; symptoms and notes fold out ([expanded]: always open, as on the
 * journal tab). Shared by the overview and the journal tab, both on [DayRepo]'s day state.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun CheckinCard(day: String, expanded: Boolean = false) {
    val ctx = LocalContext.current
    val state by DayRepo.state.collectAsState()
    val d = state.view(day)
    val symptoms = state.symptoms
    val pendingIds = state.pending.mapNotNull { (it as? PendingOp.AddCheckin)?.checkin?.id }.toSet()
    var symptom by rememberSaveable { mutableStateOf("") }
    var severity by rememberSaveable { mutableStateOf(2) }
    var note by rememberSaveable { mutableStateOf("") }
    var more by rememberSaveable { mutableStateOf(expanded) }
    val latest = { kind: String -> d.checkins.lastOrNull { it.kind == kind }?.value?.toInt() }

    VCard(tr("Befinden"), R.drawable.ic_t_mood_smile, tint = Vita.colors.mind) {
        Scale(tr("Stimmung"), MOOD, latest("mood")) { DayRepo.addCheckin(ctx, day, "mood", it.toDouble(), null, null) }
        Scale(tr("Energie"), ENERGY, latest("energy")) { DayRepo.addCheckin(ctx, day, "energy", it.toDouble(), null, null) }
        if (!expanded) TextButton(onClick = { more = !more }, contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)) {
            Text(if (more) tr("Weniger") else tr("Symptom oder Notiz hinzufügen"))
        }
        if (more) {
            OutlinedTextField(
                value = symptom, onValueChange = { symptom = it.take(120) }, singleLine = true, label = { Text(tr("Symptom")) },
                placeholder = { Text(tr("z. B. Kopfschmerzen")) }, modifier = Modifier.fillMaxWidth(),
            )
            val suggestions = symptoms.filter { it.contains(symptom.trim(), ignoreCase = true) && it != symptom }.take(6)
            if (suggestions.isNotEmpty()) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    suggestions.forEach { s -> SuggestionChip(onClick = { symptom = s }, label = { Text(s) }) }
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(tr("Stärke"), style = MaterialTheme.typography.bodySmall)
                (1..5).forEach { n -> FilterChip(selected = severity == n, onClick = { severity = n }, label = { Text("${n}") }) }
            }
            OutlinedButton(enabled = symptom.isNotBlank(), onClick = {
                DayRepo.addCheckin(ctx, day, "symptom", severity.toDouble(), symptom.trim(), null)
                symptom = ""
            }) { Text(tr("Symptom speichern")) }
            OutlinedTextField(value = note, onValueChange = { note = it.take(4000) }, modifier = Modifier.fillMaxWidth(), minLines = 2,
                label = { Text(tr("Notiz")) })
            OutlinedButton(enabled = note.isNotBlank(), onClick = {
                DayRepo.addCheckin(ctx, day, "note", null, null, note.trim())
                note = ""
            }) { Text(tr("Notiz speichern")) }
        }
        if (d.checkins.isNotEmpty()) {
            var showEntries by rememberSaveable { mutableStateOf(false) }
            FoldToggle(tr("Einträge ({0})", d.checkins.size), showEntries) { showEntries = !showEntries }
            if (showEntries) d.checkins.asReversed().forEach { c ->
                EntryRow(clock(c.ts), describe(c), c.id in pendingIds, onDelete = { DayRepo.deleteCheckin(ctx, c.id) })
            }
        }
    }
}

@Composable
private fun Scale(title: String, labels: List<String>, current: Int?, onPick: (Int) -> Unit) {
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(title, style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f))
            current?.let { Muted(labels.getOrNull(it - 1).orEmpty()) }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            (1..5).forEach { n ->
                FilterChip(selected = current == n, onClick = { onPick(n) }, label = { Text("${n}") }, modifier = Modifier.weight(1f))
            }
        }
    }
}

private fun describe(c: Checkin): String = when (c.kind) {
    "mood" -> tr("Stimmung {0}/5 · {1}", c.value?.toInt(), MOOD.getOrNull((c.value?.toInt() ?: 0) - 1).orEmpty())
    "energy" -> tr("Energie {0}/5 · {1}", c.value?.toInt(), ENERGY.getOrNull((c.value?.toInt() ?: 0) - 1).orEmpty())
    "symptom" -> "${c.label}${c.value?.let { tr(" · Stärke {0}/5", it.toInt()) } ?: ""}"
    "rpe" -> tr("Trainingsanstrengung {0}/10", c.value?.toInt())
    else -> c.note ?: c.kind
}

/** "22:07 ✓", "22:41 · +21 min", "keins ✓" for clock habits; otherwise ✓ / offen / – as before. */
internal fun automaticStatus(h: Habit): String {
    if (h.done == null) return "–"
    if (h.none) return tr("keins ✓")
    val shown = h.display ?: return if (h.done) "✓" else tr("offen")
    if (h.done) return "${shown} ✓"
    val delta = h.delta?.toInt() ?: 0
    return if (delta != 0) tr("{0} · {1}{2} min", shown, if (delta > 0) "+" else "−", kotlin.math.abs(delta)) else shown
}

// --- Day values -------------------------------------------------------------------------------

/** Key values that come from logging, not from a watch. */
private val NO_WATCH_KPIS = setOf("nutrition.energy", "weight")

/** First run: the setup (profile, areas, AI) lives in the WebUI. */
@Composable
private fun SetupHint() {
    val ctx = LocalContext.current
    VCard(tr("Einrichtung abschließen"), R.drawable.ic_t_sparkles, tint = Vita.colors.day) {
        Text(tr("Profil, Ziele und angezeigte Bereiche richtest du einmal in der Web-App ein."),
            style = MaterialTheme.typography.bodyMedium)
        OutlinedButton(onClick = { openWeb(ctx, "#/setup") }) { Text(tr("Web-App öffnen")) }
    }
}

private data class Kpi(val label: String, val value: String, val unit: String?, val sub: String?, val icon: Int)

/** The day's key values at a glance (without a watch: what was logged); the rest is in the WebUI. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun MetricsCard(d: TodayData, day: String, watch: Boolean) {
    val kpis = buildList {
        if (watch) d.sleep.asleepMinutes?.let { add(Kpi(tr("Schlaf"), "${it / 60}:${"%02d".format(it % 60)}", "h", null, R.drawable.ic_t_moon)) }
        listOf(
            Triple("daily.sleep_score", tr("Schlafscore"), R.drawable.ic_t_zzz), Triple("resting_heart_rate", tr("Ruhepuls"), R.drawable.ic_t_heart),
            Triple("steps", tr("Schritte"), R.drawable.ic_t_shoe), Triple("active_energy", tr("Aktive Energie"), R.drawable.ic_t_flame),
            Triple("body_energy", tr("Body Energy max"), R.drawable.ic_t_battery_3), Triple("stress", tr("Ø Stress"), R.drawable.ic_t_bolt),
            Triple("nutrition.energy", tr("Aufnahme"), R.drawable.ic_t_salad), Triple("weight", tr("Gewicht"), R.drawable.ic_t_scale),
        ).forEach { (key, label, icon) ->
            // intake and weight only stand in for the watch values; with a watch they are on the nutrition tab
            if (watch == (key in NO_WATCH_KPIS)) return@forEach
            val m = d.metrics[key] ?: return@forEach
            val weight = key == "weight"
            val conv = { x: Double -> if (weight) BodyUnits.shown(x) else x }
            val v = m.value?.let(conv) ?: return@forEach
            add(Kpi(label, num(v, m.decimals), if (weight) BodyUnits.unit else m.unit?.takeIf { it != "score" && it != "steps" },
                m.baseline?.let { tr("Ø 28 T: {0}", num(conv(it), m.decimals)) }, icon))
        }
    }
    VCard(tr("Tageswerte"), R.drawable.ic_t_layout_grid, tint = Vita.colors.day) {
        if (kpis.isEmpty() && d.activities.isEmpty()) Muted(tr("Noch keine Werte für diesen Tag."))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp), maxItemsInEachRow = 2) {
            kpis.forEach { k -> StatTile(k.label, k.value, Modifier.weight(1f), unit = k.unit, sub = k.sub, icon = k.icon) }
            if (kpis.size % 2 == 1) Spacer(Modifier.weight(1f))
        }
        d.activities.forEach { a ->
            val parts = listOfNotNull(
                a.elapsedS?.let { "${(it / 60).toInt()} min" },
                a.distanceM?.takeIf { it > 0 }?.let { "${num(it / 1000, 2)} km" },
                a.kcal?.let { "${it.toInt()} kcal" },
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                VIcon(R.drawable.ic_t_run, size = 16.dp, tint = Vita.colors.train)
                Spacer(Modifier.width(8.dp))
                Text("${clock(a.start)}  ${a.name}${if (parts.isEmpty()) "" else " · " + parts.joinToString(" · ")}",
                    style = MaterialTheme.typography.bodyMedium)
            }
        }
        WebLink(tr("Details in der WebUI"), if (day == DayRepo.today) "#/" else "#/day/${day}")
    }
}

private fun num(v: Double, decimals: Int): String = NumberFormat.getNumberInstance(I18n.locale).apply {
    maximumFractionDigits = decimals
    minimumFractionDigits = decimals
}.format(v)

/** Local wall-clock time of a server/ISO timestamp. */
private fun clock(iso: String): String = runCatching {
    OffsetDateTime.parse(iso).atZoneSameInstant(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("HH:mm"))
}.getOrDefault("")
