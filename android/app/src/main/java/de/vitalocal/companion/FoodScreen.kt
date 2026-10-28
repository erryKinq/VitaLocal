package de.vitalocal.companion

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import de.vitalocal.companion.ui.CardShape
import de.vitalocal.companion.ui.ScreenHeader
import de.vitalocal.companion.ui.VCard
import de.vitalocal.companion.ui.VIcon
import de.vitalocal.companion.ui.DAYS_BACK
import de.vitalocal.companion.ui.DayArrows
import de.vitalocal.companion.ui.dayLabel
import de.vitalocal.companion.ui.Vita
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException
import java.text.NumberFormat
import java.time.LocalDate
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.UUID
import kotlin.math.roundToInt

/** Tab "Ernährung": the diary of one of the last 7 days, totals against the goals, drinks and weight. */
@Composable
fun FoodScreen() {
    val ctx = LocalContext.current
    val dayState by DayRepo.state.collectAsState()
    val food by FoodRepo.state.collectAsState()
    val scope = rememberCoroutineScope()
    var day by rememberSaveable { mutableStateOf(DayRepo.today) }
    var addFor by remember { mutableStateOf<Int?>(null) }
    var editing by remember { mutableStateOf<FoodEntry?>(null) }
    var saveAs by remember { mutableStateOf<Pair<String, List<FoodEntry>>?>(null) }
    var catalogOpen by remember { mutableStateOf(false) }
    var goalsOpen by remember { mutableStateOf(false) }
    val d = food.view(day, dayState.pending)
    val waiting = dayState.pending.count { it.isFood }

    LaunchedEffect(day) { FoodRepo.refresh(ctx, day) }
    LaunchedEffect(day) { DayRepo.refresh(ctx, day) }
    val settings = remember { Settings(ctx) }
    val dd = dayState.view(day)
    val drinkPending = dayState.pending.mapNotNull {
        when (it) { is PendingOp.AddDrink -> it.drink.id; is PendingOp.UpdateDrink -> it.drink.id; else -> null }
    }.toSet()
    LifecycleResumeEffect(day) {
        if (LocalDate.parse(day).isBefore(LocalDate.now().minusDays(DAYS_BACK))) day = DayRepo.today
        scope.launch { FoodRepo.refresh(ctx, day) }
        onPauseOrDispose { }
    }
    // the outbox sent the last food write: reload so the server's numbers replace the local ones
    LaunchedEffect(waiting) { if (waiting == 0 && food.sent.isNotEmpty()) FoodRepo.refresh(ctx, day) }

    PullToRefreshBox(isRefreshing = food.refreshing, onRefresh = { scope.launch { FoodRepo.refresh(ctx, day); DayRepo.refresh(ctx, day) } }) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            DayNav(day, onDay = { day = it }, onCatalog = { catalogOpen = true })
            val status = listOfNotNull(
                food.error,
                when (waiting) { 0 -> null; 1 -> tr("1 Eintrag wartet auf Übertragung"); else -> tr("{0} Einträge warten auf Übertragung", waiting) },
            )
            if (status.isNotEmpty()) {
                Text(status.joinToString(" · "), style = MaterialTheme.typography.bodySmall,
                    color = if (food.error != null) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
            }
            TotalsCard(d, dd.energy ?: dd.metrics["resting_metabolic_rate"]?.value?.let { r ->
                dd.metrics["active_energy"]?.value?.let { a -> Energy(r + a, r, a, "watch") } }, onGoals = { goalsOpen = true })
            if (Caps.state.collectAsState().value.on("drinks")) DrinksCard(dd, dd.drinkGoalMl(settings.waterGoal), drinkPending, day)
            d.meals.forEachIndexed { i, name ->
                MealCard(name, d.mealEntries(i), onAdd = { addFor = i }, onEntry = { editing = it }, onSaveTemplate = { saveAs = name to d.mealEntries(i) })
            }
            // entries of a meal the list does not name (more meals on the server than here)
            d.entries.filter { it.mealIndex >= d.meals.size }.takeIf { it.isNotEmpty() }?.let {
                MealCard(tr("Weitere"), it, onAdd = null, onEntry = { e -> editing = e })
            }
            WeightCard(dd, dayState.pending.any { it is PendingOp.AddBodyEntry && it.day == day }, day)
        }
    }

    addFor?.let { meal -> AddFoodSheet(day, meal, d.meals, onDone = { addFor = null }) }
    editing?.let { e -> EditEntrySheet(e, d.meals, onDone = { editing = null }) }
    saveAs?.let { (name, entries) -> SaveTemplateSheet(name, entries, onDone = { saveAs = null }) }
    if (catalogOpen) CatalogSheet(onDone = { catalogOpen = false })
    if (goalsOpen) GoalsSheet(d.goals, onDone = { changed ->
        goalsOpen = false
        if (changed) scope.launch { FoodRepo.refresh(ctx, day) }
    })
}

/** The foods of a meal as a new template, still to be named and adjusted. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SaveTemplateSheet(mealName: String, entries: List<FoodEntry>, onDone: () -> Unit) {
    val items = entries.filter { it.foodId != null && it.amount != null }
        .map { TemplateItem(it.foodId!!, it.name, it.amount!!, it.quantity, it.unit, it.nutrients) }
    ModalBottomSheet(onDismissRequest = onDone, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        TemplateEditor(null, onDone = onDone, initial = items, initialName = mealName)
    }
}

@Composable
private fun DayNav(day: String, onDay: (String) -> Unit, onCatalog: () -> Unit) {
    ScreenHeader(tr("Ernährung"), dayLabel(day), R.drawable.ic_t_salad, Vita.colors.food) {
        DayArrows(day, onDay)
        // own foods, dishes and templates
        IconButton(onClick = onCatalog) { VIcon(R.drawable.ic_t_bowl) }
    }
}

/** Sub-rows of a total: tap fat → saturated fat, carbohydrates → sugars. */
private val TOTAL_DETAILS = mapOf("fat" to listOf("saturated-fat" to tr("davon ges. Fettsäuren")), "carbohydrates" to listOf("sugars" to tr("davon Zucker")))

@Composable
private fun TotalsCard(d: NutritionDay, energy: Energy?, onGoals: () -> Unit) {
    val burned = energy?.total
    var open by rememberSaveable { mutableStateOf(setOf<String>()) }
    VCard(tr("Tagessumme"), R.drawable.ic_t_tools_kitchen_2, tint = Vita.colors.food, trailing = {
        TextButton(onClick = onGoals) { VIcon(R.drawable.ic_t_target, size = 16.dp); Text(tr(" Ziele")) }
    }) {
        // energy balance against the burn (watch, or estimated from profile + workouts): deficit or surplus
        val kcal = d.total("calories").first
        if (burned != null && kcal != null) {
            val bal = (kcal - burned).toInt()
            Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Vita.colors.surface2).padding(horizontal = 12.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(tr("Bilanz"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(tr("{0} · verbrannt ≈ {1} kcal", if (bal < 0) tr("Defizit") else tr("Überschuss"), num(burned, 0)) +
                        if (energy.estimated) tr(" (geschätzt)") else "",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Text("${if (bal > 0) "+" else if (bal < 0) "−" else "±"}${num(kotlin.math.abs(bal).toDouble(), 0)} kcal",
                    style = MaterialTheme.typography.titleLarge)
            }
        }
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            MAIN_NUTRIENTS.forEach { key ->
                val details = TOTAL_DETAILS[key]
                val expanded = key in open
                TotalRow(NUTRIENT_LABEL[key] ?: key, d.total(key).first, d.goal(key), if (key == "calories") "kcal" else "g",
                    toggle = details?.let { { open = if (expanded) open - key else open + key } }, expanded = expanded)
                if (details != null && expanded) {
                    details.forEach { (k, label) ->
                        Row(Modifier.padding(start = 16.dp)) {
                            Column {
                                TotalRow(label, d.total(k).first, d.goal(k), "g", small = true)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun TotalRow(label: String, value: Double?, goal: NutrientGoal?, unit: String, toggle: (() -> Unit)? = null,
                     expanded: Boolean = false, small: Boolean = false) {
    val style = if (small) MaterialTheme.typography.bodySmall else MaterialTheme.typography.bodyMedium
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth().then(if (toggle != null) Modifier.clickable(onClick = toggle) else Modifier)) {
            Text(label, modifier = Modifier.weight(1f), style = style,
                color = if (small) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
            Text((value?.let { "${num(it, if (it < 10) 1 else 0)} ${unit}" } ?: "–") + (goal?.let { tr(" · Ziel {0}", goalText(it)) } ?: ""), style = style)
            if (toggle != null) {
                VIcon(if (expanded) R.drawable.ic_t_chevron_up else R.drawable.ic_t_chevron_down, size = 16.dp,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        val ref = goal?.max ?: goal?.min
        if (goal != null && ref != null && ref > 0) {
            val within = value != null && (goal.min == null || value >= goal.min) && (goal.max == null || value <= goal.max)
            LinearProgressIndicator(
                progress = { ((value ?: 0.0) / ref).toFloat().coerceIn(0f, 1f) },
                modifier = Modifier.fillMaxWidth().height(if (small) 4.dp else 6.dp),
                color = if (within) Vita.colors.ok else Vita.colors.warn, trackColor = Vita.colors.surface2, drawStopIndicator = {},
            )
        }
    }
}

private fun goalText(g: NutrientGoal): String = when {
    g.min != null && g.max != null -> "${num(g.min, 0)}–${num(g.max, 0)}"
    g.min != null -> "≥ ${num(g.min, 0)}"
    g.max != null -> "≤ ${num(g.max, 0)}"
    else -> "–"
}

@Composable
private fun MealCard(
    name: String, entries: List<FoodEntry>, onAdd: (() -> Unit)?, onEntry: (FoodEntry) -> Unit,
    onSaveTemplate: (() -> Unit)? = null,
) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    var open by rememberSaveable(name) { mutableStateOf(false) }
    Card(Modifier.fillMaxWidth(), shape = CardShape, colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        border = BorderStroke(1.dp, Vita.colors.border)) {
        Column(Modifier.padding(start = 16.dp, end = 8.dp, top = 8.dp, bottom = 8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(name, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                val kcal = entries.mapNotNull { it.kcal }.sum()
                // tap the kcal: what the meal is made of
                if (entries.isNotEmpty()) TextButton(onClick = { open = !open }) {
                    Text("${num(kcal, 0)} kcal", color = muted, style = MaterialTheme.typography.bodySmall)
                    VIcon(if (open) R.drawable.ic_t_chevron_up else R.drawable.ic_t_chevron_down, size = 16.dp, tint = muted)
                }
                if (onAdd != null) IconButton(onClick = onAdd) { VIcon(R.drawable.ic_t_plus, tint = Vita.colors.food) }
            }
            if (open && entries.isNotEmpty()) {
                MealBreakdown(entries)
                if (onSaveTemplate != null && entries.any { it.foodId != null }) {
                    TextButton(onClick = onSaveTemplate) { Text(tr("Als Vorlage speichern")) }
                }
            }
            entries.forEach { e ->
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth().clickable(enabled = e.editable) { onEntry(e) }.padding(vertical = 6.dp),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(e.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(listOfNotNull(e.amountText.takeIf { it.isNotEmpty() }, e.brand).joinToString(" · "),
                            style = MaterialTheme.typography.bodySmall, color = muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    if (e.pending) VIcon(R.drawable.ic_t_clock, size = 14.dp, tint = muted)
                    Text(e.kcal?.let { "${num(it, 0)} kcal" } ?: "–", modifier = Modifier.padding(end = 8.dp))
                }
            }
        }
    }
}

private val MACROS = listOf(Triple("fat", tr("Fett"), 9.0), Triple("carbohydrates", tr("Kohlenhydrate"), 4.0), Triple("proteins", tr("Eiweiß"), 4.0))
private val DETAILS = listOf("saturated-fat" to tr("Ges. Fettsäuren"), "sugars" to tr("Zucker"), "fiber" to tr("Ballaststoffe"), "salt" to tr("Salz"))

/** Energy, macros in grams and as share of the energy, and further nutrients (undeclared = 0). */
@Composable
fun MealBreakdown(entries: List<FoodEntry>) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    fun sum(k: String) = entries.sumOf { it.nutrients[k] ?: 0.0 }
    val energy = MACROS.map { (k, _, f) -> sum(k) * f }
    val all = energy.sum().takeIf { it > 0 } ?: 1.0
    val colors = listOf(androidx.compose.ui.graphics.Color(0xFF7C3AED), androidx.compose.ui.graphics.Color(0xFFD97706), androidx.compose.ui.graphics.Color(0xFF0F766E))
    Column(Modifier.fillMaxWidth().padding(end = 8.dp, bottom = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp))) {
            energy.forEachIndexed { i, e ->
                if (e > 0) Box(Modifier.weight((e / all).toFloat()).fillMaxHeight().background(colors[i]))
            }
        }
        MACROS.forEachIndexed { i, (k, label, _) ->
            Row {
                Text("●  ", color = colors[i], style = MaterialTheme.typography.bodySmall)
                Text(label, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                Text("${num(sum(k), if (sum(k) < 10) 1 else 0)} g · ${(energy[i] / all * 100).roundToInt()} %",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
        Text(DETAILS.joinToString(" · ") { (k, l) -> "${l} ${num(sum(k), 1)} g" }, style = MaterialTheme.typography.bodySmall, color = muted)
    }
}

// --- adding ----------------------------------------------------------------------------------------------

private sealed class AddMode {
    data object Search : AddMode()
    data object Scan : AddMode()
    data object Quick : AddMode()
    /** Amounts of the selected foods. */
    data object Amounts : AddMode()
    data class NewFood(val name: String = "", val barcode: String = "", val dish: Boolean = false) : AddMode()
    data class Template(val template: MealTemplate) : AddMode()
    data class EditTemplate(val template: MealTemplate?) : AddMode()
}

/** What the list in the add sheet shows. */
private enum class AddList(val label: String) { RECENT(tr("Zuletzt")), FAVORITES(tr("Favoriten")), DISHES(tr("Gerichte")), TEMPLATES(tr("Vorlagen")) }

/**
 * Adding food: tick any number of foods (search, favorites, dishes, barcode, online), then give each
 * one's amount (the last one is suggested) and log them together.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AddFoodSheet(day: String, meal: Int, meals: List<String>, onDone: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val dayState by DayRepo.state.collectAsState()
    var mode by remember { mutableStateOf<AddMode>(AddMode.Search) }
    val selected = remember { mutableStateListOf<Food>() }
    // amount and count per selected food, as typed
    val inputs = remember { mutableStateMapOf<String, Pair<String, String>>() }
    fun select(f: Food) {
        if (selected.none { it.id == f.id }) selected.add(f)
        if (f.id !in inputs) inputs[f.id] = suggestedAmount(f, dayState.pending).let { (a, q) -> numInput(a) to numInput(q) }
    }
    fun toggle(f: Food) {
        if (selected.any { it.id == f.id }) { selected.removeAll { it.id == f.id }; inputs.remove(f.id) } else select(f)
    }
    ModalBottomSheet(onDismissRequest = onDone, sheetState = sheet) {
        when (val m = mode) {
            AddMode.Search -> SearchPane(selected.map { it.id }.toSet(), onToggle = ::toggle, onSelect = ::select,
                onNext = { mode = AddMode.Amounts }, onMode = { mode = it })
            AddMode.Scan -> ScanPane(onFound = { select(it); mode = AddMode.Amounts }, onUnknown = { mode = AddMode.NewFood(barcode = it) },
                onCancel = { mode = AddMode.Search })
            AddMode.Quick -> QuickPane(day, meal, meals, onDone = onDone, onBack = { mode = AddMode.Search })
            AddMode.Amounts -> AmountsPane(selected, inputs, day, meal, meals, onDone = onDone, onMore = { mode = AddMode.Search },
                onRemove = { f -> toggle(f); if (selected.isEmpty()) mode = AddMode.Search })
            is AddMode.NewFood -> FoodEditor(null, dish = m.dish, draftName = m.name, draftBarcode = m.barcode,
                onSaved = { select(it); mode = AddMode.Amounts }, onBack = { mode = AddMode.Search })
            is AddMode.Template -> TemplatePane(m.template, day, meal, meals, onDone = onDone, onBack = { mode = AddMode.Search },
                onEdit = { mode = AddMode.EditTemplate(m.template) })
            is AddMode.EditTemplate -> TemplateEditor(m.template, onDone = { mode = AddMode.Search })
        }
    }
}

@Composable
private fun SearchPane(
    selected: Set<String>, onToggle: (Food) -> Unit, onSelect: (Food) -> Unit, onNext: () -> Unit, onMode: (AddMode) -> Unit,
) {
    val ctx = LocalContext.current
    val dayState by DayRepo.state.collectAsState()
    val food by FoodRepo.state.collectAsState()
    val scope = rememberCoroutineScope()
    var query by rememberSaveable { mutableStateOf("") }
    var online by remember { mutableStateOf<List<Food>?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var show by rememberSaveable { mutableStateOf(AddList.RECENT) }
    val catalog = food.catalog(dayState.pending)
    val local = when (show) {
        AddList.RECENT -> if (query.isBlank() && food.recent.isNotEmpty()) {
            food.recent.filter { !it.archived }.map { r -> catalog[r.id]?.copy(lastAmount = r.lastAmount, lastQuantity = r.lastQuantity) ?: r }
        } else searchFoods(catalog.values, query)
        AddList.FAVORITES -> searchFoods(catalog.values.filter { it.favorite }, query, limit = 200).ifEmpty {
            if (query.isBlank()) catalog.values.filter { it.favorite && !it.archived }.sortedBy { it.name.lowercase() } else emptyList()
        }
        AddList.DISHES -> catalog.values.filter { it.dish && !it.archived && (query.isBlank() || searchFoods(listOf(it), query).isNotEmpty()) }
            .sortedWith(compareByDescending<Food> { it.favorite }.thenBy { it.name.lowercase() })
        AddList.TEMPLATES -> emptyList()
    }
    val templates = food.templates(dayState.pending).filter { query.isBlank() || it.name.contains(query.trim(), ignoreCase = true) }
    val muted = MaterialTheme.colorScheme.onSurfaceVariant

    // full height, so switching the list does not move the chips under the finger
    Column(Modifier.fillMaxWidth().fillMaxHeight()) {
        Column(Modifier.weight(1f).fillMaxWidth().padding(horizontal = 16.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = query, onValueChange = { query = it; online = null; message = null },
                label = { Text(tr("Lebensmittel suchen")) }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { onMode(AddMode.Scan) }) { Text(tr("Barcode")) }
                OutlinedButton(enabled = query.trim().length >= 2 && !busy, onClick = {
                    busy = true
                    message = tr("Suche bei Open Food Facts …")
                    scope.launch {
                        val s = Settings(ctx)
                        val result = withContext(Dispatchers.IO) { runCatching { Api(s.serverUrl, s.token).searchOnline(query.trim()) } }
                        busy = false
                        result.onSuccess { online = it; message = if (it.isEmpty()) tr("Online nichts gefunden.") else null }
                            .onFailure { message = offMessage(it) }
                    }
                }) { Text(tr("Online")) }
                OutlinedButton(onClick = { onMode(AddMode.Quick) }) { Text(tr("Schnell")) }
                OutlinedButton(onClick = {
                    onMode(if (show == AddList.TEMPLATES) AddMode.EditTemplate(null) else AddMode.NewFood(name = query.trim(), dish = show == AddList.DISHES))
                }) { Text(tr("Neu")) }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                AddList.entries.forEach { l ->
                    FilterChip(selected = show == l && online == null, onClick = { show = l; online = null; message = null }, label = { Text(l.label) })
                }
            }
            message?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = muted) }
            if (show == AddList.TEMPLATES && online == null) {
                if (templates.isEmpty()) {
                    Text(tr("Noch keine Vorlagen: „Neu“, oder bei einer Mahlzeit auf die kcal tippen → „Als Vorlage speichern“."),
                        style = MaterialTheme.typography.bodySmall, color = muted)
                }
                templates.forEach { t ->
                    Column(Modifier.fillMaxWidth().clickable { onMode(AddMode.Template(t)) }.padding(vertical = 6.dp)) {
                        Text((if (t.favorite) "★ " else "") + t.name + tr(" · {0} Lebensmittel", t.items.size), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(macroLine(t.nutrients) + " · " + t.items.joinToString(", ") { it.name }, style = MaterialTheme.typography.bodySmall,
                            color = muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            val list = online ?: local
            if (list.isEmpty() && online == null && show != AddList.TEMPLATES) {
                Text(when {
                    show == AddList.FAVORITES -> tr("Noch keine Favoriten: beim Eintragen den Stern antippen.")
                    show == AddList.DISHES -> tr("Noch keine Gerichte: „Neu“ legt eins an.")
                    query.isBlank() -> tr("Noch keine Lebensmittel: suchen, Barcode scannen oder neu anlegen.")
                    else -> tr("Nichts in deinen Lebensmitteln. Online suchen?")
                }, style = MaterialTheme.typography.bodySmall, color = muted)
            }
            list.forEach { f ->
                val known = catalog[f.id] ?: f
                FoodRow(f, online = online != null, checked = f.id.isNotEmpty() && f.id in selected,
                    last = if (f.id.isNotEmpty()) FoodRepo.state.value.lastUse(f.id, dayState.pending) else null) {
                    if (online == null || f.id.isNotEmpty()) {
                        onToggle(known)
                        return@FoodRow
                    }
                    busy = true
                    message = tr("Übernehme …")
                    scope.launch {
                        val s = Settings(ctx)
                        val r = withContext(Dispatchers.IO) { runCatching { Api(s.serverUrl, s.token).takeOverOnline(f.barcode.orEmpty()) } }
                        busy = false
                        r.onSuccess { taken ->
                            FoodRepo.remember(taken)
                            onSelect(taken)
                            online = online?.map { if (it.barcode == f.barcode) it.copy(id = taken.id) else it }
                            message = null
                        }.onFailure { message = offMessage(it) }
                    }
                }
            }
            Row(Modifier.padding(bottom = 24.dp)) {}
        }
        if (selected.isNotEmpty()) {
            HorizontalDivider()
            Button(onClick = onNext, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                Text(if (selected.size == 1) tr("Weiter mit 1 Lebensmittel") else tr("Weiter mit {0} Lebensmitteln", selected.size))
            }
        }
    }
}

@Composable
private fun FoodRow(f: Food, online: Boolean, checked: Boolean, last: FoodUsage?, onClick: () -> Unit) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    val lastAmount = last?.lastAmount ?: f.lastAmount
    val lastQty = if (last?.lastAmount != null) last.lastQuantity else f.lastQuantity
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text((if (f.favorite) "★ " else "") + f.name + (f.brand?.let { " · ${it}" } ?: "") + if (online) tr("  (OFF)") else "",
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                macroLine(f.nutrients) + " / ${num(f.baseAmount, 0)} ${f.unitLabel}" +
                    (lastAmount?.let { tr(" · zuletzt {0} {1}", num(it * (lastQty ?: 1.0), 0), f.unitLabel) } ?: ""),
                style = MaterialTheme.typography.bodySmall, color = muted, maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
        }
        VIcon(if (checked) R.drawable.ic_t_square_check else R.drawable.ic_t_square,
            tint = if (checked) Vita.colors.food else Vita.colors.faint, modifier = Modifier.padding(start = 8.dp))
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ScanPane(onFound: (Food) -> Unit, onUnknown: (String) -> Unit, onCancel: () -> Unit) {
    val ctx = LocalContext.current
    val dayState by DayRepo.state.collectAsState()
    val scope = rememberCoroutineScope()
    var message by remember { mutableStateOf<String?>(null) }
    var code by remember { mutableStateOf<String?>(null) }
    Column {
        if (code == null) {
            BarcodeScanner(onCode = { c ->
                code = c
                FoodRepo.byBarcode(c, dayState.pending)?.let { onFound(it); return@BarcodeScanner }
                message = tr("Suche {0} …", c)
                scope.launch {
                    val s = Settings(ctx)
                    val r = withContext(Dispatchers.IO) { runCatching { Api(s.serverUrl, s.token).foodByBarcode(c) } }
                    r.onSuccess { f -> if (f != null) { FoodRepo.remember(f); onFound(f) } else message = tr("Barcode {0} ist unbekannt, auch bei Open Food Facts.", c) }
                        .onFailure { message = offMessage(it) }
                }
            }, onCancel = onCancel)
        }
        code?.let { c ->
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(message ?: "", style = MaterialTheme.typography.bodyMedium)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { onUnknown(c) }) { Text(tr("Lebensmittel anlegen")) }
                    OutlinedButton(onClick = { code = null; message = null }) { Text(tr("Neu scannen")) }
                    TextButton(onClick = onCancel) { Text(tr("Zurück")) }
                }
            }
        }
    }
}

private fun offMessage(e: Throwable): String = when {
    e is ApiException && e.code == 503 -> tr("Open Food Facts ist gerade nicht erreichbar.")
    e is ApiException && e.code == 429 -> tr("Zu viele Online-Abfragen – gleich noch einmal.")
    e is IOException && e !is ApiException -> tr("Offline: online suchen geht gerade nicht. Neu anlegen geht auch offline.")
    else -> e.describe()
}

/** The amount of each selected food (last amount suggested), one meal and time for all, then one entry each. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AmountsPane(
    foods: List<Food>, inputs: MutableMap<String, Pair<String, String>>, day: String, meal: Int, meals: List<String>,
    onDone: () -> Unit, onMore: () -> Unit, onRemove: (Food) -> Unit,
) {
    val ctx = LocalContext.current
    var mealIndex by remember { mutableStateOf(meal) }
    var clock by remember { mutableStateOf(defaultClock(day)) }
    val clockOk = parseClock(clock) != null
    val parsed = foods.associate { f ->
        val (a, q) = inputs[f.id] ?: ("" to "1")
        f.id to (parseNum(a)?.takeIf { it > 0 } to parseNum(q)?.takeIf { it > 0 })
    }
    val valid = foods.isNotEmpty() && parsed.values.all { (a, q) -> a != null && q != null } && clockOk
    val total = foods.flatMap { f -> parsed[f.id]?.let { (a, q) -> if (a != null && q != null) f.nutrientsFor(a, q).entries else null }.orEmpty() }
        .groupBy({ it.key }, { it.value }).mapValues { it.value.sum() }

    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(if (foods.size == 1) tr("Menge") else tr("Mengen ({0} Lebensmittel)", foods.size), style = MaterialTheme.typography.titleMedium)
        foods.forEach { f ->
            AmountBlock(f, inputs[f.id] ?: ("" to "1"), onChange = { inputs[f.id] = it }, onRemove = { onRemove(f) })
            HorizontalDivider()
        }
        TextButton(onClick = onMore) { VIcon(R.drawable.ic_t_plus, size = 16.dp); Text(tr(" Weitere Lebensmittel")) }
        MealChips(meals, mealIndex) { mealIndex = it }
        OutlinedTextField(clock, { clock = it.take(5) }, label = { Text(tr("Uhrzeit")) }, singleLine = true, isError = !clockOk,
            modifier = Modifier.width(120.dp), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
        if (foods.size > 1) Text(tr("Zusammen: {0}", macroLine(total)), fontWeight = FontWeight.SemiBold)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 24.dp)) {
            Button(enabled = valid, onClick = {
                val at = entryTimestamp(day, parseClock(clock)!!)
                foods.forEach { f ->
                    val (a, q) = parsed[f.id]!!
                    DayRepo.addFoodEntry(ctx, FoodEntryIn(
                        id = UUID.randomUUID().toString(), day = day, loggedAt = at, mealIndex = mealIndex, foodId = f.id, amount = a, quantity = q!!,
                    ))
                }
                onDone()
            }) { Text(tr("Eintragen")) }
            TextButton(onClick = onMore) { Text(tr("Zurück")) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AmountBlock(food: Food, input: Pair<String, String>, onChange: (Pair<String, String>) -> Unit, onRemove: () -> Unit) {
    val ctx = LocalContext.current
    val (amount, qty) = input
    var favorite by remember(food.id) { mutableStateOf(food.favorite) }
    val a = parseNum(amount)
    val q = parseNum(qty)
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(food.name + (food.brand?.let { " · ${it}" } ?: ""), fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text("${macroLine(food.nutrients)} / ${num(food.baseAmount, 0)} ${food.unitLabel}",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            // the food's favorite flag, saved through the outbox (offline too)
            IconButton(enabled = food.id.isNotEmpty(), onClick = {
                favorite = !favorite
                DayRepo.updateFood(ctx, food.copy(favorite = favorite))
            }) { VIcon(R.drawable.ic_t_star, tint = if (favorite) Vita.colors.warn else Vita.colors.faint) }
            IconButton(onClick = onRemove) { VIcon(R.drawable.ic_t_x, size = 18.dp) }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            food.portions.forEach { p ->
                SuggestionChip(onClick = { onChange(numInput(p.amount) to "1") }, label = { Text("${p.label} (${num(p.amount, 0)} ${food.unitLabel})") })
            }
            if (food.baseUnit != "portion") {
                SuggestionChip(onClick = { onChange(numInput(food.baseAmount) to "1") }, label = { Text("${num(food.baseAmount, 0)} ${food.baseUnit}") })
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(amount, { onChange(it to qty) }, label = { Text(tr("Menge ({0})", food.unitLabel)) }, singleLine = true,
                isError = a == null || a <= 0, modifier = Modifier.weight(1f), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
            Text("×")
            OutlinedTextField(qty, { onChange(amount to it) }, label = { Text(tr("Anzahl")) }, singleLine = true,
                isError = q == null || q <= 0, modifier = Modifier.width(88.dp), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
        }
        Text(if (a != null && q != null) macroLine(food.nutrientsFor(a, q)) else "–", style = MaterialTheme.typography.bodySmall)
    }
}

/** Log a template: one entry per food at the chosen meal and time; the amounts can be changed here first. */
@Composable
private fun TemplatePane(
    t: MealTemplate, day: String, meal: Int, meals: List<String>, onDone: () -> Unit, onBack: () -> Unit, onEdit: () -> Unit,
) {
    val ctx = LocalContext.current
    var mealIndex by remember { mutableStateOf(meal) }
    var clock by remember { mutableStateOf(defaultClock(day)) }
    val amounts = remember { mutableStateListOf<String>().apply { t.items.forEach { add(numInput(it.amount * it.quantity)) } } }
    val items = t.items.mapIndexedNotNull { i, item ->
        val a = parseNum(amounts[i])?.takeIf { it > 0 } ?: return@mapIndexedNotNull null
        val total = item.amount * item.quantity
        item.copy(amount = a, quantity = 1.0, nutrients = scaled(item.nutrients, if (total > 0) a / total else 1.0))
    }
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(t.name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            IconButton(onClick = onEdit) { VIcon(R.drawable.ic_t_pencil) }
        }
        t.items.forEachIndexed { i, item ->
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(item.name, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                OutlinedTextField(amounts[i], { amounts[i] = it }, singleLine = true, modifier = Modifier.width(96.dp),
                    label = { Text(unitLabel(item.unit)) }, isError = parseNum(amounts[i])?.takeIf { it > 0 } == null,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
            }
        }
        Text(macroLine(MealTemplate("", "", items).nutrients), fontWeight = FontWeight.SemiBold)
        MealChips(meals, mealIndex) { mealIndex = it }
        OutlinedTextField(clock, { clock = it.take(5) }, label = { Text(tr("Uhrzeit")) }, singleLine = true,
            isError = parseClock(clock) == null, modifier = Modifier.width(120.dp))
        Text(tr("Jedes Lebensmittel wird ein eigener Eintrag; die Mengen lassen sich danach ändern. Die Vorlage bleibt, wie sie ist."),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 24.dp)) {
            Button(enabled = parseClock(clock) != null && items.size == t.items.size && items.isNotEmpty(), onClick = {
                DayRepo.logTemplate(ctx, t.copy(items = items), day, entryTimestamp(day, parseClock(clock)!!), mealIndex)
                onDone()
            }) { Text(tr("Eintragen")) }
            TextButton(onClick = onBack) { Text(tr("Zurück")) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MealChips(meals: List<String>, selected: Int, onPick: (Int) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        meals.forEachIndexed { i, name -> FilterChip(selected = selected == i, onClick = { onPick(i) }, label = { Text(name) }) }
    }
}

@Composable
private fun QuickPane(day: String, meal: Int, meals: List<String>, onDone: () -> Unit, onBack: () -> Unit) {
    val ctx = LocalContext.current
    var name by remember { mutableStateOf("") }
    var values by remember { mutableStateOf(mapOf<String, String>()) }
    var mealIndex by remember { mutableStateOf(meal) }
    var clock by remember { mutableStateOf(defaultClock(day)) }
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(tr("Schnell-Eintrag"), style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(name, { name = it.take(200) }, label = { Text(tr("Was, z. B. Kuchen im Büro")) }, singleLine = true,
            modifier = Modifier.fillMaxWidth())
        NutrientEditor(values, { values = it })
        MealChips(meals, mealIndex) { mealIndex = it }
        OutlinedTextField(clock, { clock = it.take(5) }, label = { Text(tr("Uhrzeit")) }, singleLine = true,
            isError = parseClock(clock) == null, modifier = Modifier.width(120.dp))
        Text(tr("Leere Felder bleiben unbekannt (nicht 0)."), style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 24.dp)) {
            Button(enabled = name.isNotBlank() && parseClock(clock) != null, onClick = {
                DayRepo.addFoodEntry(ctx, FoodEntryIn(
                    id = UUID.randomUUID().toString(), day = day, loggedAt = entryTimestamp(day, parseClock(clock)!!),
                    mealIndex = mealIndex, name = name.trim(), nutrients = parseNutrients(values),
                ))
                onDone()
            }) { Text(tr("Eintragen")) }
            TextButton(onClick = onBack) { Text(tr("Zurück")) }
        }
    }
}

// --- editing ------------------------------------------------------------------------------------------------

/**
 * Change an entry: amount, count, meal, time; a quick entry also its name and values. For a food
 * entry, the food itself can be corrected too (this entry then takes the new values).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EditEntrySheet(e: FoodEntry, meals: List<String>, onDone: () -> Unit) {
    val ctx = LocalContext.current
    val dayState by DayRepo.state.collectAsState()
    val foodState by FoodRepo.state.collectAsState()
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val quick = e.foodId == null
    var amount by remember { mutableStateOf(e.amount?.let(::numInput) ?: "") }
    var qty by remember { mutableStateOf(numInput(e.quantity)) }
    var mealIndex by remember { mutableStateOf(e.mealIndex) }
    var clock by remember { mutableStateOf(clockOf(e.loggedAt) ?: "12:00") }
    var name by remember { mutableStateOf(e.name) }
    // a quick entry's values per piece
    var values by remember { mutableStateOf(nutrientInputs(scaled(e.nutrients, if (e.quantity > 0) 1 / e.quantity else 1.0))) }
    var editFood by remember { mutableStateOf(false) }
    val food = e.foodId?.let { foodState.catalog(dayState.pending)[it] }
    val a = parseNum(amount)
    val q = parseNum(qty)
    val preview = when {
        q == null || q <= 0 -> null
        quick -> scaled(parseNutrients(values), q)
        e.amount == null || (a != null && a > 0) -> scaled(e.nutrients, (a ?: 1.0) * q / ((e.amount ?: 1.0) * e.quantity))
        else -> null
    }
    fun update(recompute: Boolean = false) = DayRepo.updateFoodEntry(ctx, PendingOp.UpdateFoodEntry(
        UUID.randomUUID().toString(), e.id, e.day, entryTimestamp(e.day, parseClock(clock) ?: "12:00"), mealIndex,
        if (e.amount != null) a ?: e.amount else null, q ?: e.quantity,
        name = if (quick) name.trim().ifEmpty { e.name } else null, nutrients = if (quick) parseNutrients(values) else null,
        recompute = recompute,
    ))
    ModalBottomSheet(onDismissRequest = onDone, sheetState = sheet) {
        if (editFood && food != null) {
            FoodEditor(food, note = tr("Korrigiert das Lebensmittel; dieser Eintrag übernimmt die neuen Werte, frühere Einträge behalten ihre."),
                onSaved = { update(recompute = true); onDone() }, onBack = { editFood = false })
            return@ModalBottomSheet
        }
        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (quick) {
                OutlinedTextField(name, { name = it.take(200) }, label = { Text(tr("Was")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            } else {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(e.name + (e.brand?.let { " · ${it}" } ?: ""), style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                    if (food != null) TextButton(onClick = { editFood = true }) { VIcon(R.drawable.ic_t_pencil, size = 16.dp); Text(tr(" Lebensmittel")) }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                if (e.amount != null) {
                    OutlinedTextField(amount, { amount = it }, label = { Text(tr("Menge ({0})", unitLabel(e.unit))) }, singleLine = true,
                        modifier = Modifier.weight(1f), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                    Text("×")
                }
                OutlinedTextField(qty, { qty = it }, label = { Text(tr("Anzahl")) }, singleLine = true,
                    modifier = Modifier.width(96.dp), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
            }
            if (!quick && food != null && food.portions.isNotEmpty()) {
                @OptIn(ExperimentalLayoutApi::class)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    food.portions.forEach { p ->
                        SuggestionChip(onClick = { amount = numInput(p.amount); qty = "1" }, label = { Text("${p.label} (${num(p.amount, 0)} ${food.unitLabel})") })
                    }
                }
            }
            if (quick) NutrientEditor(values, { values = it })
            MealChips(meals, mealIndex) { mealIndex = it }
            OutlinedTextField(clock, { clock = it.take(5) }, label = { Text(tr("Uhrzeit")) }, singleLine = true,
                isError = parseClock(clock) == null, modifier = Modifier.width(120.dp))
            Text(preview?.let(::macroLine) ?: "–", fontWeight = FontWeight.SemiBold)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 24.dp)) {
                Button(enabled = preview != null && parseClock(clock) != null && (!quick || name.isNotBlank()), onClick = {
                    update()
                    onDone()
                }) { Text(tr("Speichern")) }
                OutlinedButton(onClick = { DayRepo.deleteFoodEntry(ctx, e.id); onDone() }) {
                    Text(tr("Löschen"), color = MaterialTheme.colorScheme.error)
                }
                TextButton(onClick = onDone) { Text(tr("Abbrechen")) }
            }
        }
    }
}

// --- helpers --------------------------------------------------------------------------------------------------

private fun num(v: Double, decimals: Int) = foodNum(v, decimals)

fun macroLine(n: Map<String, Double>): String = listOf(
    n["calories"]?.let { "${num(it, 0)} kcal" } ?: tr("– kcal"),
    "F ${n["fat"]?.let { num(it, if (it < 10) 1 else 0) } ?: "–"}",
    tr("KH {0}", n["carbohydrates"]?.let { num(it, if (it < 10) 1 else 0) } ?: "–"),
    "E ${n["proteins"]?.let { num(it, if (it < 10) 1 else 0) } ?: "–"}",
).joinToString(" · ")

/** Up to [decimals] decimals, none when whole (German format). */
fun foodNum(v: Double, decimals: Int): String = NumberFormat.getNumberInstance(I18n.locale).apply {
    maximumFractionDigits = decimals
    minimumFractionDigits = 0
}.format(v)

fun numInput(v: Double): String = if (v % 1.0 == 0.0) v.toLong().toString() else v.toString().replace('.', ',')

fun parseNum(s: String): Double? = s.trim().replace(',', '.').toDoubleOrNull()?.takeIf { it >= 0 }

/** "8:05", "08:05", "805" → "08:05"; null when not a time. */
fun parseClock(s: String): String? {
    val t = s.trim()
    val m = Regex("^(\\d{1,2}):?(\\d{2})$").find(t) ?: return null
    val (h, min) = m.destructured
    return if (h.toInt() in 0..23 && min.toInt() in 0..59) "%02d:%02d".format(h.toInt(), min.toInt()) else null
}

private fun defaultClock(day: String): String =
    if (day == DayRepo.today) LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm")) else "12:00"
