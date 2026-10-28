package de.vitalocal.companion

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.work.WorkInfo
import androidx.work.WorkManager
import de.vitalocal.companion.ui.VCard
import de.vitalocal.companion.ui.Vita
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** Sync status and log (Settings › Sync status): server, live sync, sources, "sync now". */
@Composable
fun SyncStatusScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    val settings = remember { Settings(ctx) }
    val state by Store.state.collectAsState()
    val work by remember { WorkManager.getInstance(ctx).getWorkInfosForUniqueWorkFlow(Scheduler.MANUAL) }
        .collectAsState(initial = emptyList())
    val running = work.any { it.state == WorkInfo.State.RUNNING || it.state == WorkInfo.State.ENQUEUED }

    LazyColumn(
        Modifier.fillMaxWidth(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { SubHeader(tr("Sync-Status & Protokoll"), onBack) }
        item {
            VCard(tr("Server"), R.drawable.ic_t_cloud) {
                Column {
                    if (!settings.isConfigured) {
                        Text(tr("Noch nicht eingerichtet"), color = MaterialTheme.colorScheme.error)
                        TextButton(onClick = onBack) { Text(tr("Zu den Einstellungen")) }
                    } else {
                        Text(settings.serverUrl, style = MaterialTheme.typography.bodySmall)
                        Text(
                            when (state.serverReachable) {
                                null -> tr("Noch nicht geprüft")
                                true -> tr("Erreichbar · {0}", formatTime(state.lastServerCheck))
                                false -> tr("Nicht erreichbar · {0}", formatTime(state.lastServerCheck))
                            },
                            color = if (state.serverReachable == false) MaterialTheme.colorScheme.error else Color.Unspecified,
                        )
                    }
                }
            }
        }
        if (settings.liveEnabled) item { LiveCard() }
        val shown = Source.entries.filter { src ->
            when (src) {
                Source.GADGETBRIDGE -> settings.gadgetbridgeFolder != null && settings.dataSource == "gadgetbridge"
                Source.HEALTH_CONNECT -> settings.dataSource == "health_connect"
                Source.SHARED -> state.sources[src] != null
            }
        }
        items(shown) { src -> SourceCard(src, state.sources[src] ?: SourceState()) }
        item {
            Column {
                Button(
                    onClick = { Scheduler.syncNow(ctx) },
                    enabled = settings.isConfigured && !running,
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (running) tr("Synchronisiere …") else tr("Jetzt synchronisieren")) }
                if (running) LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 4.dp))
                Text(
                    tr("Automatisch alle {0} h, sobald der Server erreichbar ist", settings.intervalHours),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(tr("Protokoll"), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                if (state.log.isNotEmpty()) TextButton(onClick = { Store.clearLog() }) { Text(tr("Leeren")) }
            }
        }
        if (state.log.isEmpty()) item { Text(tr("Noch keine Einträge"), color = MaterialTheme.colorScheme.onSurfaceVariant) }
        items(state.log.take(100)) { LogRow(it) }
    }
}

/** Live sync: the last cycle step by step, the next run and the day's foreground-service time. */
@Composable
private fun LiveCard() {
    val live by LiveState.state.collectAsState()
    val day by DayRepo.state.collectAsState()
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    VCard(tr("Live-Sync"), R.drawable.ic_t_bolt, trailing = {
        when {
            live.paused -> Text(tr("pausiert"), color = Vita.colors.warn, style = MaterialTheme.typography.labelMedium)
            live.backoff > 1 -> Text(tr("verlangsamt"), color = Vita.colors.warn, style = MaterialTheme.typography.labelMedium)
            live.last?.ok == false -> Text(tr("Fehler"), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.labelMedium)
            else -> Text(tr("aktiv"), color = Vita.colors.ok, style = MaterialTheme.typography.labelMedium)
        }
    }) {
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            day.view(DayRepo.today).lastWatchData?.let { Text(tr("Uhr-Daten bis {0}", watchAge(it)), style = MaterialTheme.typography.bodySmall) }
            live.last?.let { c ->
                Text(tr("Letzter Lauf {0}: {1}", formatTime(c.startedAt), c.message), style = MaterialTheme.typography.bodySmall,
                    color = if (c.ok) Color.Unspecified else MaterialTheme.colorScheme.error)
                c.steps.forEach { s ->
                    Text("${if (s.ok) "✓" else "✗"} ${s.name}: ${s.outcome} · ${"%.1f".format(s.ms / 1000.0)} s",
                        style = MaterialTheme.typography.bodySmall, color = muted)
                }
            }
            if (live.nextAt > 0) Text(tr("Nächster Lauf {0}", formatTime(live.nextAt)), style = MaterialTheme.typography.bodySmall, color = muted)
            val used = LiveSchedule.usedLast24h(live.runtime, System.currentTimeMillis())
            Text(tr("Hintergrundzeit 24 h: {0} min von 6 h", used.toMinutes()), style = MaterialTheme.typography.bodySmall, color = muted)
            if (live.paused) Text(tr("Android-Kontingent erreicht; läuft weiter, sobald die App geöffnet wird."),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        }
    }
}

@Composable
private fun SourceCard(source: Source, s: SourceState) {
    VCard(source.title, trailing = { StatusBadge(s.status) }) {
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            s.lastFile?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
            val muted = MaterialTheme.colorScheme.onSurfaceVariant
            if (s.lastCheck > 0) Text(tr("Geprüft: {0}", formatTime(s.lastCheck)), style = MaterialTheme.typography.bodySmall, color = muted)
            if (s.lastUpload > 0) Text(tr("Hochgeladen: {0}", formatTime(s.lastUpload)), style = MaterialTheme.typography.bodySmall, color = muted)
            s.batchId?.let { Text(tr("Import #{0}", it), style = MaterialTheme.typography.bodySmall, color = muted) }
            s.error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
        }
    }
}

@Composable
private fun StatusBadge(status: String?) {
    val (text, color) = when (status) {
        "completed" -> tr("Importiert") to Vita.colors.ok
        "completed_with_errors" -> tr("Mit Fehlern") to Vita.colors.warn
        "pending", "processing" -> tr("Läuft") to MaterialTheme.colorScheme.primary
        "failed", "error" -> tr("Fehler") to MaterialTheme.colorScheme.error
        else -> return
    }
    Text(text, color = color, style = MaterialTheme.typography.labelMedium)
}

@Composable
private fun LogRow(e: LogEntry) {
    val color = when (e.level) {
        Level.INFO -> Color.Unspecified
        Level.WARN -> Vita.colors.warn
        Level.ERROR -> MaterialTheme.colorScheme.error
    }
    Row(Modifier.fillMaxWidth()) {
        Text(
            formatTime(e.ts),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(92.dp),
        )
        Spacer(Modifier.width(4.dp))
        Text(
            listOfNotNull(e.source?.title, e.message).joinToString(": "),
            style = MaterialTheme.typography.bodySmall,
            color = color,
        )
    }
}

private val TIME = DateTimeFormatter.ofPattern("HH:mm")
private val DAY_TIME = DateTimeFormatter.ofPattern("dd.MM. HH:mm")

/** "14:32 (vor 6 min)", or with the date when it is not from today. */
fun watchAge(iso: String): String = runCatching {
    val t = java.time.OffsetDateTime.parse(iso).toInstant()
    val minutes = java.time.Duration.between(t, Instant.now()).toMinutes().coerceAtLeast(0)
    val ago = when {
        minutes < 1 -> tr("gerade eben")
        minutes < 90 -> tr("vor {0} min", minutes)
        minutes < 48 * 60 -> tr("vor {0} h", minutes / 60)
        else -> tr("vor {0} Tagen", minutes / 1440)
    }
    "${formatTime(t.toEpochMilli())} (${ago})"
}.getOrDefault(iso)

fun formatTime(ms: Long): String {
    val t = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault())
    return if (t.toLocalDate() == LocalDate.now()) tr("heute {0}", TIME.format(t)) else DAY_TIME.format(t)
}
