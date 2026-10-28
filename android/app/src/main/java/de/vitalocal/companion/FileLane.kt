package de.vitalocal.companion

import android.content.Context
import android.net.Uri
import android.os.ParcelFileDescriptor
import androidx.documentfile.provider.DocumentFile
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.zip.ZipFile

/**
 * FIT files next to the live database: workouts from Gadgetbridge's FIT export (one original file
 * per workout, in the Gadgetbridge folder or a subfolder) every cycle, everything else (monitoring,
 * sleep, skin temperature) from a ZIP export the server asks for. The server learns every file by
 * path and SHA-256; only bytes it lacks are sent.
 */
object FileLane {
    private const val MAX_UPLOAD = 48L shl 20
    private const val ZIP_EVERY_MS = 6 * 3600_000L
    private const val ZIP_ASK_GAP_MS = 30 * 60_000L
    private const val ZIP_GIVE_UP_MS = 45 * 60_000L

    /** path|size|crc-or-mtime → (sha256, sent) */
    private class Index(private val file: File) {
        private val map = LinkedHashMap<String, Pair<String, Boolean>>()

        init {
            runCatching {
                file.readLines().forEach { l ->
                    val p = l.split('\t')
                    if (p.size == 3) map[p[0]] = p[1] to (p[2] == "1")
                }
            }
        }

        fun get(key: String) = map[key]
        fun put(key: String, sha: String, sent: Boolean) { map[key] = sha to sent }

        fun save() {
            val tmp = File(file.parentFile, "${file.name}.tmp")
            tmp.writeText(map.entries.joinToString("\n") { "${it.key}\t${it.value.first}\t${if (it.value.second) 1 else 0}" })
            if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
        }
    }

    class Entry(val path: String, val sha256: String, val size: Long, val key: String, val read: () -> ByteArray)

    /** One live cycle's file work; returns a short line for the step list, or null if nothing to do. */
    fun cycle(context: Context, api: Api, pkg: String, tree: Uri, trigger: String): String? {
        val status = runCatching { api.ingestStatus() }.getOrNull() ?: return null
        if ("files_v1" !in status.capabilities) return null
        val parts = mutableListOf<String>()
        fitExport(context, api, tree, trigger)?.let { parts += it }

        val live = LiveState.state.value
        val now = System.currentTimeMillis()
        val zip = newestZip(context, tree)
        if (live.zipRequestedAt > 0 && zip != null && zip.lastModified() > live.zipRequestedAt &&
            Exports.isSettled(ExportFile(zip.name.orEmpty(), zip.length(), zip.lastModified(), zip.uri.toString()), now)
        ) {
            parts += "ZIP: " + uploadZip(context, api, zip, trigger)
            LiveState.update { it.copy(zipRequestedAt = 0, lastZipAt = now) }
        } else if (live.zipRequestedAt > 0 && now - live.zipRequestedAt > ZIP_GIVE_UP_MS) {
            LiveState.update { it.copy(zipRequestedAt = 0) }
            Store.log(Level.WARN, Source.GADGETBRIDGE, tr("ZIP-Export kam nicht (Gadgetbridge Intent API: \"Allow zip export\"?)"))
        } else if (live.zipRequestedAt == 0L) {
            val wanted = runCatching { api.ingestStatus() }.getOrNull()?.reasons ?: status.reasons
            val stale = now - live.lastZipAt > ZIP_EVERY_MS && !LiveSchedule.inNight(java.time.LocalTime.now(), Settings(context).liveConfig)
            if ((wanted.isNotEmpty() || stale) && now - live.lastZipRequestAt > ZIP_ASK_GAP_MS) {
                GbIntents.send(context, pkg, GbIntents.TRIGGER_ZIP_EXPORT)
                LiveState.update { it.copy(zipRequestedAt = now, lastZipRequestAt = now) }
                parts += tr("ZIP angefordert ({0})", if (wanted.isNotEmpty()) wanted.joinToString() else "6 h")
            }
        }
        return parts.takeIf { it.isNotEmpty() }?.joinToString(" · ")
    }

    /** Workout files the FIT export wrote since the last cycle. */
    private fun fitExport(context: Context, api: Api, tree: Uri, trigger: String): String? {
        val index = Index(File(context.filesDir, "fit_index.tsv"))
        val root = DocumentFile.fromTreeUri(context, tree) ?: return null
        val fits = buildList {
            root.listFiles().forEach { f ->
                if (f.isDirectory) f.listFiles().forEach { if (it.isFile && it.name.orEmpty().endsWith(".fit", true)) add(it) }
                else if (f.isFile && f.name.orEmpty().endsWith(".fit", true)) add(f)
            }
        }
        val entries = fits.mapNotNull { f ->
            val key = "${f.uri}|${f.length()}|${f.lastModified()}"
            val known = index.get(key)
            if (known?.second == true) return@mapNotNull null
            val read = { context.contentResolver.openInputStream(f.uri)?.use { it.readBytes() } ?: throw IOException("${f.name} nicht lesbar") }
            val sha = known?.first ?: sha256(read())
            index.put(key, sha, false)
            Entry("fit_export/${f.name}", sha, f.length(), key, read)
        }.distinctBy { it.path }
        index.save()
        if (entries.isEmpty()) return null
        val r = send(api, entries, "fit_export", null, trigger)
        entries.forEach { index.put(it.key, it.sha256, true) }
        index.save()
        return tr("{0} Trainingsdatei(en) {1}", entries.size, r)
    }

    private fun newestZip(context: Context, tree: Uri): DocumentFile? =
        DocumentFile.fromTreeUri(context, tree)?.listFiles()
            ?.filter { it.isFile && Exports.isGadgetbridgeExport(it.name.orEmpty()) }
            ?.maxByOrNull { it.lastModified() }

    /** The files of a ZIP export (without the database, which comes live) by manifest. */
    fun uploadZip(context: Context, api: Api, zip: DocumentFile, trigger: String): String {
        val index = Index(File(context.filesDir, "zip_index.tsv"))
        val pfd = context.contentResolver.openFileDescriptor(zip.uri, "r") ?: throw IOException(tr("ZIP nicht lesbar"))
        val copy = File(context.cacheDir, "export.zip")
        try {
            // ZipFile needs a file: the descriptor's /proc path, else a copy
            val zf = runCatching { ZipFile(File("/proc/self/fd/${pfd.fd}")) }.getOrElse {
                ParcelFileDescriptor.AutoCloseInputStream(context.contentResolver.openFileDescriptor(zip.uri, "r"))
                    .use { input -> copy.outputStream().use { input.copyTo(it) } }
                ZipFile(copy)
            }
            zf.use { z ->
                val entries = z.entries().asSequence()
                    .filter { !it.isDirectory && it.name != "database/Gadgetbridge" }
                    .map { e ->
                        val key = "${e.name}|${e.size}|${e.crc}"
                        val read = { z.getInputStream(e).use { it.readBytes() } }
                        val sha = index.get(key)?.first ?: sha256(read()).also { index.put(key, it, false) }
                        Entry(e.name, sha, e.size, key, read)
                    }.toList()
                index.save()
                val exported = java.time.Instant.ofEpochMilli(zip.lastModified()).toString()
                return send(api, entries, "zip_export", exported, trigger)
            }
        } finally {
            pfd.close()
            copy.delete()
        }
    }

    /** Manifest of [entries]; sends the bytes of those the server lacks. */
    private fun send(api: Api, entries: List<Entry>, origin: String, exportedAt: String?, trigger: String): String {
        val missing = entries.map { it.sha256 }.distinct().chunked(5000).flatMap { api.missingArtifacts(it) }.toSet()
        val blobs = entries.filter { it.sha256 in missing }.distinctBy { it.sha256 }
        if (blobs.sumOf { it.size } > MAX_UPLOAD) throw IOException(tr("Zu viele neue Dateien auf einmal ({0})", blobs.size))
        val manifest = JSONObject().put("v", 1).put("origin", origin).put("exported_at", exportedAt ?: JSONObject.NULL)
            .put("entries", JSONArray().apply {
                entries.forEach { put(JSONObject().put("path", it.path).put("sha256", it.sha256).put("size", it.size)) }
            })
        val client = JSONObject().put("app", "vitalocal-android").put("app_version", BuildConfig.VERSION_NAME).put("trigger", trigger)
        val r = api.uploadFiles(manifest, client, blobs.map { it.sha256 to it.read() })
        // wait briefly, so the next status already counts these files
        var batch = api.batch(r.batchId)
        val deadline = System.currentTimeMillis() + 20_000L
        while (!batch.finished && System.currentTimeMillis() < deadline) {
            Thread.sleep(2_000L)
            batch = api.batch(r.batchId)
        }
        return tr("{0} neu, Import #{1}{2}", blobs.size, r.batchId, if (r.created) "" else " (bekannt)")
    }

    private fun sha256(b: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) }
}
