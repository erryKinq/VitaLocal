package de.vitalocal.companion.ui

import de.vitalocal.companion.I18n
import de.vitalocal.companion.Settings
import de.vitalocal.companion.tr
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.ui.platform.LocalContext
import androidx.annotation.DrawableRes
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import de.vitalocal.companion.R

/** Opens the settings (incl. sync) from any screen; "sync" jumps to the sync status. */
val LocalOpenSettings = compositionLocalOf<(String?) -> Unit> { {} }

val CardShape = RoundedCornerShape(16.dp)

@Composable
fun VIcon(@DrawableRes id: Int, modifier: Modifier = Modifier, size: Dp = 20.dp, tint: Color = LocalContentColor.current) =
    Icon(painterResource(id), contentDescription = null, modifier = modifier.size(size), tint = tint)

/** A stored emoji (habit, achievement) as its line icon; unknown emojis stay text. */
@Composable
fun Glyph(emoji: String?, size: Dp = 18.dp, tint: Color = LocalContentColor.current) {
    val id = emojiIcon(emoji)
    when {
        id != null -> VIcon(id, size = size, tint = tint)
        !emoji.isNullOrBlank() -> Text(emoji, style = MaterialTheme.typography.bodyLarge)
    }
}

private val PLANTS = mapOf(
    "seed" to R.drawable.plant_seed, "sprout" to R.drawable.plant_sprout, "seedling" to R.drawable.plant_seedling,
    "bush" to R.drawable.plant_bush, "bloom" to R.drawable.plant_bloom, "tree" to R.drawable.plant_tree, "mighty" to R.drawable.plant_mighty,
)
private val STAGE_KEYS = listOf("seed", "sprout", "seedling", "bush", "bloom", "tree", "mighty")

fun plantRes(stage: String?): Int = PLANTS[stage] ?: R.drawable.plant_seed
fun stageKey(index: Int): String = STAGE_KEYS.getOrElse(index) { "mighty" }


/** Screen title with an area icon, a subtitle and actions; the gear opens the settings. */
@Composable
fun ScreenHeader(
    title: String, subtitle: String? = null, @DrawableRes icon: Int? = null, tint: Color = MaterialTheme.colorScheme.primary,
    actions: @Composable RowScope.() -> Unit = {},
) {
    val openSettings = LocalOpenSettings.current
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        if (icon != null) {
            VIcon(icon, size = 24.dp, tint = tint)
            Spacer(Modifier.width(10.dp))
        }
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.headlineSmall)
            subtitle?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
        actions()
        IconButton(onClick = { openSettings(null) }) { VIcon(R.drawable.ic_t_settings, tint = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}

/**
 * The one card of the app: icon + title + trailing in the head. With [collapsedKey] the card folds
 * (its state survives rotation); [summary] then stays visible in the head.
 */
@Composable
fun VCard(
    title: String? = null,
    @DrawableRes icon: Int? = null,
    tint: Color = MaterialTheme.colorScheme.primary,
    modifier: Modifier = Modifier,
    summary: String? = null,
    collapsedKey: String? = null,
    startOpen: Boolean = true,
    trailing: @Composable RowScope.() -> Unit = {},
    content: @Composable ColumnScope.() -> Unit,
) {
    var open by rememberSaveable(collapsedKey) { mutableStateOf(collapsedKey == null || startOpen) }
    Card(
        modifier.fillMaxWidth(), shape = CardShape,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        border = BorderStroke(1.dp, Vita.colors.border),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (title != null) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = if (collapsedKey != null) Modifier.fillMaxWidth().clickable { open = !open } else Modifier.fillMaxWidth(),
                ) {
                    if (icon != null) {
                        VIcon(icon, size = 20.dp, tint = tint)
                        Spacer(Modifier.width(8.dp))
                    }
                    Column(Modifier.weight(1f)) {
                        Text(title, style = MaterialTheme.typography.titleMedium)
                        if (summary != null && (collapsedKey != null)) {
                            Text(summary, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                    trailing()
                    if (collapsedKey != null) {
                        val angle by animateFloatAsState(if (open) 180f else 0f, label = "chev")
                        VIcon(R.drawable.ic_t_chevron_down, Modifier.rotate(angle), size = 18.dp, tint = Vita.colors.faint)
                    }
                }
            }
            AnimatedVisibility(open) { Column(verticalArrangement = Arrangement.spacedBy(10.dp)) { content() } }
        }
    }
}

/** A value tile: label with icon, the value, a muted line (e.g. the 28-day mean). */
@Composable
fun StatTile(
    label: String, value: String, modifier: Modifier = Modifier, unit: String? = null, sub: String? = null,
    @DrawableRes icon: Int? = null, tint: Color = Vita.colors.faint,
) {
    Column(
        modifier.clip(RoundedCornerShape(12.dp)).background(Vita.colors.surface2).padding(horizontal = 12.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (icon != null) { VIcon(icon, size = 14.dp, tint = tint); Spacer(Modifier.width(4.dp)) }
            Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1,
                overflow = TextOverflow.Ellipsis)
        }
        Row(verticalAlignment = Alignment.Bottom) {
            Text(value, style = MaterialTheme.typography.titleLarge)
            unit?.let { Text(" ${it}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
        sub?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}

/** A small status pill: green when fine, amber when something is old or failed. */
@Composable
fun StatusPill(text: String, ok: Boolean, onClick: (() -> Unit)? = null) {
    val c = Vita.colors
    Surface(
        shape = CircleShape, color = if (ok) c.okSoft else c.warnSoft,
        modifier = if (onClick != null) Modifier.clip(CircleShape).clickable(onClick = onClick) else Modifier,
    ) {
        Row(Modifier.padding(horizontal = 10.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(if (ok) c.ok else c.warn))
            Spacer(Modifier.width(6.dp))
            Text(text, style = MaterialTheme.typography.labelMedium, color = if (ok) c.ok else c.warn)
        }
    }
}

/** A round tick button for check habits. */
@Composable
fun RoundCheck(checked: Boolean, color: Color, onClick: () -> Unit) {
    Box(
        Modifier.size(36.dp).clip(CircleShape)
            .background(if (checked) color else Color.Transparent)
            .border(2.dp, if (checked) color else MaterialTheme.colorScheme.outline, CircleShape)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        if (checked) VIcon(R.drawable.ic_t_check, size = 20.dp, tint = MaterialTheme.colorScheme.surface)
    }
}

/** − value + for counts (habits, supplements). */
@Composable
fun Stepper(text: String, done: Boolean, minusEnabled: Boolean, onMinus: () -> Unit, onPlus: () -> Unit, color: Color) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        SmallRound(R.drawable.ic_t_minus, enabled = minusEnabled, onClick = onMinus)
        Text(text, Modifier.padding(horizontal = 8.dp), style = MaterialTheme.typography.titleSmall,
            color = if (done) color else MaterialTheme.colorScheme.onSurface)
        SmallRound(R.drawable.ic_t_plus, filled = done, color = color, onClick = onPlus)
    }
}

@Composable
private fun SmallRound(@DrawableRes id: Int, enabled: Boolean = true, filled: Boolean = false, color: Color = Color.Unspecified, onClick: () -> Unit) {
    Box(
        Modifier.size(32.dp).clip(CircleShape)
            .background(if (filled) color else Vita.colors.surface2)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        VIcon(id, size = 16.dp, tint = when {
            filled -> MaterialTheme.colorScheme.surface
            enabled -> MaterialTheme.colorScheme.onSurface
            else -> Vita.colors.faint
        })
    }
}

/** A muted line of help or status text. */
@Composable
fun Muted(text: String, modifier: Modifier = Modifier) =
    Text(text, modifier, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

/** A small section title between cards. */
@Composable
fun SectionLabel(text: String) =
    Text(text, Modifier.padding(top = 8.dp), style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
        fontWeight = FontWeight.SemiBold)

/** "Einträge (3)" with a chevron: shows or hides a list below. */
@Composable
fun FoldToggle(label: String, open: Boolean, onToggle: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).clickable(onClick = onToggle).padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
        VIcon(if (open) R.drawable.ic_t_chevron_up else R.drawable.ic_t_chevron_down, size = 16.dp, tint = Vita.colors.faint)
    }
}

/** A small number field (40 dp high) for one row with buttons. */
@Composable
fun CompactNumberField(value: String, onValueChange: (String) -> Unit, placeholder: String, modifier: Modifier = Modifier) {
    androidx.compose.foundation.text.BasicTextField(
        value = value, onValueChange = onValueChange, singleLine = true,
        textStyle = MaterialTheme.typography.bodyMedium.copy(color = MaterialTheme.colorScheme.onSurface),
        cursorBrush = androidx.compose.ui.graphics.SolidColor(MaterialTheme.colorScheme.primary),
        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Number),
        modifier = modifier.height(40.dp),
        decorationBox = { inner ->
            Box(Modifier.fillMaxSize().clip(RoundedCornerShape(20.dp)).border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(20.dp))
                .padding(horizontal = 12.dp), contentAlignment = Alignment.CenterStart) {
                if (value.isEmpty()) Text(placeholder, style = MaterialTheme.typography.bodyMedium, color = Vita.colors.faint)
                inner()
            }
        },
    )
}

/** How far back the day arrows go (the app caches a week; older days are edited in the WebUI). */
const val DAYS_BACK = 6L

/** "Heute", "Gestern", else weekday and date. */
fun dayLabel(day: String): String {
    val date = java.time.LocalDate.parse(day)
    val today = java.time.LocalDate.now()
    return when (date) {
        today -> tr("Heute")
        today.minusDays(1) -> tr("Gestern")
        else -> date.format(java.time.format.DateTimeFormatter.ofPattern(I18n.pattern("EEEE, d. MMM"), I18n.locale))
    }
}

/** ‹ › over the last [DAYS_BACK] days, for a [ScreenHeader]'s actions. */
@Composable
fun RowScope.DayArrows(day: String, onDay: (String) -> Unit) {
    val date = java.time.LocalDate.parse(day)
    val today = java.time.LocalDate.now()
    IconButton(enabled = date.isAfter(today.minusDays(DAYS_BACK)), onClick = { onDay(date.minusDays(1).toString()) }) {
        VIcon(R.drawable.ic_t_chevron_left)
    }
    IconButton(enabled = date.isBefore(today), onClick = { onDay(date.plusDays(1).toString()) }) { VIcon(R.drawable.ic_t_chevron_right) }
}

/** Opens [path] (e.g. "#/training") of the WebUI on the configured server. */
fun openWeb(context: Context, path: String) {
    val base = Settings(context).serverUrl.trimEnd('/')
    if (base.isEmpty()) return
    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("${base}/${path.trimStart('/')}"))) }
}

/** A quiet "more in the WebUI" link below a card's content. */
@Composable
fun WebLink(label: String, path: String) {
    val ctx = LocalContext.current
    Row(Modifier.clip(RoundedCornerShape(8.dp)).clickable { openWeb(ctx, path) }.padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(4.dp))
        VIcon(R.drawable.ic_t_external_link, size = 14.dp, tint = MaterialTheme.colorScheme.primary)
    }
}
