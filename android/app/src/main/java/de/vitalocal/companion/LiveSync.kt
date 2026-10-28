package de.vitalocal.companion

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.documentfile.provider.DocumentFile
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.time.Duration
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.zip.GZIPOutputStream

/** Up to [n] bytes from the start (InputStream.readNBytes needs Android 13). */
fun java.io.InputStream.readHead(n: Int): ByteArray {
    val buf = ByteArray(n)
    var got = 0
    while (got < n) {
        val r = read(buf, got, n - got)
        if (r < 0) break
        got += r
    }
    return buf.copyOf(got)
}

/** One sync at a time: the live cycle and the export sync never read the same files at once. */
object SyncLock {
    val mutex = Mutex()

    suspend fun <T> tryWith(waitMs: Long, block: suspend () -> T): T? {
        if (withTimeoutOrNull(waitMs) { mutex.lock() } == null) return null
        try {
            return block()
        } finally {
            mutex.unlock()
        }
    }
}

/** The exact alarm that starts each live cycle (day: every few minutes, night: every few hours). */
object LiveScheduler {
    const val ALARM = "de.vitalocal.companion.LIVE_SYNC"

    private fun intent(context: Context) = PendingIntent.getBroadcast(
        context, 7, Intent(context, LiveAlarmReceiver::class.java).setAction(ALARM),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun arm(context: Context, from: LocalDateTime = LocalDateTime.now()) {
        val settings = Settings(context)
        val am = context.getSystemService(AlarmManager::class.java)
        val s = LiveState.state.value
        if (!settings.liveEnabled || !settings.isConfigured || s.paused) {
            am.cancel(intent(context))
            LiveState.update { it.copy(nextAt = 0) }
            return
        }
        val budget = LiveSchedule.budgetFactor(LiveSchedule.usedLast24h(s.runtime, System.currentTimeMillis()))
        val next = LiveSchedule.next(from, settings.liveConfig, s.backoff * (if (budget == 2) 2 else 1))
        val delayMs = Duration.between(from, next).toMillis().coerceAtLeast(60_000L)
        val at = SystemClock.elapsedRealtime() + delayMs
        // exact and allowed in Doze (USE_EXACT_ALARM): also starts the foreground service from the background
        if (Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()) {
            am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent(context))
        } else {
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent(context))
        }
        LiveState.update { it.copy(nextAt = System.currentTimeMillis() + delayMs) }
    }

    /** At app start: arm again only if no alarm is pending (the app may have been stopped). */
    fun ensureArmed(context: Context) {
        val s = LiveState.state.value
        if (Settings(context).liveEnabled && s.nextAt < System.currentTimeMillis() - 60_000L) arm(context)
    }

    fun cancel(context: Context) {
        context.getSystemService(AlarmManager::class.java).cancel(intent(context))
        LiveState.update { it.copy(nextAt = 0) }
    }
}

class LiveAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Store.init(context)
        DayRepo.init(context)
        LiveState.init(context)
        val settings = Settings(context)
        if (!settings.liveEnabled || !settings.isConfigured) return
        LiveScheduler.arm(context) // first: a failed cycle must not end the loop
        if (!LiveState.state.value.paused) LiveSyncService.start(context, "live")
    }
}

/** Short foreground service (type dataSync) for one cycle; network and CPU stay available in Doze. */
class LiveSyncService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var job: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification(this), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        if (job?.isActive == true) return START_NOT_STICKY
        val trigger = intent?.getStringExtra(EXTRA_TRIGGER) ?: "live"
        job = scope.launch {
            Store.init(applicationContext)
            DayRepo.init(applicationContext)
            LiveState.init(applicationContext)
            val started = System.currentTimeMillis()
            val wake = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "vitalocal:live").apply { acquire(MAX_CYCLE_MS) }
            try {
                withTimeoutOrNull(if (trigger == TEST) MAX_TEST_MS else MAX_CYCLE_MS - 10_000L) {
                    LiveCycle(applicationContext, trigger).run()
                } ?: LiveCycle.timedOut(trigger, started)
            } finally {
                if (wake.isHeld) wake.release()
                LiveState.recordRuntime(started, System.currentTimeMillis() - started)
                ServiceCompat.stopForeground(this@LiveSyncService, ServiceCompat.STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    /** Android 15: the day's dataSync budget is used up. */
    override fun onTimeout(startId: Int, fgsType: Int) {
        job?.cancel()
        LiveState.update { it.copy(paused = true) }
        stopSelf()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        const val TEST = "test"
        private const val EXTRA_TRIGGER = "trigger"
        private const val CHANNEL = "live-sync"
        private const val NOTIFICATION_ID = 41
        private const val MAX_CYCLE_MS = 4 * 60_000L
        private const val MAX_TEST_MS = 6 * 60_000L

        fun createChannel(context: Context) {
            val ch = NotificationChannel(CHANNEL, tr("Live-Sync"), NotificationManager.IMPORTANCE_MIN)
                .apply { description = tr("Kurz sichtbar, während die Uhr-Daten übertragen werden"); setShowBadge(false) }
            context.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
        }

        private fun notification(context: Context): Notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(tr("Uhr-Daten werden übertragen"))
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setSilent(true)
            .setOngoing(true)
            .setContentIntent(
                PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE),
            )
            .build()

        fun start(context: Context, trigger: String) {
            try {
                ContextCompat.startForegroundService(context, Intent(context, LiveSyncService::class.java).putExtra(EXTRA_TRIGGER, trigger))
            } catch (e: IllegalStateException) { // ForegroundServiceStartNotAllowedException
                Store.log(Level.WARN, Source.GADGETBRIDGE, tr("Live-Sync durfte nicht starten: {0}", e.message ?: e.javaClass.simpleName))
            }
        }
    }
}

/** Gadgetbridge's answers while a cycle runs (implicit broadcasts: registered at runtime only). */
private class GbListener(private val context: Context) : BroadcastReceiver() {
    @Volatile var syncFinished = false
    @Volatile var exportSucceeded = false
    @Volatile var exportFailed = false

    override fun onReceive(c: Context, intent: Intent) {
        when (intent.action) {
            GbIntents.SYNC_FINISHED -> syncFinished = true
            GbIntents.DATABASE_EXPORT_SUCCESS -> exportSucceeded = true
            GbIntents.DATABASE_EXPORT_FAIL -> exportFailed = true
        }
    }

    fun register() {
        val filter = IntentFilter().apply {
            addAction(GbIntents.SYNC_FINISHED)
            addAction(GbIntents.DATABASE_EXPORT_SUCCESS)
            addAction(GbIntents.DATABASE_EXPORT_FAIL)
        }
        ContextCompat.registerReceiver(context, this, filter, ContextCompat.RECEIVER_EXPORTED)
    }

    fun unregister() = runCatching { context.unregisterReceiver(this) }
}

/**
 * One cycle: Gadgetbridge syncs the watch, exports its database into the chosen folder, and only
 * the database pages that changed since the last confirmed upload go to the server.
 */
class LiveCycle(private val context: Context, private val trigger: String) {
    private val settings = Settings(context)
    private val api = Api(settings.serverUrl, settings.token)
    private val steps = mutableListOf<LiveStep>()
    private val test = trigger == LiveSyncService.TEST
    private val indexFile = File(context.filesDir, "gbdb.index")
    private var finished = false
    private var gbAnswered = false

    private class Stop(val ok: Boolean, message: String) : Exception(message)

    suspend fun run() {
        val started = System.currentTimeMillis()
        val (ok, message) = try {
            true to cycle()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Stop) {
            e.ok to (e.message ?: "")
        } catch (e: Exception) {
            false to ((e as? ApiException)?.message ?: e.describe())
        }
        record(started, ok, message)
    }

    private suspend fun cycle(): String {
        val pkg = GbIntents.packageFor(context) ?: throw Stop(false, tr("Gadgetbridge ist nicht installiert"))
        val tree = settings.gadgetbridgeFolder ?: throw Stop(false, tr("Kein Gadgetbridge-Ordner gewählt (Einstellungen)"))
        if (!api.isReachable()) throw Stop(true, tr("Server nicht erreichbar, nächster Versuch beim nächsten Lauf"))
        return SyncLock.tryWith(10_000L) {
            val listener = GbListener(context).also { it.register() }
            try {
                syncAndUpload(pkg, tree, listener)
            } finally {
                listener.unregister()
            }
        } ?: throw Stop(true, tr("Eine andere Synchronisierung läuft gerade"))
    }

    private suspend fun syncAndUpload(pkg: String, tree: Uri, listener: GbListener): String {
        val s = LiveState.state.value
        // 1. the watch
        val wait = if (test) 120_000L else LiveSchedule.syncWait(s.syncDurations, s.watchSilent)
        var t = now()
        GbIntents.send(context, pkg, GbIntents.ACTIVITY_SYNC)
        waitFor(wait) { listener.syncFinished }
        finished = listener.syncFinished
        gbAnswered = finished
        step(tr("Uhr-Sync"), if (finished) tr("fertig") else tr("keine Rückmeldung"), now() - t, ok = finished || !test)
        if (finished) LiveState.update { it.copy(syncDurations = (it.syncDurations + (now() - t)).takeLast(20)) }
        if (!finished && s.finishWorks && now() - s.lastUploadAt < 30 * 60_000L && !test) {
            step(tr("Export"), "ausgelassen", 0)
            return files(pkg, tree) ?: tr("Keine neuen Uhr-Daten")
        }

        // 2. the database export
        val before = findDatabase(tree)
        val mark = before?.let { it.lastModified() to it.length() }
        t = now()
        GbIntents.send(context, pkg, GbIntents.TRIGGER_DATABASE_EXPORT)
        val exported = awaitExport(listener, tree, mark, if (test) 90_000L else 45_000L)
        when {
            listener.exportFailed -> {
                gbAnswered = true
                step(tr("Export"), tr("fehlgeschlagen"), now() - t, ok = false)
                throw Stop(false, tr("Gadgetbridge meldet: Datenbank-Export fehlgeschlagen"))
            }
            exported == null && before == null -> {
                step(tr("Export"), tr("keine Datei"), now() - t, ok = false)
                throw Stop(false, tr("Keine Datenbank-Exportdatei im Ordner (Gadgetbridge: Automatisierungen → Auto export, Datei im VitaLocal-Ordner)"))
            }
            exported == null -> {
                step(tr("Export"), tr("keine Rückmeldung"), now() - t, ok = !test)
                throw Stop(!test, tr("Gadgetbridge hat nicht exportiert (Intent API: \"Allow database export\")"))
            }
        }
        gbAnswered = true
        step(tr("Export"), if (listener.exportSucceeded) tr("fertig") else tr("Datei geändert"), now() - t)

        // 3. upload: only the changed pages
        t = now()
        val upload = upload(exported!!, pkg)
        step(tr("Upload"), upload.text, now() - t)
        val batchId = upload.batchId ?: return files(pkg, tree) ?: tr("Datenbank unverändert")

        // 4. the server imports it
        t = now()
        var batch = api.batch(batchId)
        val deadline = now() + if (test) 60_000L else 20_000L
        while (!batch.finished && now() < deadline) {
            delay(2_000L)
            batch = api.batch(batchId)
        }
        val imported = batch.status in BatchInfo.SUCCESS || batch.status == "superseded"
        step(tr("Import"), when {
            !batch.finished -> tr("läuft noch")
            imported -> tr("fertig")
            else -> tr("fehlgeschlagen")
        }, now() - t, ok = !batch.finished || imported)
        if (batch.finished && !imported) throw Stop(false, tr("Import #{0} fehlgeschlagen: {1}", batchId, batch.error ?: batch.status))
        val filesText = files(pkg, tree)
        DayRepo.refresh(context)
        return tr("Übertragen: {0}", upload.text) + (filesText?.let { " · ${it}" } ?: "")
    }

    /** 5. FIT files: workouts from the FIT export, the rest from a ZIP export when the server wants it. */
    private fun files(pkg: String, tree: Uri): String? {
        val t = now()
        return try {
            FileLane.cycle(context, api, pkg, tree, trigger)?.also { step(tr("Dateien"), it, now() - t) }
        } catch (e: Exception) {
            step(tr("Dateien"), (e as? ApiException)?.message ?: e.describe(), now() - t, ok = false)
            null
        }
    }

    private class Upload(val text: String, val batchId: Long?)

    /** Reads the export once: page hashes against the last confirmed upload, then delta or whole. */
    private fun upload(doc: DocumentFile, pkg: String): Upload {
        val resolver = context.contentResolver
        val fileSize = doc.length()
        val head = resolver.openInputStream(doc.uri)?.use { it.readHead(100) } ?: throw IOException(tr("Export nicht lesbar"))
        val header = DbDelta.header(head, fileSize) ?: throw Stop(true, "Export ist noch unvollständig, nächster Lauf")
        val base = DbDelta.readIndex(indexFile)
        val scan = resolver.openInputStream(doc.uri)?.use { DbDelta.scan(it, header.logicalSize, header.pageSize, base) }
            ?: throw IOException(tr("Export nicht lesbar"))
        if (base != null && scan.sha256 == base.sha256) return Upload(tr("unverändert"), null)

        val gz = File(context.cacheDir, "gbdb-upload.gz")
        try {
            var delta = DbDelta.deltaWorthIt(scan, base)
            repeat(2) {
                if (delta) {
                    GZIPOutputStream(gz.outputStream()).use { DbDelta.writeVldd(it, base!!, scan) }
                } else {
                    GZIPOutputStream(gz.outputStream()).use { out ->
                        resolver.openInputStream(doc.uri)?.use { copyExactly(it, out, header.logicalSize) }
                            ?: throw IOException(tr("Export nicht lesbar"))
                    }
                }
                val meta = JSONObject()
                    .put("sha256", scan.sha256).put("size", scan.size)
                    .put("encoding", if (delta) "vldd1" else "full")
                    .put("exported_at", iso(doc.lastModified()))
                    .put("gadgetbridge", JSONObject().put("package", pkg).put("version", GbIntents.versionOf(context, pkg)))
                    .put("client", JSONObject()
                        .put("app", "vitalocal-android").put("app_version", BuildConfig.VERSION_NAME)
                        .put("device", Build.MODEL).put("trigger", trigger)
                        .put("cycle", JSONObject().put("sync", if (finished) "finished" else "timeout")))
                if (delta) meta.put("base_sha256", base!!.sha256)
                try {
                    val r = api.uploadGbDb(meta, gz)
                    if (r.sha256 == scan.sha256) DbDelta.writeIndex(indexFile, scan.toIndex())
                    LiveState.update { it.copy(lastUploadAt = System.currentTimeMillis()) }
                    val what = if (delta) tr("{0} Seiten", scan.changed.size) else tr("ganze Datenbank")
                    return Upload("${what}, ${formatBytes(gz.length())}${if (r.created) "" else " (bekannt)"}", r.batchId)
                } catch (e: ApiException) {
                    // the server lacks the base or rejected the delta: send the whole file once
                    if (!delta || e.code !in setOf(409, 422)) throw e
                    indexFile.delete()
                    delta = false
                }
            }
            throw IOException(tr("Upload nicht möglich"))
        } finally {
            gz.delete()
        }
    }

    /** The export file: the remembered one, else the newest SQLite file in the folder. */
    private fun findDatabase(tree: Uri): DocumentFile? {
        settings.gbDatabaseDoc?.let { uri ->
            DocumentFile.fromSingleUri(context, uri)?.takeIf { it.exists() && it.isFile }?.let { return it }
        }
        val folder = DocumentFile.fromTreeUri(context, tree) ?: return null
        val found = folder.listFiles()
            .filter { it.isFile && !it.name.orEmpty().endsWith(".zip", ignoreCase = true) && it.length() >= 100 }
            .sortedByDescending { it.lastModified() }
            .firstOrNull { f ->
                runCatching { context.contentResolver.openInputStream(f.uri)?.use { DbDelta.header(it.readHead(100), f.length()) } }
                    .getOrNull() != null
            }
        if (found != null) settings.gbDatabaseDoc = found.uri
        return found
    }

    /** Export done: the broadcast came, or the file changed and its size held still twice. */
    private suspend fun awaitExport(listener: GbListener, tree: Uri, before: Pair<Long, Long>?, timeoutMs: Long): DocumentFile? {
        val deadline = now() + timeoutMs
        var lastSize = -1L
        var stable = 0
        while (now() < deadline) {
            if (listener.exportFailed) return null
            val f = findDatabase(tree)
            if (f != null) {
                val mark = f.lastModified() to f.length()
                if (listener.exportSucceeded) return f
                if (mark != before) {
                    stable = if (mark.second == lastSize) stable + 1 else 0
                    lastSize = mark.second
                    if (stable >= 2) return f
                }
            }
            delay(2_000L)
        }
        return null
    }

    private suspend fun waitFor(ms: Long, done: () -> Boolean) {
        val deadline = now() + ms
        while (!done() && now() < deadline) delay(500L)
    }

    private fun step(name: String, outcome: String, ms: Long, ok: Boolean = true) {
        steps += LiveStep(name, outcome, ms, ok)
    }

    private fun record(started: Long, ok: Boolean, message: String) {
        val prev = LiveState.state.value
        val cycle = CycleRecord(started, trigger, steps.toList(), ok, message)
        val gbSilent = if (gbAnswered) 0 else prev.gbSilent + 1
        val backoff = when {
            gbAnswered -> 1
            gbSilent >= 3 -> (prev.backoff * 2).coerceAtMost(8)
            else -> prev.backoff
        }
        LiveState.update {
            it.copy(
                last = cycle,
                lastSuccessAt = if (ok) started else it.lastSuccessAt,
                finishWorks = it.finishWorks || finished,
                watchSilent = if (finished) 0 else it.watchSilent + 1,
                gbSilent = gbSilent,
                backoff = backoff,
                reportedError = if (ok) null else it.reportedError,
            )
        }
        // the log and notifications only on changes, not every few minutes
        when {
            ok && prev.reportedError != null -> Store.log(Level.INFO, Source.GADGETBRIDGE, tr("Live-Sync läuft wieder: {0}", message))
            ok && prev.lastSuccessAt == 0L -> Store.log(Level.INFO, Source.GADGETBRIDGE, tr("Live-Sync läuft: {0}", message))
            !ok && prev.reportedError != message -> {
                Store.log(Level.ERROR, Source.GADGETBRIDGE, tr("Live-Sync: {0}", message))
                if (!test) Notifier.error(context, tr("Live-Sync"), message)
                LiveState.update { it.copy(reportedError = message) }
            }
        }
        if (gbSilent == 3 && !test) {
            val text = tr("Gadgetbridge antwortet nicht: läuft es, und sind in Gadgetbridge (Entwickleroptionen → Intent API) ") +
                tr("Sync und Datenbank-Export erlaubt? Der Takt wird bis dahin verlangsamt.")
            Store.log(Level.WARN, Source.GADGETBRIDGE, text)
            Notifier.error(context, tr("Live-Sync"), text)
        }
    }

    companion object {
        private fun now() = System.currentTimeMillis()

        private fun iso(ms: Long): String =
            DateTimeFormatter.ISO_OFFSET_DATE_TIME.format(Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()))

        private fun copyExactly(input: java.io.InputStream, out: java.io.OutputStream, size: Long) {
            val buf = ByteArray(1 shl 16)
            var left = size
            while (left > 0) {
                val n = input.read(buf, 0, minOf(buf.size.toLong(), left).toInt())
                if (n < 0) throw IOException(tr("Export endet vorzeitig"))
                out.write(buf, 0, n)
                left -= n
            }
        }

        /** The whole cycle ran out of time (watchdog of the service). */
        fun timedOut(trigger: String, started: Long) {
            LiveState.update {
                it.copy(last = CycleRecord(started, trigger, emptyList(), false, tr("Zeitlimit des Laufs erreicht")))
            }
        }
    }
}
