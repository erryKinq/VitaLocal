package de.vitalocal.companion

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.size
import androidx.compose.material3.FilledTonalIconButton
import de.vitalocal.companion.ui.CompactNumberField
import de.vitalocal.companion.ui.FoldToggle
import de.vitalocal.companion.ui.Muted
import de.vitalocal.companion.ui.NumberStyle
import de.vitalocal.companion.ui.VCard
import de.vitalocal.companion.ui.VIcon
import de.vitalocal.companion.ui.Vita
import java.text.NumberFormat
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

// Drinks and weight: logged on the nutrition tab (offline first, through DayRepo's outbox).

private val DRINKS = listOf("water" to tr("Wasser"), "tea" to tr("Tee"), "coffee" to tr("Kaffee"), "other" to tr("Sonstiges"))

// --- Drinks -----------------------------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun DrinksCard(d: TodayData, goal: Double, pendingIds: Set<String>, day: String) {
    val ctx = LocalContext.current
    var drink by rememberSaveable { mutableStateOf("water") }
    var caffeine by rememberSaveable { mutableStateOf(false) }
    var custom by rememberSaveable { mutableStateOf("") }
    var editing by remember { mutableStateOf<Drink?>(null) }
    val add = { ml: Double -> DayRepo.addDrink(ctx, day, ml, drink, caffeine) }

    val accent = Vita.colors.food
    var showEntries by rememberSaveable { mutableStateOf(false) }
    VCard(tr("Getränke"), R.drawable.ic_t_droplet, tint = accent, trailing = {
        Text("${((d.drinksTotal / goal) * 100).toInt()} %", style = MaterialTheme.typography.labelMedium, color = accent)
    }) {
        Row(verticalAlignment = Alignment.Bottom) {
            Text(formatMl(d.drinksTotal), style = NumberStyle, color = accent)
            Text(" / ${formatMl(goal)}", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        LinearProgressIndicator(progress = { (d.drinksTotal / goal).toFloat().coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth().height(8.dp),
            color = accent, trackColor = Vita.colors.surface2, drawStopIndicator = {})
        d.drinkGoal?.let { g ->
            if (g.baseMl != null && g.trainingMl != null) Muted(tr("Ziel = {0} Basis + {1} Training", formatMl(g.baseMl), formatMl(g.trainingMl)))
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            DRINKS.forEach { (k, label) ->
                FilterChip(selected = drink == k, onClick = { drink = k; caffeine = caffeinatedByDefault(k) }, label = { Text(label) })
            }
            FilterChip(selected = caffeine, onClick = { caffeine = !caffeine }, label = { Text(tr("koffeinhaltig")) },
                leadingIcon = { VIcon(R.drawable.ic_t_coffee, size = 16.dp) })
        }
        // three quick amounts, an own amount and its + in one row
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            listOf(250, 330, 500).forEach { ml ->
                FilledTonalButton(onClick = { add(ml.toDouble()) }, modifier = Modifier.weight(1f).height(40.dp),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 2.dp)) {
                    Text("+${ml}", style = MaterialTheme.typography.labelMedium)
                }
            }
            val ml = custom.toIntOrNull()
            CompactNumberField(custom, { v -> custom = v.filter(Char::isDigit).take(4) }, "ml", Modifier.weight(1.1f))
            FilledTonalIconButton(enabled = ml != null && ml in 1..5000, onClick = { add(ml!!.toDouble()); custom = "" },
                modifier = Modifier.size(40.dp)) { VIcon(R.drawable.ic_t_plus, size = 18.dp) }
        }
        if (d.drinks.isNotEmpty()) {
            FoldToggle(tr("Einträge ({0})", d.drinks.size), showEntries) { showEntries = !showEntries }
            if (showEntries) {
                d.drinks.asReversed().forEach { x ->
                    EntryRow(
                        time = clock(x.ts),
                        text = "${DRINKS.firstOrNull { it.first == x.drink }?.second ?: tr("Getränk")} ${formatMl(x.ml)}${if (x.caffeine) tr(" · koffeinhaltig") else ""}",
                        pending = x.id in pendingIds,
                        onDelete = { DayRepo.deleteDrink(ctx, x.id) },
                        onClick = { editing = x },
                    )
                }
                Muted(tr("Tippe auf einen Eintrag, um ihn zu ändern."))
            }
        }
    }
    editing?.let { x -> EditDrinkDialog(x, onDismiss = { editing = null }, onSave = { DayRepo.updateDrink(ctx, day, it); editing = null }) }
}

/** Corrects kind, amount and caffeine of a logged drink (the time stays). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun EditDrinkDialog(x: Drink, onDismiss: () -> Unit, onSave: (Drink) -> Unit) {
    var drink by remember { mutableStateOf(x.drink) }
    var caffeine by remember { mutableStateOf(x.caffeine) }
    var ml by remember { mutableStateOf(x.ml.toInt().toString()) }
    val amount = ml.toIntOrNull()
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(tr("Getränk um {0}", clock(x.ts))) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    DRINKS.forEach { (k, label) ->
                        FilterChip(selected = drink == k, onClick = { drink = k; caffeine = caffeinatedByDefault(k) }, label = { Text(label) })
                    }
                }
                FilterChip(selected = caffeine, onClick = { caffeine = !caffeine }, label = { Text(tr("koffeinhaltig")) },
                    leadingIcon = { VIcon(R.drawable.ic_t_coffee, size = 16.dp) })
                OutlinedTextField(
                    value = ml, onValueChange = { v -> ml = v.filter(Char::isDigit).take(4) },
                    label = { Text("ml") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
            }
        },
        confirmButton = {
            TextButton(enabled = amount != null && amount in 1..5000, onClick = {
                onSave(x.copy(drink = drink, caffeine = caffeine, ml = amount!!.toDouble()))
            }) { Text(tr("Speichern")) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(tr("Abbrechen")) } },
    )
}

@Composable
internal fun EntryRow(time: String, text: String, pending: Boolean, onDelete: () -> Unit, onClick: (() -> Unit)? = null) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier,
    ) {
        Text(time, style = MaterialTheme.typography.bodySmall, color = muted, modifier = Modifier.width(48.dp))
        Text(text, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
        if (pending) VIcon(R.drawable.ic_t_clock, size = 16.dp, tint = muted)
        IconButton(onClick = onDelete) { VIcon(R.drawable.ic_t_x, size = 18.dp, tint = muted) }
    }
}

// --- Weight -----------------------------------------------------------------------------------

@Composable
fun WeightCard(d: TodayData, pending: Boolean, day: String) {
    val ctx = LocalContext.current
    var input by rememberSaveable { mutableStateOf("") }
    val kg = input.trim().replace(',', '.').toDoubleOrNull()?.let(BodyUnits::toKg)?.takeIf { it in 20.0..400.0 }
    val current = d.metrics["weight"]?.value
    VCard(tr("Gewicht"), R.drawable.ic_t_scale, tint = Vita.colors.food, trailing = {
        Text((current?.let { "${num(BodyUnits.shown(it), 1)} ${BodyUnits.unit}" } ?: "–") + if (pending) tr(" · wartet") else "", style = MaterialTheme.typography.bodyMedium)
    }) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = input, onValueChange = { input = it.take(6) }, label = { Text(BodyUnits.unit) }, singleLine = true,
                modifier = Modifier.weight(1f), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            )
            FilledTonalButton(enabled = kg != null, onClick = {
                DayRepo.addBody(ctx, day, "weight", kg!!)
                input = ""
            }) { Text(tr("Speichern")) }
        }
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
