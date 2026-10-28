package de.vitalocal.companion

import android.content.Context
import android.net.Uri
import android.os.Build
import androidx.documentfile.provider.DocumentFile
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.time.ZoneId

/** Result of handing one file to the server. */
private data class Outcome(val batch: BatchInfo?)

/**
 * One sync pass: Gadgetbridge folder and files shared into the app.
 * Blocking I/O; run from a worker (Dispatchers.IO).
 */
class Syncer(private val context: Context, private val trigger: String) {
    private val settings = Settings(context)
    private val api = Api(settings.serverUrl, settings.token)
    private val manual = trigger != "auto"

    /** False if the server could not be reached (nothing was attempted). */
    suspend fun run(sources: Set<Source> = Source.entries.toSet()): Boolean {
        if (!settings.isConfigured) {
            if (manual) Store.log(Level.WARN, null, tr("Server ist noch nicht eingerichtet"))
            return false
        }
        val reachable = api.isReachable()
        Store.serverChecked(reachable)
        if (!reachable) {
            if (manual) Store.log(Level.WARN, null, tr("Server nicht erreichbar (WireGuard aktiv?)"))
            return false
        }
        // the live cycle reads the same folder: one after the other
        val ran = SyncLock.tryWith(if (manual) 60_000L else 5 * 60_000L) {
            if (Source.GADGETBRIDGE in sources && settings.dataSource == "gadgetbridge") guarded(Source.GADGETBRIDGE) { gadgetbridge() }
            if (Source.HEALTH_CONNECT in sources && settings.dataSource == "health_connect") guarded(Source.HEALTH_CONNECT) { healthConnect() }
            if (Source.SHARED in sources) guarded(Source.SHARED) { shared() }
        }
        if (ran == null && manual) Store.log(Level.INFO, null, tr("Live-Sync läuft gerade, bitte gleich noch einmal"))
        return true
    }

    private suspend fun guarded(source: Source, block: suspend () -> Unit) {
        try {
            block()
        } catch (e: Exception) {
            val msg = (e as? ApiException)?.message ?: e.describe()
            fail(source, msg)
        }
    }

    // --- Health Connect -----------------------------------------------------------------------

    private suspend fun healthConnect() {
        val src = Source.HEALTH_CONNECT
        if (!HealthConnect.available(context)) return fail(src, tr("Health Connect ist auf diesem Gerät nicht verfügbar"))
        val granted = HealthConnect.granted(context)
        if (granted.intersect(HealthConnect.PERMISSIONS).isEmpty()) {
            return fail(src, tr("Kein Zugriff auf Health Connect, bitte in den Einstellungen erlauben"))
        }
        // without the background permission Health Connect answers only while the app is in front
        if (trigger == "auto" && HealthConnect.BACKGROUND !in granted) return
        Store.updateSource(src) { it.copy(lastCheck = System.currentTimeMillis()) }
        val meta = JSONObject().put("app", "vitalocal-android").put("version", BuildConfig.VERSION_NAME)
            .put("device", android.os.Build.MODEL)
        val id = HealthConnect.sync(context, api, meta)
        Store.updateSource(src) { it.copy(lastUpload = System.currentTimeMillis(), batchId = id, status = "uploaded", error = null) }
        if (manual) Store.log(Level.INFO, src, tr("Health Connect übertragen (Import {0})", id))
    }

    // --- Gadgetbridge -------------------------------------------------------------------------

    private suspend fun gadgetbridge() {
        val src = Source.GADGETBRIDGE
        val tree = settings.gadgetbridgeFolder ?: return
        val folder = DocumentFile.fromTreeUri(context, tree)
        if (folder == null || !folder.canRead()) {
            return fail(src, tr("Kein Zugriff auf den Gadgetbridge-Ordner, bitte in den Einstellungen neu wählen"))
        }
        val files = folder.listFiles()
            .filter { it.isFile && Exports.isGadgetbridgeExport(it.name.orEmpty()) }
            .map { ExportFile(it.name.orEmpty(), it.length(), it.lastModified(), it.uri.toString()) }
        Store.updateSource(src) { it.copy(lastCheck = System.currentTimeMillis()) }
        val newest = Exports.newest(files)
            ?: return fail(src, tr("Keine Gadgetbridge-Exporte im Ordner (Auto export zip eingerichtet?)"))
        if (!Exports.isSettled(newest, System.currentTimeMillis())) {
            if (manual) Store.log(Level.INFO, src, tr("{0} wird gerade geschrieben, nächster Lauf", newest.name))
            return
        }
        // a server that takes file sets gets only the files it lacks (the database comes live)
        if (runCatching { "files_v1" in api.ingestStatus().capabilities }.getOrDefault(false)) {
            val key = "gbm:${newest.ref}"
            if (Store.indexed(key, newest.size, newest.modified) != null) return
            val doc = DocumentFile.fromSingleUri(context, Uri.parse(newest.ref)) ?: return
            val text = FileLane.uploadZip(context, api, doc, trigger)
            Store.putIndex(key, IndexEntry(newest.size, newest.modified, "manifest"))
            Store.log(Level.INFO, src, "${newest.name}: ${text}")
            Store.updateSource(src) { it.copy(lastFile = newest.name, lastUpload = System.currentTimeMillis(), status = "completed", error = null) }
            return
        }
        val outcome = deliver(src, newest, "gb:${newest.ref}") { context.contentResolver.openInputStream(Uri.parse(it.ref)) }
        if (settings.cleanupEnabled) cleanupGadgetbridge(files, outcome.batch?.succeeded == true)
    }

    private fun cleanupGadgetbridge(files: List<ExportFile>, newestImported: Boolean) {
        val src = Source.GADGETBRIDGE
        for (f in Exports.cleanupCandidates(files, System.currentTimeMillis(), ZoneId.systemDefault())) {
            val key = "gb:${f.ref}"
            val sha = Store.indexed(key, f.size, f.modified) ?: run {
                val input = context.contentResolver.openInputStream(Uri.parse(f.ref)) ?: return@run null
                Exports.hash(input).also { Store.putIndex(key, IndexEntry(f.size, f.modified, it)) }
            } ?: continue
            val own = api.artifactBatches(sha)
            if (!Exports.safeToDelete(own, newestImported)) continue
            val reason = if (own.orEmpty().any { it.succeeded }) tr("liegt auf dem Server") else tr("durch neueren Export ersetzt")
            val doc = DocumentFile.fromSingleUri(context, Uri.parse(f.ref))
            if (doc != null && doc.delete()) {
                Store.log(Level.INFO, src, tr("{0} gelöscht ({1})", f.name, reason))
            } else {
                Store.log(Level.WARN, src, tr("{0} konnte nicht gelöscht werden", f.name))
            }
        }
    }

    // --- Files shared into the app ------------------------------------------------------------

    private suspend fun shared() {
        val src = Source.SHARED
        val pending = Inbox.files(context)
        if (pending.isEmpty()) return
        for (file in pending) {
            val name = Inbox.displayName(file)
            if (isLabReport(file)) {
                labReport(file, name)
                continue
            }
            val export = ExportFile(name, file.length(), file.lastModified(), file.path)
            val outcome = deliver(src, export, null) { file.inputStream() }
            // Server has the bytes (whatever the import result), so the local copy is done.
            if (outcome.batch != null) file.delete()
        }
    }

    private fun isLabReport(file: File): Boolean {
        val head = ByteArray(12)
        val n = file.inputStream().use { it.read(head) }
        return n > 0 && Exports.isLabReport(head.copyOf(n))
    }

    /** A shared PDF or photo of a lab report: upload it; the values are confirmed in the WebUI. */
    private fun labReport(file: File, name: String) {
        val src = Source.SHARED
        try {
            api.uploadLabReport(file, name, clientMeta(name))
            Store.log(Level.INFO, src, tr("Befund {0} hochgeladen; in der WebUI unter Gesundheit prüfen und bestätigen", name))
            file.delete()
        } catch (e: ApiException) {
            val msg = when (e.code) {
                409 -> tr("Befund {0}: auf dem Server ist kein KI-Modell eingerichtet (WebUI → Einstellungen)", name)
                413, 415 -> tr("Befund {0} wurde nicht angenommen ({1})", name, e.message)
                else -> throw e
            }
            Store.log(Level.ERROR, src, msg)
            Notifier.error(context, tr("Befund nicht hochgeladen"), msg)
            if (e.code != 409) file.delete() // a 409 can succeed once a model is set up
        }
    }

    // --- Shared delivery logic ----------------------------------------------------------------

    /**
     * Uploads [file] unless the server already has these bytes, then waits for the import.
     * [indexKey] lets unchanged files skip re-reading.
     */
    private suspend fun deliver(
        source: Source,
        file: ExportFile,
        indexKey: String?,
        open: suspend (ExportFile) -> java.io.InputStream?,
    ): Outcome {
        val known = indexKey?.let { Store.indexed(it, file.size, file.modified) }
        if (known != null) {
            api.artifactBatches(known)?.firstOrNull()?.let { batch ->
                if (manual) Store.log(Level.INFO, source, tr("{0} unverändert, bereits importiert", file.name))
                return Outcome(finish(source, file.name, batch, uploaded = false))
            }
        }
        val tmp = File.createTempFile("upload-", ".bin", context.cacheDir)
        try {
            val input = open(file) ?: throw IOException(tr("{0} lässt sich nicht öffnen", file.name))
            val sha = Exports.copyHashing(input, tmp)
            indexKey?.let { Store.putIndex(it, IndexEntry(file.size, file.modified, sha)) }
            api.artifactBatches(sha)?.firstOrNull()?.let { batch ->
                return Outcome(finish(source, file.name, batch, uploaded = false))
            }
            val result = api.upload(tmp, file.name, clientMeta(file.name))
            Store.log(Level.INFO, source, tr("{0} hochgeladen ({1}), Import #{2}", file.name, formatBytes(tmp.length()), result.batchId))
            Store.updateSource(source) { it.copy(lastUpload = System.currentTimeMillis()) }
            return Outcome(finish(source, file.name, api.batch(result.batchId), uploaded = true))
        } finally {
            tmp.delete()
        }
    }

    /** Waits for a running import (bounded) and records the result. */
    private suspend fun finish(source: Source, name: String, start: BatchInfo, uploaded: Boolean): BatchInfo {
        var batch = start
        val deadline = System.currentTimeMillis() + WAIT_MS
        while (!batch.finished && System.currentTimeMillis() < deadline) {
            delay(POLL_MS)
            batch = api.batch(batch.id)
        }
        Store.updateSource(source) {
            it.copy(lastFile = name, batchId = batch.id, status = batch.status, error = batch.error)
        }
        when {
            !batch.finished -> Store.log(Level.INFO, source, tr("Import #{0} läuft noch", batch.id))
            batch.status == "completed" -> if (uploaded) Store.log(Level.INFO, source, tr("Import #{0} abgeschlossen", batch.id))
            batch.status == "superseded" -> Store.log(Level.INFO, source, tr("Import #{0} durch neueren ersetzt", batch.id))
            batch.status == "completed_with_errors" ->
                Store.log(Level.WARN, source, tr("Import #{0} mit Fehlern abgeschlossen", batch.id))
            else -> {
                Store.log(Level.ERROR, source, tr("Import #{0} fehlgeschlagen: {1}", batch.id, batch.error ?: batch.status))
                Notifier.error(context, tr("{0}: Import fehlgeschlagen", source.title), batch.error ?: batch.status)
            }
        }
        return batch
    }

    private fun fail(source: Source, message: String) {
        val previous = Store.state.value.sources[source]?.error
        Store.updateSource(source) { it.copy(lastCheck = System.currentTimeMillis(), error = message, status = "error") }
        // Log a persisting problem once, not on every periodic run.
        if (manual || previous != message) {
            Store.log(Level.ERROR, source, message)
            Notifier.error(context, source.title, message)
        }
    }

    private fun clientMeta(filename: String) = JSONObject()
        .put("app", "vitalocal-android")
        .put("app_version", BuildConfig.VERSION_NAME)
        .put("device", Build.MODEL)
        .put("trigger", trigger)
        .put("file", filename)

    companion object {
        private const val WAIT_MS = 4 * 60_000L
        private const val POLL_MS = 3_000L
    }
}

fun formatBytes(n: Long): String = when {
    n >= 1 shl 20 -> "%.1f MB".format(n / 1048576.0)
    n >= 1 shl 10 -> "%.0f KB".format(n / 1024.0)
    else -> "${n} B"
}
