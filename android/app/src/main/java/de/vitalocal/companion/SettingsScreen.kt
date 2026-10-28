package de.vitalocal.companion

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.TextButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.clickable
import androidx.compose.material3.IconButton
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.documentfile.provider.DocumentFile
import de.vitalocal.companion.ui.Muted
import de.vitalocal.companion.ui.SectionLabel
import de.vitalocal.companion.ui.StatusPill
import de.vitalocal.companion.ui.VCard
import de.vitalocal.companion.ui.VIcon
import de.vitalocal.companion.ui.Vita
import androidx.lifecycle.compose.LifecycleResumeEffect
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.LocalTime

/**
 * Settings with everything about the connection and the sync; "sync" opens the sync status
 * and log right away. Reached from the gear on every screen.
 */
@Composable
fun SettingsScreen(section: String, onClose: () -> Unit) {
    var page by rememberSaveable { mutableStateOf(section) }
    if (page == "sync") {
        SyncStatusScreen(onBack = { if (section == "sync") onClose() else page = "" })
        return
    }
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        SubHeader(tr("Einstellungen"), onClose)
        SyncSummary(onOpen = { page = "sync" })
        SectionLabel(tr("Verbindung"))
        ServerSection()
        SectionLabel(tr("Uhr & Synchronisierung"))
        val ctx = LocalContext.current
        var source by remember { mutableStateOf(Settings(ctx).dataSource) }
        DataSourceSection(source) { source = it }
        if (source == "gadgetbridge") {
            GadgetbridgeSection()
            LiveSection()
        }
        if (source == "health_connect") HealthConnectSection()
        SyncSection()
        SectionLabel(tr("Erfassung & Erinnerungen"))
        EntrySection()
        RemindersSection()
        SectionLabel("App")
        UpdateSection()
        Muted("VitaLocal ${BuildConfig.VERSION_NAME}")
    }
}

/** Back arrow and title of a settings page. */
@Composable
fun SubHeader(title: String, onBack: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onBack) { VIcon(R.drawable.ic_t_arrow_left) }
        Text(title, style = MaterialTheme.typography.headlineSmall)
    }
}

/** The sync at a glance; a tap opens status and log. */
@Composable
private fun SyncSummary(onOpen: () -> Unit) {
    val ctx = LocalContext.current
    val settings = remember { Settings(ctx) }
    val state by Store.state.collectAsState()
    val day by DayRepo.state.collectAsState()
    val watch = day.view(DayRepo.today).lastWatchData
    val ok = settings.isConfigured && state.serverReachable != false
    VCard(tr("Sync-Status"), R.drawable.ic_t_refresh, modifier = Modifier.clickable(onClick = onOpen), trailing = {
        VIcon(R.drawable.ic_t_chevron_right, tint = Vita.colors.faint)
    }) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            StatusPill(when {
                !settings.isConfigured -> tr("nicht eingerichtet")
                state.serverReachable == false -> tr("Server nicht erreichbar")
                else -> tr("Server erreichbar")
            }, ok)
            watch?.let { Muted(tr("Uhr-Daten bis {0}", watchAge(it))) }
        }
        Muted(tr("Status der Quellen, Live-Sync, „Jetzt synchronisieren“ und Protokoll"))
    }
}

// keyed by the German titles: [Section] looks up before translating
private val SECTION_ICONS = mapOf(
    "Datenquelle" to R.drawable.ic_t_device_watch, "Health Connect" to R.drawable.ic_t_heart,
    "Server" to R.drawable.ic_t_cloud, "Gadgetbridge" to R.drawable.ic_t_device_watch, "Live-Sync" to R.drawable.ic_t_bolt,
    "Erfassung" to R.drawable.ic_t_droplet, "Erinnerungen" to R.drawable.ic_t_bell, "Synchronisierung" to R.drawable.ic_t_refresh,
    "App-Update" to R.drawable.ic_t_download,
)

/** [title]: the German title, translated here (product names stay as they are). */
@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    VCard(tr(title), SECTION_ICONS[title]) { content() }
}

@Composable
private fun UpdateSection() {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var checked by remember { mutableStateOf(false) }
    Section("App-Update") {
        if (checked) UpdateBanner(showUpToDate = true)
        OutlinedButton(onClick = { scope.launch { AppUpdate.check(ctx, force = true); checked = true } }) { Text(tr("Nach Update suchen")) }
        Hint(tr("Neue Versionen kommen vom VitaLocal-Server (in der WebUI unter Einstellungen → Android-App). Die App fragt beim Start danach; Android lässt dich jede Installation bestätigen."))
    }
}

@Composable
private fun Hint(text: String) =
    Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

@Composable
private fun ServerSection() {
    val ctx = LocalContext.current
    val settings = remember { Settings(ctx) }
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(settings.serverUrl) }
    var token by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<Pair<Boolean, String>?>(null) }
    var busy by remember { mutableStateOf(false) }

    Section("Server") {
        OutlinedTextField(
            value = url, onValueChange = { url = it; result = null },
            label = { Text(tr("Adresse")) }, placeholder = { Text("https://vitalocal.example.arpa") },
            singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = token, onValueChange = { token = it; result = null },
            label = { Text(tr("API-Token")) },
            placeholder = { Text(if (settings.hasToken) tr("gespeichert, leer lassen zum Behalten") else "VITALOCAL_API_TOKEN") },
            singleLine = true, visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            enabled = !busy && url.isNotBlank() && (token.isNotBlank() || settings.hasToken),
            onClick = {
                val invalid = Settings.validateUrl(url)
                if (invalid != null) {
                    result = false to invalid
                    return@Button
                }
                busy = true
                scope.launch {
                    val useToken = token.ifBlank { settings.token }
                    val check = withContext(Dispatchers.IO) { runCatching { Api(url, useToken).checkAccess() } }
                    busy = false
                    check.onSuccess { version ->
                        settings.serverUrl = url
                        if (token.isNotBlank()) settings.token = token
                        token = ""
                        url = settings.serverUrl
                        result = true to tr("Verbunden, Server-Version {0}", version)
                        Store.log(Level.INFO, null, tr("Server eingerichtet: {0}", settings.serverUrl))
                        Scheduler.schedulePeriodic(ctx, update = true)
                        if (Inbox.files(ctx).isNotEmpty()) Scheduler.uploadShared(ctx)
                    }.onFailure { e ->
                        result = false to tr("Nicht gespeichert: {0}", e.message ?: e.javaClass.simpleName)
                    }
                }
            },
        ) { Text(if (busy) tr("Prüfe …") else tr("Speichern & testen")) }
        result?.let { (ok, msg) ->
            Text(msg, color = if (ok) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error)
        }
        Hint(tr("Über den Reverse Proxy auf dem NAS (https) oder im Heimnetz/WireGuard auch per http://."))
    }
}

/** Where measurements come from: a watch via Gadgetbridge, Health Connect, or nothing (logging only). */
@Composable
private fun DataSourceSection(source: String, onChange: (String) -> Unit) {
    val ctx = LocalContext.current
    Section("Datenquelle") {
        listOf(
            "gadgetbridge" to tr("Uhr über Gadgetbridge"),
            "health_connect" to tr("Health Connect (Samsung Health, Fitbit, Google Fit, …)"),
            "none" to tr("Keine – ich erfasse selbst"),
        ).forEach { (key, label) ->
            Row(Modifier.fillMaxWidth().clickable {
                Settings(ctx).dataSource = key
                onChange(key)
            }, verticalAlignment = Alignment.CenterVertically) {
                androidx.compose.material3.RadioButton(selected = source == key, onClick = {
                    Settings(ctx).dataSource = key
                    onChange(key)
                })
                Text(label)
            }
        }
        Hint(tr("Ohne Uhr schätzt VitaLocal den Verbrauch aus deinem Profil und deinen Trainings (Web-App → Einstellungen → Profil)."))
    }
}

/** Health Connect: availability, permissions (incl. reading in the background) and a first sync. */
@Composable
private fun HealthConnectSection() {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val available = remember { HealthConnect.available(ctx) }
    var granted by remember { mutableStateOf(emptySet<String>()) }
    val launcher = rememberLauncherForActivityResult(
        androidx.health.connect.client.PermissionController.createRequestPermissionResultContract(),
    ) { result ->
        granted = result
        if (result.isNotEmpty()) Scheduler.syncHealthConnect(ctx)
    }
    LaunchedEffect(Unit) { granted = runCatching { HealthConnect.granted(ctx) }.getOrDefault(emptySet()) }
    Section("Health Connect") {
        if (!available) {
            Text(tr("Health Connect ist auf diesem Gerät nicht verfügbar oder muss aktualisiert werden."))
            OutlinedButton(onClick = {
                ctx.startActivity(Intent(Intent.ACTION_VIEW,
                    Uri.parse("market://details?id=com.google.android.apps.healthdata")))
            }) { Text(tr("Health Connect öffnen")) }
            return@Section
        }
        val read = granted.intersect(HealthConnect.PERMISSIONS).size
        Text(if (read == 0) tr("Noch kein Zugriff") else tr("Zugriff auf {0} von {1} Datenarten", read, HealthConnect.PERMISSIONS.size))
        if (read > 0) {
            Muted(if (HealthConnect.BACKGROUND in granted) tr("Liest auch im Hintergrund")
                else tr("Liest, wenn die App geöffnet wird (Lesen im Hintergrund nicht erlaubt)"))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { launcher.launch(HealthConnect.requested(ctx)) }) {
                Text(if (read == 0) tr("Zugriff erlauben") else tr("Zugriff ändern"))
            }
            if (read > 0) OutlinedButton(onClick = { scope.launch { Scheduler.syncHealthConnect(ctx) } }) { Text(tr("Jetzt lesen")) }
        }
        Hint(tr("Schritte, Energie, Schlaf, Gewicht, Puls und Trainings, die andere Apps in Health Connect schreiben. ") +
            tr("Die erste Übertragung holt die letzten 30 Tage."))
    }
}

@Composable
private fun GadgetbridgeSection() {
    val ctx = LocalContext.current
    val settings = remember { Settings(ctx) }
    var folder by remember { mutableStateOf(settings.gadgetbridgeFolder) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri: Uri? ->
        if (uri == null) return@rememberLauncherForActivityResult
        val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
        folder?.takeIf { it != uri }?.let { old ->
            runCatching { ctx.contentResolver.releasePersistableUriPermission(old, flags) }
        }
        ctx.contentResolver.takePersistableUriPermission(uri, flags)
        settings.gadgetbridgeFolder = uri
        folder = uri
        Store.log(Level.INFO, Source.GADGETBRIDGE, tr("Ordner gewählt: {0}", folderName(ctx, uri)))
    }
    Section("Gadgetbridge") {
        Text(folder?.let { tr("Ordner: {0}", folderName(ctx, it)) } ?: tr("Kein Ordner gewählt"))
        OutlinedButton(onClick = { picker.launch(folder) }) { Text(if (folder == null) tr("Ordner wählen") else tr("Anderen Ordner wählen")) }
        Hint(
            tr("Einen eigenen Ordner wählen, z. B. Documents/Gadgetbridge (Download selbst lässt Android nicht zu). ") +
                tr("In Gadgetbridge: Einstellungen → Automatisierungen → Auto export zip, die Exportdatei ") +
                tr("(Gadgetbridge.zip) in diesem Ordner anlegen. Für Live-Sync zusätzlich den Datenbank-Export ") +
                tr("(Auto export) in diesen Ordner legen, siehe unten."),
        )
    }
}

/** Live sync: Gadgetbridge syncs the watch and exports its database every few minutes. */
@Composable
private fun LiveSection() {
    val ctx = LocalContext.current
    val settings = remember { Settings(ctx) }
    var enabled by remember { mutableStateOf(settings.liveEnabled) }
    var config by remember { mutableStateOf(settings.liveConfig) }
    val live by LiveState.state.collectAsState()
    var checks by remember { mutableStateOf(liveChecks(ctx)) }
    val notifPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LifecycleResumeEffect(Unit) {
        checks = liveChecks(ctx)
        onPauseOrDispose { }
    }

    fun save(c: LiveConfig) {
        config = c
        settings.liveConfig = c
        if (settings.liveEnabled) LiveScheduler.arm(ctx)
    }

    Section("Live-Sync") {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(tr("Uhr-Daten nahezu live"))
                Hint(tr("VitaLocal lässt Gadgetbridge die Uhr synchronisieren und die Datenbank exportieren; übertragen werden nur die geänderten Teile."))
            }
            Switch(checked = enabled, enabled = settings.isConfigured && settings.gadgetbridgeFolder != null, onCheckedChange = { on ->
                enabled = on
                settings.liveEnabled = on
                if (on) {
                    if (Build.VERSION.SDK_INT >= 33) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                    LiveState.resume()
                    LiveScheduler.arm(ctx)
                    Store.log(Level.INFO, Source.GADGETBRIDGE, tr("Live-Sync eingeschaltet"))
                } else {
                    LiveScheduler.cancel(ctx)
                    Store.log(Level.INFO, Source.GADGETBRIDGE, tr("Live-Sync ausgeschaltet"))
                }
            })
        }
        if (settings.gadgetbridgeFolder == null) Hint(tr("Zuerst oben den Gadgetbridge-Ordner wählen."))
        Text(tr("Tagsüber alle"))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Settings.LIVE_DAY_MINUTES.forEach { m ->
                FilterChip(selected = config.dayMinutes == m, onClick = { save(config.copy(dayMinutes = m)) }, label = { Text("${m} min") })
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr("Nachts"), modifier = Modifier.weight(1f))
            TextButton(onClick = {
                android.app.TimePickerDialog(ctx, { _, h, m -> save(config.copy(nightStart = LocalTime.of(h, m))) },
                    config.nightStart.hour, config.nightStart.minute, true).show()
            }) { Text(config.nightStart.toString()) }
            Text(tr("bis"))
            TextButton(onClick = {
                android.app.TimePickerDialog(ctx, { _, h, m -> save(config.copy(nightEnd = LocalTime.of(h, m))) },
                    config.nightEnd.hour, config.nightEnd.minute, true).show()
            }) { Text(config.nightEnd.toString()) }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Settings.LIVE_NIGHT_HOURS.forEach { h ->
                FilterChip(selected = config.nightHours == h, onClick = { save(config.copy(nightHours = h)) }, label = { Text(tr("alle {0} h", h)) })
            }
        }
        Text(tr("Einrichtung"), fontWeight = FontWeight.SemiBold)
        checks.forEach { c ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(if (c.ok) "✓" else "✗", color = if (c.ok) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(end = 8.dp))
                Column(Modifier.weight(1f)) {
                    Text(c.title)
                    c.hint?.let { Hint(it) }
                }
                c.action?.let { (label, intent) -> TextButton(onClick = { runCatching { ctx.startActivity(intent) } }) { Text(label) } }
            }
        }
        Hint(
            tr("In Gadgetbridge unter Einstellungen → Entwickleroptionen → Intent API einschalten: ") +
                tr("\"Allow activity sync trigger\", \"Broadcast on activity sync finish\", ") +
                tr("\"Allow database export\" und \"Broadcast on database export\". Unter Automatisierungen → ") +
                tr("Auto export (Datenbank) die Exportdatei (Gadgetbridge.db) im VitaLocal-Ordner anlegen, Intervall 0. ") +
                tr("Für Trainings den FIT-Export in diesen Ordner (oder einen Unterordner) legen. Für Schlaf-Details, ") +
                tr("Hauttemperatur und Stockwerke zusätzlich \"Allow zip export\" + \"Broadcast on zip export\" ") +
                tr("einschalten und den ZIP-Auto-Export auf Intervall 0 stellen: VitaLocal fordert ihn bei Bedarf an."),
        )
        OutlinedButton(
            enabled = settings.isConfigured && settings.gadgetbridgeFolder != null,
            onClick = { LiveSyncService.start(ctx, LiveSyncService.TEST) },
        ) { Text(tr("Testlauf")) }
        live.last?.takeIf { it.trigger == LiveSyncService.TEST }?.let { c ->
            Text(tr("Testlauf {0}: {1}", formatTime(c.startedAt), c.message), color = if (c.ok) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error)
            c.steps.forEach { st ->
                Hint("${if (st.ok) "✓" else "✗"} ${st.name}: ${st.outcome} (${st.ms / 1000} s)")
            }
        }
    }
}

private data class LiveCheck(val title: String, val ok: Boolean, val hint: String? = null, val action: Pair<String, Intent>? = null)

private fun liveChecks(ctx: android.content.Context): List<LiveCheck> {
    val settings = Settings(ctx)
    val pkg = GbIntents.packageFor(ctx)
    val version = pkg?.let { GbIntents.versionOf(ctx, it) }
    val appInfo = { p: String -> Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${p}")) }
    val am = ctx.getSystemService(android.app.ActivityManager::class.java)
    val alarms = ctx.getSystemService(android.app.AlarmManager::class.java)
    return listOfNotNull(
        LiveCheck(
            if (pkg != null) tr("Gadgetbridge {0} installiert", version) else tr("Gadgetbridge nicht gefunden"),
            pkg != null,
            if (pkg != null && version != null && !GbIntents.atLeast(version, "0.94")) tr("Intent API für Garmin ab Gadgetbridge 0.94 getestet") else null,
        ),
        pkg?.let {
            val ok = GbIntents.unrestricted(ctx, it)
            LiveCheck(tr("Akku von Gadgetbridge nicht eingeschränkt"), ok,
                if (ok) null else tr("Sonst kann Android den Export im Ruhezustand verzögern: App-Info → Akku → Nicht eingeschränkt"),
                if (ok) null else "Öffnen" to appInfo(it))
        },
        LiveCheck(
            tr("Datenbank-Export im Ordner"),
            settings.gbDatabaseDoc?.let { androidx.documentfile.provider.DocumentFile.fromSingleUri(ctx, it)?.exists() } == true,
            tr("Wird beim ersten Lauf gesucht (eine SQLite-Datei im Gadgetbridge-Ordner)"),
        ),
        LiveCheck(tr("VitaLocal darf im Hintergrund laufen"), !am.isBackgroundRestricted,
            if (am.isBackgroundRestricted) tr("App-Info → Akku → nicht \"Eingeschränkt\"") else null,
            if (am.isBackgroundRestricted) "Öffnen" to appInfo(ctx.packageName) else null),
        LiveCheck(tr("Exakte Alarme erlaubt"), Build.VERSION.SDK_INT < 31 || alarms.canScheduleExactAlarms()),
    )
}

private fun folderName(ctx: android.content.Context, uri: Uri): String =
    DocumentFile.fromTreeUri(ctx, uri)?.name ?: uri.lastPathSegment ?: uri.toString()

@Composable
private fun EntrySection() {
    val ctx = LocalContext.current
    val settings = remember { Settings(ctx) }
    var goal by remember { mutableStateOf(settings.waterGoal) }
    Section("Erfassung") {
        Text(tr("Tagesziel Getränke (ohne Server-Ziel)"))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(1500, 2000, 2500, 3000).forEach { ml ->
                FilterChip(
                    selected = goal == ml,
                    onClick = {
                        goal = ml
                        settings.waterGoal = ml
                        Surfaces.update(ctx)
                    },
                    label = { Text(formatMl(ml.toDouble())) },
                )
            }
        }
        Hint(tr("Das Ziel berechnet normalerweise der Server aus Gewicht und Training (einstellbar in der WebUI unter Ernährung); dieser Wert gilt nur, wenn dort keins gesetzt ist oder noch keine Daten geladen sind."))
        Hint(tr("Schnell erfassen ohne App: Kachel \"+250 ml Wasser\" in den Schnelleinstellungen und das Widget \"VitaLocal Heute\" für den Startbildschirm."))
    }
}

/** Daily reminders per supplement and manual habit, only when the item is still open. */
@Composable
private fun RemindersSection() {
    val ctx = LocalContext.current
    val state by DayRepo.state.collectAsState()
    val d = state.view(DayRepo.today)
    var reminders by remember { mutableStateOf(Reminders.all(ctx).associateBy { it.key }) }
    val notifPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    val items = d.supplements.map { Triple(Reminder.SUPPLEMENT, it.id, it.name) } +
        d.habits.filter { !it.automatic }.map { Triple(Reminder.HABIT, it.id, it.name) }

    fun update(r: Reminder?, key: String) {
        if (r == null) Reminders.remove(ctx, key) else Reminders.set(ctx, r)
        reminders = Reminders.all(ctx).associateBy { it.key }
    }

    Section("Erinnerungen") {
        val streak = reminders.values.firstOrNull { it.kind == Reminder.STREAK }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(tr("Serien in Gefahr"))
                Hint(tr("Abends, wenn eine Serie ab 3 Tagen heute noch offen ist"))
            }
            if (streak != null) {
                TextButton(onClick = {
                    android.app.TimePickerDialog(ctx, { _, h, m -> update(streak.copy(time = LocalTime.of(h, m)), streak.key) },
                        streak.time.hour, streak.time.minute, true).show()
                }) { Text(streak.time.toString()) }
            }
            Switch(checked = streak != null, onCheckedChange = { on ->
                if (on && Build.VERSION.SDK_INT >= 33) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                update(if (on) Reminder.STREAK_DEFAULT else null, Reminder.STREAK_DEFAULT.key)
            })
        }
        if (items.isEmpty()) Hint(tr("Noch keine Supplements oder Gewohnheiten zum Abhaken geladen (Heute-Tab öffnen)."))
        items.forEach { (kind, id, name) ->
            val key = "${kind}:${id}"
            val r = reminders[key]
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(name)
                    Hint(if (kind == Reminder.SUPPLEMENT) tr("Supplement") else tr("Gewohnheit"))
                }
                if (r != null) {
                    TextButton(onClick = {
                        android.app.TimePickerDialog(ctx, { _, h, m -> update(r.copy(time = LocalTime.of(h, m)), key) },
                            r.time.hour, r.time.minute, true).show()
                    }) { Text(r.time.toString()) }
                }
                Switch(checked = r != null, onCheckedChange = { on ->
                    if (on && Build.VERSION.SDK_INT >= 33) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                    val default = if (kind == Reminder.SUPPLEMENT) LocalTime.of(8, 0) else LocalTime.of(20, 0)
                    update(if (on) Reminder(kind, id, name, default) else null, key)
                })
            }
        }
        // reminders of items deleted on the server
        reminders.values.filter { r -> r.kind != Reminder.STREAK && items.none { "${it.first}:${it.second}" == r.key } && items.isNotEmpty() }.forEach { r ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${r.name} (nicht mehr vorhanden)", Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant)
                TextButton(onClick = { update(null, r.key) }) { Text(tr("Entfernen")) }
            }
        }
        Hint(tr("Kommt nur, wenn das Supplement bzw. die Gewohnheit an dem Tag noch offen ist; \"Erledigt\" in der Benachrichtigung trägt es ein."))
    }
}

@Composable
private fun SyncSection() {
    val ctx = LocalContext.current
    val settings = remember { Settings(ctx) }
    var interval by remember { mutableStateOf(settings.intervalHours) }
    var cleanup by remember { mutableStateOf(settings.cleanupEnabled) }
    var notify by remember { mutableStateOf(settings.notifyErrors) }
    val notifPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }

    Section("Synchronisierung") {
        Text(tr("Intervall"))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Settings.INTERVALS.forEach { h ->
                FilterChip(
                    selected = interval == h,
                    onClick = {
                        interval = h
                        settings.intervalHours = h
                        if (settings.isConfigured) Scheduler.schedulePeriodic(ctx, update = true)
                    },
                    label = { Text("${h} h") },
                )
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(tr("Alte Exporte aufräumen"))
                Hint(tr("Löscht Gadgetbridge-Exporte von vor heute, sobald der Server sie (oder einen neueren) importiert hat. Der neueste Export und Gadgetbridge.zip bleiben immer."))
            }
            Switch(checked = cleanup, onCheckedChange = { cleanup = it; settings.cleanupEnabled = it })
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr("Bei Fehlern benachrichtigen"), modifier = Modifier.weight(1f))
            Switch(checked = notify, onCheckedChange = {
                notify = it
                settings.notifyErrors = it
                if (it && Build.VERSION.SDK_INT >= 33) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            })
        }
    }
}
