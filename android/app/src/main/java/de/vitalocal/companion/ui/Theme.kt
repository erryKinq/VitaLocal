package de.vitalocal.companion.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// The same tokens as the WebUI (server/src/vitalocal/web/app.css :root), so app and browser look alike.

/** Colours Material has no slot for: status, flames and one colour per area. */
@Immutable
data class VitaColors(
    val ok: Color, val okSoft: Color, val warn: Color, val warnSoft: Color, val flame: Color, val flameSoft: Color,
    val muted: Color, val faint: Color, val border: Color, val surface2: Color, val gardenSky: Color,
    val day: Color, val food: Color, val habit: Color, val train: Color, val health: Color, val mind: Color,
    val water: Color, val gold: Color,
    /** Muscle map: still fatigued / recovering. */
    val fatigued: Color, val recovering: Color,
)

private val LightVita = VitaColors(
    ok = Color(0xFF15803D), okSoft = Color(0xFFE3F4E8), warn = Color(0xFFB45309), warnSoft = Color(0xFFFDF0DC),
    flame = Color(0xFFEA580C), flameSoft = Color(0xFFFFEDD5), muted = Color(0xFF5F6B67), faint = Color(0xFF8D9894),
    border = Color(0xFFE3E7E3), surface2 = Color(0xFFF0F2EF), gardenSky = Color(0xFFE6F4F1),
    day = Color(0xFF0F766E), food = Color(0xFFC2410C), habit = Color(0xFF15803D), train = Color(0xFF4F46E5),
    health = Color(0xFFBE185D), mind = Color(0xFF7C3AED), water = Color(0xFF0891B2), gold = Color(0xFFC99A06),
    fatigued = Color(0xFFDC2626), recovering = Color(0xFFF59E0B),
)

private val DarkVita = VitaColors(
    ok = Color(0xFF4ADE80), okSoft = Color(0xFF12301D), warn = Color(0xFFFBBF24), warnSoft = Color(0xFF3A2C10),
    flame = Color(0xFFFB923C), flameSoft = Color(0xFF3B2312), muted = Color(0xFF9AA6A2), faint = Color(0xFF6D7A76),
    border = Color(0xFF263130), surface2 = Color(0xFF1E2624), gardenSky = Color(0xFF14302B),
    day = Color(0xFF2DD4BF), food = Color(0xFFFB923C), habit = Color(0xFF4ADE80), train = Color(0xFFA5B4FC),
    health = Color(0xFFF472B6), mind = Color(0xFFC4B5FD), water = Color(0xFF22D3EE), gold = Color(0xFFFACC15),
    fatigued = Color(0xFFF87171), recovering = Color(0xFFFBBF24),
)

private val LightScheme = lightColorScheme(
    primary = Color(0xFF0F766E), onPrimary = Color.White,
    primaryContainer = Color(0xFFDCF0EC), onPrimaryContainer = Color(0xFF04201D),
    secondary = Color(0xFF5F6B67), onSecondary = Color.White,
    secondaryContainer = Color(0xFFE6E9E5), onSecondaryContainer = Color(0xFF19201E),
    tertiary = Color(0xFFB45309), onTertiary = Color.White,
    background = Color(0xFFF5F6F4), onBackground = Color(0xFF19201E),
    surface = Color(0xFFF5F6F4), onSurface = Color(0xFF19201E),
    surfaceVariant = Color(0xFFF0F2EF), onSurfaceVariant = Color(0xFF5F6B67),
    surfaceContainerLowest = Color.White, surfaceContainerLow = Color.White, surfaceContainer = Color.White,
    surfaceContainerHigh = Color(0xFFF0F2EF), surfaceContainerHighest = Color(0xFFE6E9E5),
    outline = Color(0xFFCDD3CE), outlineVariant = Color(0xFFE3E7E3),
    error = Color(0xFFC2410C), onError = Color.White,
)

private val DarkScheme = darkColorScheme(
    primary = Color(0xFF2DD4BF), onPrimary = Color(0xFF04201D),
    primaryContainer = Color(0xFF113C38), onPrimaryContainer = Color(0xFFCCF5EE),
    secondary = Color(0xFF9AA6A2), onSecondary = Color(0xFF0E1312),
    secondaryContainer = Color(0xFF27302E), onSecondaryContainer = Color(0xFFE8EDEB),
    tertiary = Color(0xFFFBBF24), onTertiary = Color(0xFF241A04),
    background = Color(0xFF0E1312), onBackground = Color(0xFFE8EDEB),
    surface = Color(0xFF0E1312), onSurface = Color(0xFFE8EDEB),
    surfaceVariant = Color(0xFF1E2624), onSurfaceVariant = Color(0xFF9AA6A2),
    surfaceContainerLowest = Color(0xFF161C1B), surfaceContainerLow = Color(0xFF161C1B), surfaceContainer = Color(0xFF161C1B),
    surfaceContainerHigh = Color(0xFF1E2624), surfaceContainerHighest = Color(0xFF27302E),
    outline = Color(0xFF35413F), outlineVariant = Color(0xFF263130),
    error = Color(0xFFFB923C), onError = Color(0xFF241A04),
)

// Two weights only (regular, semibold), the WebUI's scale 12/13/15/18/22/28.
private val VitaTypography = Typography().let { d ->
    val bold = FontWeight.SemiBold
    d.copy(
        headlineMedium = d.headlineMedium.copy(fontSize = 28.sp, lineHeight = 34.sp, fontWeight = bold),
        headlineSmall = d.headlineSmall.copy(fontSize = 22.sp, lineHeight = 28.sp, fontWeight = bold),
        titleLarge = d.titleLarge.copy(fontSize = 18.sp, lineHeight = 24.sp, fontWeight = bold),
        titleMedium = d.titleMedium.copy(fontSize = 15.sp, lineHeight = 20.sp, fontWeight = bold),
        titleSmall = d.titleSmall.copy(fontSize = 13.sp, lineHeight = 18.sp, fontWeight = bold),
        bodyLarge = d.bodyLarge.copy(fontSize = 15.sp, lineHeight = 22.sp),
        bodyMedium = d.bodyMedium.copy(fontSize = 14.sp, lineHeight = 20.sp),
        bodySmall = d.bodySmall.copy(fontSize = 12.sp, lineHeight = 16.sp),
        labelLarge = d.labelLarge.copy(fontSize = 14.sp, fontWeight = bold),
        labelMedium = d.labelMedium.copy(fontSize = 12.sp, fontWeight = bold),
        labelSmall = d.labelSmall.copy(fontSize = 11.sp),
    )
}

/** Big tabular numbers (values, totals). */
val NumberStyle = TextStyle(fontSize = 22.sp, lineHeight = 26.sp, fontWeight = FontWeight.SemiBold)

val LocalVita = staticCompositionLocalOf { LightVita }

/** VitaLocal's own colours (not the wallpaper's), light or dark with the system. */
@Composable
fun VitaTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    CompositionLocalProvider(LocalVita provides if (dark) DarkVita else LightVita) {
        MaterialTheme(colorScheme = if (dark) DarkScheme else LightScheme, typography = VitaTypography, content = content)
    }
}

object Vita {
    val colors: VitaColors @Composable get() = LocalVita.current
}

/**
 * Inside an area (a tab) its colour is the accent: buttons, chips, switches and progress follow it,
 * as in the WebUI.
 */
@Composable
fun AreaTheme(accent: (VitaColors) -> Color, content: @Composable () -> Unit) {
    val base = MaterialTheme.colorScheme
    val c = accent(LocalVita.current)
    val soft = c.copy(alpha = 0.16f).compositeOver(base.surfaceContainer)
    MaterialTheme(
        colorScheme = base.copy(primary = c, primaryContainer = soft, secondaryContainer = soft, onSecondaryContainer = base.onSurface),
        typography = MaterialTheme.typography, content = content,
    )
}
