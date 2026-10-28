package de.vitalocal.companion

import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import de.vitalocal.companion.ui.CardShape
import de.vitalocal.companion.ui.Glyph
import de.vitalocal.companion.ui.ICON_PICKER
import de.vitalocal.companion.ui.Muted
import de.vitalocal.companion.ui.NumberStyle
import de.vitalocal.companion.ui.ScreenHeader
import de.vitalocal.companion.ui.VCard
import de.vitalocal.companion.ui.VIcon
import de.vitalocal.companion.ui.Vita
import de.vitalocal.companion.ui.WebLink
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.UUID

// --- achievements (full rewards, online with a cache) -------------------------------------------------

/** One achievement: earned ([unlockedOn]) or with its progress towards [target]. */
data class Achievement(
    val key: String, val icon: String, val title: String, val tier: String, val habit: String?,
    val unlockedOn: String?, val progress: Double, val target: Double, val new: Boolean,
)

data class AchievementState(val list: List<Achievement> = emptyList(), val loadedAt: Long = 0, val error: String? = null)

/** The habits tab's achievements from `GET /habits/rewards`, cached so they show offline too. */
object AchievementRepo {
    private val _state = MutableStateFlow(AchievementState())
    val state: StateFlow<AchievementState> = _state.asStateFlow()

    private fun file(context: Context) = File(context.filesDir, "achievements.json")

    private fun parse(o: JSONObject) = o.optJSONArray("achievements").objects().map {
        Achievement(it.optString("key"), it.optString("icon"), it.optString("title"), it.optString("tier", "bronze"),
            it.optStringOrNull("habit"), it.optStringOrNull("unlocked_on"), it.optDouble("progress", 0.0),
            it.optDouble("target", 1.0), it.optBoolean("new"))
    }

    suspend fun load(context: Context) {
        if (_state.value.loadedAt == 0L) runCatching {
            val o = JSONObject(file(context).readText())
            _state.value = AchievementState(parse(o), o.optLong("at"))
        }
        val settings = Settings(context)
        if (!settings.isConfigured) return
        try {
            val o = withContext(Dispatchers.IO) { Api(settings.serverUrl, settings.token).habitRewards() }
            val at = System.currentTimeMillis()
            runCatching { file(context).writeText(JSONObject().put("achievements", o.optJSONArray("achievements")).put("at", at).toString()) }
            _state.value = AchievementState(parse(o), at)
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = if (e is IOException && e !is ApiException) tr("Server nicht erreichbar") else e.describe())
        }
    }

    suspend fun markSeen(context: Context) {
        val settings = Settings(context)
        runCatching { withContext(Dispatchers.IO) { Api(settings.serverUrl, settings.token).markRewardsSeen() } }
        _state.value = _state.value.copy(list = _state.value.list.map { it.copy(new = false) })
        DayRepo.refresh(context)
    }
}

private val RANKS = mapOf("bronze" to tr("Bronze"), "silver" to tr("Silber"), "gold" to tr("Gold"), "platinum" to tr("Platin"), "diamond" to tr("Diamant"),
    "master" to tr("Meister"), "grandmaster" to tr("Großmeister"), "legend" to tr("Legende"))

// --- screen ---------------------------------------------------------------------------------------------

/**
 * Tab "Gewohnheiten": flames and level, today's habits (automatic ones too) and newly earned
 * achievements; new habits are created here (from a template or by hand). Garden, challenges and
 * all achievements are in the WebUI.
 */
@Composable
fun HabitsScreen() {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val dayState by DayRepo.state.collectAsState()
    val ach by AchievementRepo.state.collectAsState()
    val day = DayRepo.today
    val d = dayState.view(day)
    var creating by rememberSaveable { mutableStateOf(false) }

    LaunchedEffect(Unit) { DayRepo.refresh(ctx, day) }
    LifecycleResumeEffect(Unit) {
        scope.launch { AchievementRepo.load(ctx) }
        onPauseOrDispose { }
    }

    PullToRefreshBox(isRefreshing = dayState.refreshing, onRefresh = { scope.launch { DayRepo.refresh(ctx, day); AchievementRepo.load(ctx) } },
        modifier = Modifier.fillMaxSize()) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp).padding(bottom = 72.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            val r = d.rewards
            ScreenHeader(tr("Gewohnheiten"), r?.let { tr("Level {0} · {1}", it.level, it.levelTitleDe) }, R.drawable.ic_t_plant_2, Vita.colors.habit)
            r?.let { RewardBanner(it) }
            NewAchievements(ach, onSeen = { scope.launch { AchievementRepo.markSeen(ctx) } })
            VCard(tr("Heute"), R.drawable.ic_t_list_check, tint = Vita.colors.habit, trailing = {
                val manual = d.habits.filter { !it.automatic }
                if (manual.isNotEmpty()) Text("${manual.count { it.done == true }}/${manual.size}", style = MaterialTheme.typography.labelMedium)
            }) {
                if (d.habits.isEmpty()) Muted(tr("Noch keine Gewohnheiten – lege unten die erste an."))
                d.habits.forEach { HabitRow(it, day) }
            }
        }
        ExtendedFloatingActionButton(
            onClick = { creating = true }, modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
            containerColor = Vita.colors.habit, contentColor = MaterialTheme.colorScheme.surface,
            icon = { VIcon(R.drawable.ic_t_plus) }, text = { Text(tr("Neue Gewohnheit")) },
        )
    }
    if (creating) NewHabitSheet(onDone = { created ->
        creating = false
        if (created) scope.launch { DayRepo.refresh(ctx, day); AchievementRepo.load(ctx) }
    })
}

/** Flames, level and perfect days on a warm banner (the playful part of the app). */
@Composable
private fun RewardBanner(r: HabitRewards) {
    val c = Vita.colors
    Column(
        Modifier.fillMaxWidth().clip(CardShape)
            .background(Brush.linearGradient(listOf(c.flameSoft, c.okSoft)))
            .border(1.dp, c.border, CardShape)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            VIcon(R.drawable.ic_t_flame, size = 34.dp, tint = c.flame)
            Spacer(Modifier.width(8.dp))
            Column(Modifier.weight(1f)) {
                Text("${r.flames}", style = NumberStyle)
                Muted(if (r.flamesToday > 0) tr("Flammen · heute +{0}", r.flamesToday) else tr("Flammen"))
            }
            Column(horizontalAlignment = Alignment.End) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    VIcon(R.drawable.ic_t_star, size = 16.dp, tint = c.gold)
                    Text(" ${r.perfectStreak}", style = MaterialTheme.typography.titleMedium)
                }
                Muted(if (r.perfectToday) tr("heute perfekt") else tr("perfekte Tage"))
            }
        }
        LinearProgressIndicator(progress = { r.levelProgress.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth().height(8.dp),
            color = c.flame, trackColor = MaterialTheme.colorScheme.surface, drawStopIndicator = {})
        Muted(tr("Level {0} · {1} · noch {2} bis Level {3}", r.level, r.levelTitleDe, r.toNextLevel, r.level + 1))
        r.bestStreak?.let { b ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Glyph(b.icon, size = 14.dp, tint = c.habit)
                Muted(tr("  Längste Serie: {0}, {1}", b.label, b.span))
            }
        }
        WebLink(tr("Garten, Challenges und Erfolge in der WebUI"), "#/habits/garden")
    }
}

/** Achievements earned since the last look, until "Super!" marks them seen. */
@Composable
private fun NewAchievements(s: AchievementState, onSeen: () -> Unit) {
    val fresh = s.list.filter { it.new }
    if (fresh.isEmpty()) return
    VCard(tr("Neu freigeschaltet"), R.drawable.ic_t_medal_2, tint = Vita.colors.gold) {
        BadgeRow(fresh)
        TextButton(onClick = onSeen) { Text(tr("Super!")) }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BadgeRow(list: List<Achievement>) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        list.forEach { a ->
            Column(Modifier.width(72.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Medal(a, locked = false)
                Text(a.title, style = MaterialTheme.typography.labelSmall, maxLines = 2, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center)
                Muted(RANKS[a.tier] ?: a.tier)
            }
        }
    }
}

private val TIER_COLORS = mapOf(
    "bronze" to 0xFFB0703C, "silver" to 0xFF8B95A1, "gold" to 0xFFC99A06, "platinum" to 0xFF6B8FA3, "diamond" to 0xFF0EA5E9,
    "master" to 0xFF9333EA, "grandmaster" to 0xFFDB2777, "legend" to 0xFFDC2626,
)

@Composable
private fun Medal(a: Achievement, locked: Boolean) {
    val tier = androidx.compose.ui.graphics.Color(TIER_COLORS[a.tier] ?: 0xFFB0703C)
    val color = if (locked) Vita.colors.faint else tier
    Box(
        Modifier.size(44.dp).clip(CircleShape).background(color.copy(alpha = 0.14f)).border(2.dp, color, CircleShape),
        contentAlignment = Alignment.Center,
    ) { Glyph(a.icon, size = 22.dp, tint = color) }
}

// --- new habit ------------------------------------------------------------------------------------------

/** Template from `GET /habits/templates` (names are English source strings; German below). */
private data class HabitTemplate(val key: String, val name: String, val icon: String?, val category: String, val body: JSONObject, val added: Boolean)

private val TEMPLATE_DE = mapOf(
    "Stretch" to tr("Dehnen"), "Walk outside" to tr("Draußen spazieren"), "Meditate" to tr("Meditieren"), "Write a journal" to tr("Tagebuch schreiben"),
    "Three good things" to tr("Drei gute Dinge"), "Read" to tr("Lesen"), "Learn something new" to tr("Etwas Neues lernen"),
    "Breathing exercise" to tr("Atemübung"), "Call friends or family" to tr("Freunde oder Familie anrufen"),
    "No screens before bed" to tr("Kein Bildschirm vor dem Schlafen"), "Morning daylight" to tr("Morgens Tageslicht"),
    "No sweets" to tr("Keine Süßigkeiten"), "No alcohol" to tr("Kein Alkohol"), "No snacks after dinner" to tr("Keine Snacks nach dem Abendessen"),
    "Floss" to tr("Zahnseide"), "Cold shower" to tr("Kalt duschen"), "Tidy up for 10 minutes" to tr("10 Minuten aufräumen"), "Plan tomorrow" to tr("Morgen planen"),
)
private val CATEGORY_DE = mapOf("Movement" to tr("Bewegung"), "Mind" to tr("Geist"), "Sleep" to tr("Schlaf"), "Nutrition" to tr("Ernährung"), "Everyday" to tr("Alltag"))

/** Create a habit: one tap on a template, or name, icon, type and goal by hand. Needs the server. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun NewHabitSheet(onDone: (Boolean) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var mode by rememberSaveable { mutableStateOf(0) }  // 0 templates, 1 own
    var templates by remember { mutableStateOf<List<HabitTemplate>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var name by rememberSaveable { mutableStateOf("") }
    var icon by rememberSaveable { mutableStateOf(ICON_PICKER.first()) }
    var count by rememberSaveable { mutableStateOf(false) }
    var target by rememberSaveable { mutableStateOf("") }
    var unit by rememberSaveable { mutableStateOf("") }
    var perWeek by rememberSaveable { mutableStateOf(7) }
    var hard by rememberSaveable { mutableStateOf(false) }
    val settings = remember { Settings(ctx) }

    LaunchedEffect(Unit) {
        runCatching {
            withContext(Dispatchers.IO) { Api(settings.serverUrl, settings.token).habitTemplates() }.objects().map {
                HabitTemplate(it.optString("key"), it.optString("name"), it.optStringOrNull("icon"), it.optString("category"), it, it.optBoolean("added"))
            }
        }.onSuccess { templates = it }.onFailure { error = if (it is IOException && it !is ApiException) tr("Server nicht erreichbar") else it.describe() }
    }

    fun create(body: JSONObject) {
        busy = true
        scope.launch {
            try {
                withContext(Dispatchers.IO) { Api(settings.serverUrl, settings.token).createHabit(body) }
                onDone(true)
            } catch (e: Exception) {
                error = if (e is IOException && e !is ApiException) tr("Server nicht erreichbar – neue Gewohnheiten brauchen die Verbindung.") else e.describe()
                busy = false
            }
        }
    }

    ModalBottomSheet(onDismissRequest = { onDone(false) }, sheetState = sheet) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).padding(bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(tr("Neue Gewohnheit"), style = MaterialTheme.typography.titleLarge)
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                listOf(tr("Vorlage"), tr("Eigene")).forEachIndexed { i, label ->
                    SegmentedButton(selected = mode == i, onClick = { mode = i }, shape = SegmentedButtonDefaults.itemShape(i, 2)) { Text(label) }
                }
            }
            error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
            if (mode == 0) {
                val list = templates
                if (list == null && error == null) Muted(tr("Lädt …"))
                list?.groupBy { it.category }?.forEach { (cat, items) ->
                    Text(CATEGORY_DE[cat] ?: cat, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    items.forEach { tp ->
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                                .clickable(enabled = !tp.added && !busy) {
                                    val b = tp.body
                                    create(JSONObject().put("id", UUID.randomUUID().toString()).put("name", TEMPLATE_DE[tp.name] ?: tp.name)
                                        .put("icon", tp.icon ?: JSONObject.NULL).put("template", tp.key).put("kind", b.optString("kind", "check"))
                                        .put("unit", b.opt("unit") ?: JSONObject.NULL).put("target", b.opt("target") ?: JSONObject.NULL)
                                        .put("per_week", b.optInt("per_week", 7)).put("metric", b.opt("metric") ?: JSONObject.NULL)
                                        .put("min_value", b.opt("min_value") ?: JSONObject.NULL).put("max_value", b.opt("max_value") ?: JSONObject.NULL))
                                }
                                .padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Box(Modifier.size(38.dp).clip(CircleShape).background(Vita.colors.okSoft), contentAlignment = Alignment.Center) {
                                Glyph(tp.icon, size = 20.dp, tint = Vita.colors.habit)
                            }
                            Spacer(Modifier.width(12.dp))
                            Text(TEMPLATE_DE[tp.name] ?: tp.name, Modifier.weight(1f))
                            if (tp.added) Muted(tr("schon dabei")) else VIcon(R.drawable.ic_t_plus, tint = Vita.colors.habit)
                        }
                    }
                }
            } else {
                OutlinedTextField(name, { name = it.take(80) }, label = { Text(tr("Name")) }, placeholder = { Text(tr("z. B. Dehnen, Lesen")) },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                Text(tr("Symbol"), style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    ICON_PICKER.forEach { e ->
                        val sel = e == icon
                        Box(
                            Modifier.size(42.dp).clip(RoundedCornerShape(10.dp))
                                .background(if (sel) Vita.colors.okSoft else Vita.colors.surface2)
                                .border(1.dp, if (sel) Vita.colors.habit else androidx.compose.ui.graphics.Color.Transparent, RoundedCornerShape(10.dp))
                                .clickable { icon = e },
                            contentAlignment = Alignment.Center,
                        ) { Glyph(e, size = 20.dp, tint = if (sel) Vita.colors.habit else Vita.colors.muted) }
                    }
                }
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                    listOf(tr("Abhaken"), tr("Zählen")).forEachIndexed { i, label ->
                        SegmentedButton(selected = count == (i == 1), onClick = { count = i == 1 }, shape = SegmentedButtonDefaults.itemShape(i, 2)) { Text(label) }
                    }
                }
                if (count) Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(target, { target = it.filter { ch -> ch.isDigit() || ch == ',' || ch == '.' }.take(6) }, label = { Text(tr("Tagesziel")) },
                        singleLine = true, modifier = Modifier.weight(1f),
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal))
                    OutlinedTextField(unit, { unit = it.take(20) }, label = { Text(tr("Einheit")) }, placeholder = { Text(tr("Seiten")) },
                        singleLine = true, modifier = Modifier.weight(1f))
                }
                Text(tr("Wie oft"), style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    (7 downTo 1).forEach { n ->
                        FilterChip(selected = perWeek == n, onClick = { perWeek = n }, label = { Text(if (n == 7) tr("täglich") else tr("{0}× / Woche", n)) })
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(tr("Schwere Gewohnheit"))
                        Muted(tr("Zwei Flammen pro erledigtem Tag"))
                    }
                    Switch(checked = hard, onCheckedChange = { hard = it })
                }
                val goal = target.replace(',', '.').toDoubleOrNull()
                Button(
                    enabled = !busy && name.isNotBlank() && (!count || (goal != null && goal > 0)),
                    onClick = {
                        create(JSONObject().put("id", UUID.randomUUID().toString()).put("name", name.trim()).put("icon", icon)
                            .put("kind", if (count) "count" else "check").put("unit", if (count && unit.isNotBlank()) unit.trim() else JSONObject.NULL)
                            .put("target", if (count) goal else JSONObject.NULL).put("per_week", perWeek).put("hard", hard))
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (busy) tr("Speichere …") else tr("Anlegen")) }
                Muted(tr("Automatische Gewohnheiten (aus Uhr-Daten) legst du in der WebUI an."))
            }
        }
    }
}
