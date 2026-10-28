package de.vitalocal.companion

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import de.vitalocal.companion.ui.Muted
import de.vitalocal.companion.ui.VIcon
import de.vitalocal.companion.ui.Vita
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.time.OffsetDateTime

/** One AI debrief of a workout ([status]: pending, done, error). */
data class Debrief(
    val status: String, val score: Int?, val summary: String?, val highlights: List<String>, val watch: List<String>,
    val nextTime: List<String>, val error: String?, val model: String?, val createdAt: String?,
)

internal fun parseDebrief(o: JSONObject?): Debrief? = o?.let {
    val r = it.optJSONObject("result")
    fun strings(key: String) = r?.optJSONArray(key)?.let { a -> List(a.length()) { i -> a.getString(i) } } ?: emptyList()
    Debrief(it.optString("status"), r?.optIntOrNull("score"), r?.optStringOrNull("summary"), strings("highlights"),
        strings("watch"), strings("next_time"), it.optStringOrNull("error"), it.optStringOrNull("model"), it.optStringOrNull("created_at"))
}

/** The AI coach's reading of a workout, inside "Heutiges Training": a button starts it, the reading
 *  folds in and out. Only there when the server has a model set up. */
@Composable
fun DebriefSection(activityId: Long) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var enabled by remember { mutableStateOf(false) }
    var debrief by remember(activityId) { mutableStateOf<Debrief?>(null) }
    var open by remember(activityId) { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    suspend fun <T> call(f: (Api) -> T): T = withContext(Dispatchers.IO) {
        val s = Settings(ctx)
        f(Api(s.serverUrl, s.token))
    }
    LaunchedEffect(activityId) {
        runCatching {
            enabled = call { it.coachStatus() }.optBoolean("enabled")
            if (enabled) debrief = parseDebrief(call { it.coachMessages(activityId) }.optJSONObject(0))
        }
    }
    // wait for the model: poll while the debrief is pending
    LaunchedEffect(debrief?.status) {
        while (debrief?.status == "pending") {
            delay(3000)
            now = System.currentTimeMillis()
            runCatching { debrief = parseDebrief(call { it.coachMessages(activityId) }.optJSONObject(0)) }
        }
    }
    if (!enabled) return
    val start: () -> Unit = {
        scope.launch {
            error = null
            open = true
            runCatching { debrief = parseDebrief(call { it.startDebrief(activityId) }) }
                .onFailure { e -> error = tr("Nicht gestartet: {0}", e.message) }
        }
    }
    val d = debrief
    Column(Modifier.padding(top = 4.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (d == null) {
            OutlinedButton(onClick = start) {
                VIcon(R.drawable.ic_t_sparkles, size = 16.dp)
                Text(tr("  KI-Auswertung"))
            }
        } else {
            // the head folds the reading in and out
            Row(Modifier.fillMaxWidth().clickable { open = !open }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                VIcon(R.drawable.ic_t_sparkles, size = 16.dp, tint = Vita.colors.train)
                Text(tr("  KI-Auswertung"), fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
                when (d.status) {
                    "done" -> Text("  ${d.score}/10", fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium,
                        color = when { (d.score ?: 0) >= 7 -> Vita.colors.ok; (d.score ?: 0) >= 5 -> Vita.colors.warn; else -> MaterialTheme.colorScheme.error })
                    "pending" -> {
                        val secs = d.createdAt?.let { runCatching { (now - OffsetDateTime.parse(it).toInstant().toEpochMilli()) / 1000 }.getOrNull() }
                        Muted(tr("  läuft …{0}", secs?.let { " ${it / 60}:${"%02d".format(it % 60)}" } ?: ""))
                    }
                    else -> Muted("  " + tr("fehlgeschlagen"))
                }
                Text("", Modifier.weight(1f))
                VIcon(if (open) R.drawable.ic_t_chevron_up else R.drawable.ic_t_chevron_down, size = 18.dp)
            }
            if (open) {
                when (d.status) {
                    "pending" -> Muted(tr("Das lokale Modell liest das Training im Vergleich zu deinen letzten Einheiten."))
                    "error" -> Text(tr("Auswertung fehlgeschlagen: {0}", d.error ?: "?"), color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall)
                    else -> {
                        d.summary?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                        Section(tr("Gut gelaufen"), R.drawable.ic_t_check, d.highlights)
                        Section(tr("Im Blick behalten"), R.drawable.ic_t_eye, d.watch)
                        Section(tr("Nächstes Mal"), R.drawable.ic_t_arrow_right, d.nextTime)
                        Muted(listOfNotNull(d.model, tr("KI-Einschätzung – Plan und Ziele bleiben bei deinen Progressionsregeln")).joinToString(" · "))
                    }
                }
                if (d.status != "pending") TextButton(onClick = start) { Text(tr("Neu auswerten")) }
            }
        }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
    }
}

@Composable
private fun Section(title: String, icon: Int, items: List<String>) {
    if (items.isEmpty()) return
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            VIcon(icon, size = 16.dp)
            Text("  ${title}", fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
        }
        items.forEach { Text("• ${it}", style = MaterialTheme.typography.bodySmall) }
    }
}
