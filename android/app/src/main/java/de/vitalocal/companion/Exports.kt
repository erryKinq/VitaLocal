package de.vitalocal.companion

import java.io.File
import java.io.InputStream
import java.security.MessageDigest
import java.time.Instant
import java.time.ZoneId

/** A file in an export folder (SAF document). */
data class ExportFile(val name: String, val size: Long, val modified: Long, val ref: String)

/** Pure rules for picking and cleaning up exports; kept free of Android types for unit tests. */
object Exports {
    /** Gadgetbridge's automatic ZIP export overwrites this one file; deleting it breaks the next export. */
    const val GB_AUTO_EXPORT_NAME = "Gadgetbridge.zip"

    /** A file still being written must not be uploaded half-finished. */
    const val SETTLE_MS = 60_000L

    fun isGadgetbridgeExport(name: String): Boolean =
        name.startsWith("gadgetbridge", ignoreCase = true) && name.endsWith(".zip", ignoreCase = true)

    /** A lab report to be read by the server's model: PDF or image, recognised by its first bytes. */
    fun isLabReport(head: ByteArray): Boolean {
        fun starts(vararg b: Int) = head.size >= b.size && b.indices.all { head[it] == b[it].toByte() }
        return starts(0x25, 0x50, 0x44, 0x46) || // %PDF
            starts(0x89, 0x50, 0x4E, 0x47) || // PNG
            starts(0xFF, 0xD8) || // JPEG
            starts(0x49, 0x49, 0x2A, 0x00) || starts(0x4D, 0x4D, 0x00, 0x2A) || // TIFF
            (starts(0x52, 0x49, 0x46, 0x46) && head.size >= 12 && String(head, 8, 4, Charsets.US_ASCII) == "WEBP")
    }

    fun newest(files: List<ExportFile>): ExportFile? = files.maxByOrNull { it.modified }

    fun isSettled(file: ExportFile, now: Long): Boolean = now - file.modified >= SETTLE_MS

    /**
     * Exports that may be deleted once the server has them: from before today, never the newest
     * file and never the file Gadgetbridge's auto export writes to.
     */
    fun cleanupCandidates(files: List<ExportFile>, now: Long, zone: ZoneId): List<ExportFile> {
        val newest = newest(files) ?: return emptyList()
        val startOfToday = Instant.ofEpochMilli(now).atZone(zone).toLocalDate().atStartOfDay(zone).toInstant().toEpochMilli()
        return files.filter {
            it.ref != newest.ref && !it.name.equals(GB_AUTO_EXPORT_NAME, ignoreCase = true) && it.modified < startOfToday
        }
    }

    /**
     * Delete when the server imported this exact file, or when a newer export was imported
     * (exports are full snapshots). Unknown files and failed imports stay.
     */
    fun safeToDelete(ownBatches: List<BatchInfo>?, newestImported: Boolean): Boolean =
        newestImported || ownBatches.orEmpty().any { it.succeeded }

    /** Copies [input] to [dest] and returns the SHA-256 of the bytes written. */
    fun copyHashing(input: InputStream, dest: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        input.use { src ->
            dest.outputStream().use { out ->
                val buf = ByteArray(1 shl 16)
                while (true) {
                    val n = src.read(buf)
                    if (n < 0) break
                    digest.update(buf, 0, n)
                    out.write(buf, 0, n)
                }
            }
        }
        return digest.digest().toHex()
    }

    fun hash(input: InputStream): String {
        val digest = MessageDigest.getInstance("SHA-256")
        input.use { src ->
            val buf = ByteArray(1 shl 16)
            while (true) {
                val n = src.read(buf)
                if (n < 0) break
                digest.update(buf, 0, n)
            }
        }
        return digest.digest().toHex()
    }

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
}
