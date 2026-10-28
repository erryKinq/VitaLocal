package de.vitalocal.companion

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import de.vitalocal.companion.ui.VIcon
import de.vitalocal.companion.ui.Vita
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID

// --- nutrients ------------------------------------------------------------------------------------------------

/** Nutrient fields in the order of a nutrition label; everything beyond the usual eight folded away. */
@Composable
fun NutrientEditor(values: Map<String, String>, onChange: (Map<String, String>) -> Unit, defs: List<NutrientDef> = FORM_NUTRIENTS) {
    var more by rememberSaveable { mutableStateOf(MORE_NUTRIENTS.any { !values[it.key].isNullOrBlank() }) }
    NutrientGrid(defs, values, onChange)
    if (defs === FORM_NUTRIENTS) {
        val filled = MORE_NUTRIENTS.count { !values[it.key].isNullOrBlank() }
        TextButton(onClick = { more = !more }) {
            Text(tr("Weitere Nährstoffe") + if (filled > 0) " (${filled})" else "")
            VIcon(if (more) R.drawable.ic_t_chevron_up else R.drawable.ic_t_chevron_down, size = 16.dp)
        }
        if (more) NutrientGrid(MORE_NUTRIENTS, values, onChange)
    }
}

@Composable
private fun NutrientGrid(defs: List<NutrientDef>, values: Map<String, String>, onChange: (Map<String, String>) -> Unit) {
    defs.chunked(2).forEach { pair ->
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            pair.forEach { n ->
                OutlinedTextField(
                    values[n.key].orEmpty(), { onChange(values + (n.key to it)) },
                    label = { Text("${n.label} (${n.unit})", maxLines = 1, overflow = TextOverflow.Ellipsis) },
                    singleLine = true, modifier = Modifier.weight(1f),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                )
            }
            if (pair.size == 1) Row(Modifier.weight(1f)) {}
        }
    }
}

/** Text fields' values as nutrients; empty fields stay unknown. */
fun parseNutrients(values: Map<String, String>): Map<String, Double> =
    values.mapNotNull { (k, v) -> parseNum(v)?.let { k to it } }.toMap()

fun nutrientInputs(n: Map<String, Double>): Map<String, String> = n.mapValues { numInput(roundTo(it.value, 3)) }

private fun roundTo(v: Double, digits: Int): Double = Math.round(v * Math.pow(10.0, digits.toDouble())) / Math.pow(10.0, digits.toDouble())

// --- picking a food ---------------------------------------------------------------------------------------------

/** A search over the own catalog (recent foods while empty): ingredients, template foods. */
@Composable
fun FoodPicker(exclude: Set<String> = emptySet(), onPick: (Food) -> Unit) {
    val dayState by DayRepo.state.collectAsState()
    val food by FoodRepo.state.collectAsState()
    var query by remember { mutableStateOf("") }
    val catalog = food.catalog(dayState.pending)
    val hits = (if (query.isBlank()) food.recent.mapNotNull { catalog[it.id] }.filter { !it.archived } else searchFoods(catalog.values, query, 8))
        .filter { it.id !in exclude }.take(8)
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        OutlinedTextField(query, { query = it }, label = { Text(tr("Lebensmittel suchen")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        hits.forEach { f ->
            Column(Modifier.fillMaxWidth().clickable { onPick(f); query = "" }.padding(vertical = 4.dp)) {
                Text(f.name + (f.brand?.let { " · ${it}" } ?: ""), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text("${macroLine(f.nutrients)} / ${foodNum(f.baseAmount, 0)} ${f.unitLabel}", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/** What a new entry of [f] suggests: the last logged amount, else its first portion, else the base amount. */
fun suggestedAmount(f: Food, pending: List<PendingOp>): Pair<Double, Double> {
    val last = FoodRepo.state.value.lastUse(f.id, pending)
    return when {
        last?.lastAmount != null -> last.lastAmount to (last.lastQuantity ?: 1.0)
        f.lastAmount != null -> f.lastAmount to (f.lastQuantity ?: 1.0)
        f.baseUnit == "portion" && f.portions.isNotEmpty() -> f.portions.first().amount to 1.0
        else -> f.baseAmount to 1.0
    }
}

// --- food and dish editor -----------------------------------------------------------------------------------------

/**
 * Create or change a food or a dish (`food` null = new; [dish] = with ingredients, whose nutrients
 * are added up). Saved through the outbox, so it works offline; logged entries keep their values.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FoodEditor(
    food: Food?, dish: Boolean = food?.dish == true, draftName: String = "", draftBarcode: String = "",
    note: String? = null, onSaved: (Food) -> Unit, onBack: () -> Unit, onDeleted: (() -> Unit)? = null,
) {
    val ctx = LocalContext.current
    val dayState by DayRepo.state.collectAsState()
    val foodState by FoodRepo.state.collectAsState()
    val catalog = foodState.catalog(dayState.pending)
    var name by remember { mutableStateOf(food?.name ?: draftName) }
    var brand by remember { mutableStateOf(food?.brand.orEmpty()) }
    var barcode by remember { mutableStateOf(food?.barcode ?: draftBarcode) }
    var base by remember { mutableStateOf(numInput(food?.baseAmount ?: if (dish) 1.0 else 100.0)) }
    var unit by remember { mutableStateOf(food?.baseUnit ?: if (dish) "portion" else "g") }
    var values by remember { mutableStateOf(nutrientInputs(food?.nutrients ?: emptyMap())) }
    val portions = remember { mutableStateListOf<Pair<String, String>>().apply { food?.portions?.forEach { add(it.label to numInput(it.amount)) } } }
    val parts = remember { mutableStateListOf<Pair<Component, String>>().apply { food?.components?.forEach { add(it to numInput(it.amount)) } } }
    var favorite by remember { mutableStateOf(food?.favorite ?: false) }
    var archived by remember { mutableStateOf(food?.archived ?: false) }
    var picking by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }
    val baseNum = parseNum(base)
    val components = parts.mapNotNull { (c, a) -> parseNum(a)?.takeIf { it > 0 }?.let { c.copy(amount = it) } }
    val dishNutr = if (dish) dishNutrients(components) { catalog[it] } else emptyMap()
    // a dish taken over from Waistline without known ingredients keeps its own values
    val ingredientsKnown = !dish || food == null || food.components != null
    val valid = name.isNotBlank() && baseNum != null && baseNum > 0 && (!dish || !ingredientsKnown || components.isNotEmpty())

    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(when { food != null && dish -> tr("Gericht bearbeiten"); food != null -> tr("Lebensmittel bearbeiten")
                dish -> tr("Neues Gericht"); else -> tr("Neues Lebensmittel") }, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            IconButton(onClick = { favorite = !favorite }) { VIcon(R.drawable.ic_t_star, tint = if (favorite) Vita.colors.warn else Vita.colors.faint) }
        }
        note?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        OutlinedTextField(name, { name = it.take(200) }, label = { Text(tr("Name")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        if (!dish) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(brand, { brand = it.take(200) }, label = { Text(tr("Marke")) }, singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(barcode, { barcode = it.filter(Char::isDigit).take(14) }, label = { Text(tr("Barcode")) }, singleLine = true,
                    modifier = Modifier.weight(1f), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(if (dish) tr("Ergibt") else tr("Nährwerte je"))
            OutlinedTextField(base, { base = it }, singleLine = true, modifier = Modifier.width(88.dp),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("g" to "g", "ml" to "ml", "portion" to tr("Portion")).forEach { (k, l) ->
                    FilterChip(selected = unit == k, onClick = { unit = k }, label = { Text(l) })
                }
            }
        }
        if (dish) {
            Text(tr("Zutaten"), fontWeight = FontWeight.SemiBold)
            if (!ingredientsKnown) {
                Text(tr("Die Zutaten dieses Gerichts sind hier nicht bekannt; seine Nährwerte bleiben, wie sie sind."),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            parts.forEachIndexed { i, (c, a) ->
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(c.name, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                    OutlinedTextField(a, { parts[i] = c to it }, singleLine = true, modifier = Modifier.width(88.dp),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                    Text(unitLabel(c.unit), modifier = Modifier.width(52.dp), maxLines = 1)
                    IconButton(onClick = { parts.removeAt(i) }) { VIcon(R.drawable.ic_t_x, size = 18.dp) }
                }
            }
            if (picking) {
                FoodPicker(exclude = setOfNotNull(food?.id)) { f ->
                    val (amt, q) = suggestedAmount(f, dayState.pending)
                    parts.add(Component(f.id, f.name, amt * q, f.baseUnit) to numInput(amt * q))
                    picking = false
                }
            } else {
                OutlinedButton(onClick = { picking = true }) { VIcon(R.drawable.ic_t_plus, size = 16.dp); Text(tr(" Zutat")) }
            }
            Text(if (ingredientsKnown) "${macroLine(dishNutr)} insgesamt" else macroLine(food?.nutrients ?: emptyMap()),
                fontWeight = FontWeight.SemiBold)
        } else {
            NutrientEditor(values, { values = it })
        }
        Text(tr("Portionen"), fontWeight = FontWeight.SemiBold)
        portions.forEachIndexed { i, (label, amount) ->
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(label, { portions[i] = it.take(60) to amount }, label = { Text(tr("z. B. 1 Scheibe")) }, singleLine = true,
                    modifier = Modifier.weight(1f))
                OutlinedTextField(amount, { portions[i] = label to it }, label = { Text(unitLabel(unit)) }, singleLine = true,
                    modifier = Modifier.width(88.dp), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                IconButton(onClick = { portions.removeAt(i) }) { VIcon(R.drawable.ic_t_x, size = 18.dp) }
            }
        }
        TextButton(onClick = { portions.add("" to "") }) { Text(tr("+ Portion")) }
        if (food != null) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.clickable { archived = !archived }) {
                Checkbox(archived, { archived = it })
                Text(tr("Archiviert (nicht mehr in der Suche)"))
            }
        }
        Text(
            (if (dish) "" else tr("Leere Felder bleiben unbekannt (nicht 0). ")) +
                tr("Änderungen gelten für neue Einträge; eingetragene behalten ihre Werte. Geht auch offline."),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 24.dp)) {
            Button(enabled = valid, onClick = {
                val saved = Food(
                    id = food?.id ?: UUID.randomUUID().toString(), name = name.trim(), brand = if (dish) null else brand.trim().ifEmpty { null },
                    barcode = if (dish) null else barcode.ifEmpty { null }, baseAmount = baseNum!!, baseUnit = unit,
                    portions = portions.mapNotNull { (l, a) -> parseNum(a)?.takeIf { it > 0 && l.isNotBlank() }?.let { Portion(l.trim(), it) } },
                    // keys the editor does not show stay as they were
                    nutrients = when {
                        dish && ingredientsKnown -> dishNutr
                        dish -> food?.nutrients.orEmpty()
                        else -> food?.nutrients.orEmpty().filterKeys { k -> FORM_NUTRIENTS.none { it.key == k } && MORE_NUTRIENTS.none { it.key == k } } +
                            parseNutrients(values)
                    },
                    source = food?.source ?: "vitalocal", favorite = favorite, archived = archived,
                    dish = dish, updatedAt = food?.updatedAt, uses = food?.uses ?: 0, lastUsed = food?.lastUsed,
                    lastAmount = food?.lastAmount, lastQuantity = food?.lastQuantity,
                    components = if (dish && ingredientsKnown) components else food?.components,
                )
                if (food == null) DayRepo.addFood(ctx, saved) else DayRepo.updateFood(ctx, saved)
                onSaved(saved)
            }) { Text(tr("Speichern")) }
            TextButton(onClick = onBack) { Text(tr("Zurück")) }
            if (food != null && onDeleted != null) {
                TextButton(onClick = { confirmDelete = true }) { Text(tr("Löschen"), color = MaterialTheme.colorScheme.error) }
            }
        }
    }
    if (confirmDelete && food != null) {
        ConfirmDialog(tr("„{0}“ löschen?", food.name), tr("Schon eingetragene Lebensmittel werden stattdessen archiviert; Einträge bleiben."),
            onConfirm = { DayRepo.deleteFood(ctx, food); confirmDelete = false; onDeleted?.invoke() }, onDismiss = { confirmDelete = false })
    }
}

@Composable
fun ConfirmDialog(title: String, text: String, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss, title = { Text(title) }, text = { Text(text) },
        confirmButton = { TextButton(onClick = onConfirm) { Text(tr("Löschen"), color = MaterialTheme.colorScheme.error) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(tr("Abbrechen")) } },
    )
}

// --- template editor --------------------------------------------------------------------------------------------

/** Create or change a meal template: name, foods with amount × count, favorite; delete. */
@Composable
fun TemplateEditor(template: MealTemplate?, onDone: () -> Unit, initial: List<TemplateItem> = emptyList(), initialName: String = "") {
    val ctx = LocalContext.current
    val dayState by DayRepo.state.collectAsState()
    val foodState by FoodRepo.state.collectAsState()
    val catalog = foodState.catalog(dayState.pending)
    var name by remember { mutableStateOf(template?.name ?: initialName) }
    var favorite by remember { mutableStateOf(template?.favorite ?: false) }
    val rows = remember {
        mutableStateListOf<Triple<TemplateItem, String, String>>().apply {
            (template?.items ?: initial).forEach { add(Triple(it, numInput(it.amount), numInput(it.quantity))) }
        }
    }
    var picking by remember { mutableStateOf(template == null && initial.isEmpty()) }
    var confirmDelete by remember { mutableStateOf(false) }
    val items = rows.mapNotNull { (i, a, q) ->
        val amount = parseNum(a)?.takeIf { it > 0 } ?: return@mapNotNull null
        val qty = parseNum(q)?.takeIf { it > 0 } ?: 1.0
        val f = catalog[i.foodId]
        i.copy(amount = amount, quantity = qty, nutrients = f?.nutrientsFor(amount, qty) ?: scaled(i.nutrients, amount * qty / (i.amount * i.quantity)))
    }
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(if (template == null) tr("Neue Vorlage") else tr("Vorlage bearbeiten"), style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f))
            IconButton(onClick = { favorite = !favorite }) { VIcon(R.drawable.ic_t_star, tint = if (favorite) Vita.colors.warn else Vita.colors.faint) }
        }
        OutlinedTextField(name, { name = it.take(200) }, label = { Text(tr("Name")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        rows.forEachIndexed { i, (item, a, q) ->
            Column {
                Text(item.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(a, { rows[i] = Triple(item, it, q) }, singleLine = true, modifier = Modifier.width(96.dp),
                        label = { Text(unitLabel(item.unit)) }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                    Text("×")
                    OutlinedTextField(q, { rows[i] = Triple(item, a, it) }, singleLine = true, modifier = Modifier.width(72.dp),
                        label = { Text(tr("Anz.")) }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                    Text(items.firstOrNull { it.foodId == item.foodId }?.nutrients?.get("calories")?.let { "${foodNum(it, 0)} kcal" } ?: "",
                        modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                    IconButton(onClick = { rows.removeAt(i) }) { VIcon(R.drawable.ic_t_x, size = 18.dp) }
                }
            }
        }
        if (picking) {
            FoodPicker { f ->
                val (amt, q) = suggestedAmount(f, dayState.pending)
                rows.add(Triple(TemplateItem(f.id, f.name, amt, q, f.baseUnit, f.nutrientsFor(amt, q)), numInput(amt), numInput(q)))
                picking = false
            }
        } else {
            OutlinedButton(onClick = { picking = true }) { VIcon(R.drawable.ic_t_plus, size = 16.dp); Text(tr(" Lebensmittel")) }
        }
        Text(macroLine(MealTemplate("", "", items).nutrients), fontWeight = FontWeight.SemiBold)
        Text(tr("Beim Eintragen wird jedes Lebensmittel ein eigener Eintrag. Geht auch offline."),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 24.dp)) {
            Button(enabled = name.isNotBlank() && items.isNotEmpty(), onClick = {
                val t = MealTemplate(template?.id ?: UUID.randomUUID().toString(), name.trim(), items, favorite, template?.archived ?: false)
                if (template == null) DayRepo.addTemplate(ctx, t) else DayRepo.updateTemplate(ctx, t)
                onDone()
            }) { Text(tr("Speichern")) }
            TextButton(onClick = onDone) { Text(tr("Zurück")) }
            if (template != null) TextButton(onClick = { confirmDelete = true }) { Text(tr("Löschen"), color = MaterialTheme.colorScheme.error) }
        }
    }
    if (confirmDelete && template != null) {
        ConfirmDialog(tr("Vorlage „{0}“ löschen?", template.name), tr("Schon eingetragene Einträge bleiben."),
            onConfirm = { DayRepo.deleteTemplate(ctx, template.id); confirmDelete = false; onDone() }, onDismiss = { confirmDelete = false })
    }
}

// --- catalog ------------------------------------------------------------------------------------------------------

private enum class CatalogTab(val label: String) { FOODS(tr("Lebensmittel")), DISHES(tr("Gerichte")), TEMPLATES(tr("Vorlagen")) }

private sealed class CatalogMode {
    data object List : CatalogMode()
    data class EditFood(val food: Food?, val dish: Boolean) : CatalogMode()
    data class EditTemplate(val template: MealTemplate?) : CatalogMode()
}

/** Own foods, dishes and templates: look at, create, change, delete. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CatalogSheet(onDone: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var mode by remember { mutableStateOf<CatalogMode>(CatalogMode.List) }
    var tab by rememberSaveable { mutableStateOf(CatalogTab.FOODS) }
    ModalBottomSheet(onDismissRequest = onDone, sheetState = sheet) {
        when (val m = mode) {
            CatalogMode.List -> CatalogList(tab, onTab = { tab = it }, onFood = { f, dish -> mode = CatalogMode.EditFood(f, dish) },
                onTemplate = { mode = CatalogMode.EditTemplate(it) })
            is CatalogMode.EditFood -> FoodEditor(m.food, dish = m.dish, onSaved = { mode = CatalogMode.List },
                onBack = { mode = CatalogMode.List }, onDeleted = { mode = CatalogMode.List })
            is CatalogMode.EditTemplate -> TemplateEditor(m.template, onDone = { mode = CatalogMode.List })
        }
    }
}

@Composable
private fun CatalogList(tab: CatalogTab, onTab: (CatalogTab) -> Unit, onFood: (Food?, Boolean) -> Unit, onTemplate: (MealTemplate?) -> Unit) {
    val dayState by DayRepo.state.collectAsState()
    val food by FoodRepo.state.collectAsState()
    var query by rememberSaveable { mutableStateOf("") }
    var showArchived by rememberSaveable { mutableStateOf(false) }
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    val catalog = food.catalog(dayState.pending)
    val words = query.lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
    fun matches(f: Food) = words.all { w -> w in "${f.name} ${f.brand.orEmpty()}".lowercase() || f.barcode == w }
    Column(Modifier.fillMaxWidth().fillMaxHeight().padding(horizontal = 16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(tr("Lebensmittel, Gerichte, Vorlagen"), style = MaterialTheme.typography.titleMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            CatalogTab.entries.forEach { t -> FilterChip(selected = tab == t, onClick = { onTab(t) }, label = { Text(t.label) }) }
        }
        OutlinedTextField(query, { query = it }, label = { Text(tr("Suchen")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Row(verticalAlignment = Alignment.CenterVertically) {
            Button(onClick = { if (tab == CatalogTab.TEMPLATES) onTemplate(null) else onFood(null, tab == CatalogTab.DISHES) }) {
                VIcon(R.drawable.ic_t_plus, size = 16.dp)
                Text(when (tab) { CatalogTab.FOODS -> tr(" Neues Lebensmittel"); CatalogTab.DISHES -> tr(" Neues Gericht"); CatalogTab.TEMPLATES -> tr(" Neue Vorlage") })
            }
            if (tab != CatalogTab.TEMPLATES) {
                Row(Modifier.weight(1f)) {}
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.clickable { showArchived = !showArchived }) {
                    Checkbox(showArchived, { showArchived = it })
                    Text(tr("Archivierte"), style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        HorizontalDivider()
        if (tab == CatalogTab.TEMPLATES) {
            val list = food.templates(dayState.pending).filter { query.isBlank() || it.name.contains(query.trim(), ignoreCase = true) }
            if (list.isEmpty()) Text(tr("Noch keine Vorlagen."), style = MaterialTheme.typography.bodySmall, color = muted)
            list.forEach { t ->
                Column(Modifier.fillMaxWidth().clickable { onTemplate(t) }.padding(vertical = 6.dp)) {
                    Text((if (t.favorite) "★ " else "") + t.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(macroLine(t.nutrients) + " · " + t.items.joinToString(", ") { it.name }, style = MaterialTheme.typography.bodySmall,
                        color = muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
        } else {
            val dishes = tab == CatalogTab.DISHES
            val list = catalog.values.filter { it.dish == dishes && (showArchived || !it.archived) && matches(it) }
                .sortedWith(compareByDescending<Food> { it.favorite }.thenBy { it.name.lowercase() })
            if (list.isEmpty()) {
                Text(if (dishes) tr("Noch keine Gerichte.") else tr("Keine Lebensmittel gefunden."), style = MaterialTheme.typography.bodySmall, color = muted)
            }
            // a long catalog: the first 200 matches, the search narrows it down
            list.take(200).forEach { f ->
                Column(Modifier.fillMaxWidth().clickable { onFood(f, f.dish) }.padding(vertical = 6.dp)) {
                    Text((if (f.favorite) "★ " else "") + f.name + (f.brand?.let { " · ${it}" } ?: "") + if (f.archived) " (archiviert)" else "",
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text("${macroLine(f.nutrients)} / ${foodNum(f.baseAmount, 0)} ${f.unitLabel}" +
                        (f.components?.let { c -> " · " + c.joinToString(", ") { it.name } } ?: ""),
                        style = MaterialTheme.typography.bodySmall, color = muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            if (list.size > 200) Text(tr("… und {0} weitere – Suche eingrenzen.", list.size - 200), style = MaterialTheme.typography.bodySmall, color = muted)
        }
        Row(Modifier.padding(bottom = 24.dp)) {}
    }
}

// --- goals ----------------------------------------------------------------------------------------------------------

/** Nutrient goals (min and/or max), valid from today on; earlier days keep theirs. Needs the server. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GoalsSheet(goals: List<NutrientGoal>, onDone: (changed: Boolean) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val start = remember { GOAL_METRICS.associate { (m, _, _) -> goals.firstOrNull { it.metric == m }.let { g -> m to ((g?.min?.let(::numInput) ?: "") to (g?.max?.let(::numInput) ?: "")) } } }
    var values by remember { mutableStateOf(start) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    fun bad(m: String): Boolean {
        val (lo, hi) = values[m] ?: ("" to "")
        val l = if (lo.isBlank()) null else parseNum(lo) ?: return true
        val h = if (hi.isBlank()) null else parseNum(hi) ?: return true
        return l != null && h != null && l > h
    }
    ModalBottomSheet(onDismissRequest = { onDone(false) }, sheetState = sheet) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(tr("Ziele"), style = MaterialTheme.typography.titleMedium)
            Text(tr("Mindestens und/oder höchstens pro Tag; leer = kein Ziel. Gilt ab heute, frühere Tage behalten ihre Ziele."),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            GOAL_METRICS.forEach { (m, label, unit) ->
                val (lo, hi) = values[m] ?: ("" to "")
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("${label} (${unit})", modifier = Modifier.weight(1f))
                    OutlinedTextField(lo, { values = values + (m to (it to hi)) }, label = { Text("min") }, singleLine = true, isError = bad(m),
                        modifier = Modifier.width(92.dp), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                    OutlinedTextField(hi, { values = values + (m to (lo to it)) }, label = { Text("max") }, singleLine = true, isError = bad(m),
                        modifier = Modifier.width(92.dp), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                }
            }
            message?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 24.dp)) {
                Button(enabled = !busy && GOAL_METRICS.none { bad(it.first) }, onClick = {
                    busy = true
                    message = null
                    scope.launch {
                        val s = Settings(ctx)
                        val r = withContext(Dispatchers.IO) {
                            runCatching {
                                val api = Api(s.serverUrl, s.token)
                                values.filter { (m, v) -> v != start[m] }.forEach { (m, v) ->
                                    api.setGoal(m, v.first.takeIf { it.isNotBlank() }?.let(::parseNum), v.second.takeIf { it.isNotBlank() }?.let(::parseNum))
                                }
                            }
                        }
                        busy = false
                        r.onSuccess { onDone(true) }.onFailure {
                            message = if (it is java.io.IOException && it !is ApiException) tr("Server nicht erreichbar – Ziele lassen sich nur online ändern.")
                                else it.describe()
                        }
                    }
                }) { Text(tr("Speichern")) }
                TextButton(onClick = { onDone(false) }) { Text(tr("Abbrechen")) }
            }
        }
    }
}
