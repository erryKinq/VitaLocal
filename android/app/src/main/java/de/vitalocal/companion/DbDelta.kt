package de.vitalocal.companion

import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest

/**
 * Gadgetbridge's database export is a plain copy of its SQLite file, which changes page by page.
 * The app keeps an 8-byte hash per page of the database the server last confirmed and sends only
 * the pages that changed (VLDD, see the server's ingest/dbdelta.py). Pure code, unit-tested.
 */
object DbDelta {
    private val SQLITE_MAGIC = "SQLite format 3\u0000".toByteArray(Charsets.US_ASCII)
    private const val INDEX_MAGIC = "VLBI"

    /** Send the whole file instead when more than this changed (also caps memory). */
    const val MAX_DELTA_BYTES = 8L shl 20

    /** Page size and the size the header announces; null if this is not a (complete) SQLite file. */
    data class Header(val pageSize: Int, val logicalSize: Long)

    /**
     * Gadgetbridge opens the export file with mode "w", which some storage providers do not
     * truncate: bytes of an older, longer file may follow. The header says how long the database
     * is; a file shorter than that is still being written.
     */
    fun header(head: ByteArray, fileSize: Long): Header? {
        if (head.size < 100 || !head.copyOfRange(0, 16).contentEquals(SQLITE_MAGIC)) return null
        val raw = u16(head, 16)
        val pageSize = if (raw == 1) 65536 else raw
        if (pageSize < 512 || pageSize and (pageSize - 1) != 0) return null
        val changeCounter = u32(head, 24)
        val pages = u32(head, 28)
        val validFor = u32(head, 92)
        val logical = if (pages > 0 && validFor == changeCounter) pages * pageSize else fileSize / pageSize * pageSize
        return if (logical in 1..fileSize) Header(pageSize, logical) else null
    }

    /** Hashes of the database the server last confirmed. */
    class Index(val blockSize: Int, val size: Long, val sha256: String, val hashes: LongArray)

    /** One read of the new database: its hash, block hashes and the changed blocks (if few). */
    class Scan(
        val sha256: String,
        val size: Long,
        val blockSize: Int,
        val hashes: LongArray,
        val changed: List<Int>,
        val blocks: Map<Int, ByteArray>,
        val changedBytes: Long,
    ) {
        fun toIndex() = Index(blockSize, size, sha256, hashes)
    }

    /**
     * Reads exactly [size] bytes of [input] in blocks of [blockSize]. With a [base] of the same
     * block size the changed blocks (or those past its end) are listed and, up to
     * [MAX_DELTA_BYTES], kept for the delta.
     */
    fun scan(input: InputStream, size: Long, blockSize: Int, base: Index?): Scan {
        val n = ((size + blockSize - 1) / blockSize).toInt()
        val hashes = LongArray(n)
        val full = MessageDigest.getInstance("SHA-256")
        val changed = ArrayList<Int>()
        val blocks = HashMap<Int, ByteArray>()
        var changedBytes = 0L
        val comparable = base != null && base.blockSize == blockSize
        val data = DataInputStream(input)
        for (i in 0 until n) {
            val len = minOf(blockSize.toLong(), size - i.toLong() * blockSize).toInt()
            val block = ByteArray(len)
            data.readFully(block)
            full.update(block)
            hashes[i] = blockHash(block)
            if (comparable && (i >= base!!.hashes.size || base.hashes[i] != hashes[i] || isPartial(base, i, blockSize))) {
                changed.add(i)
                changedBytes += len
                if (changedBytes <= MAX_DELTA_BYTES) blocks[i] = block
            }
        }
        return Scan(full.digest().toHex(), size, blockSize, hashes, changed, if (changedBytes <= MAX_DELTA_BYTES) blocks else emptyMap(), changedBytes)
    }

    /** The last block of a base whose size is not a multiple of the block size is never reused. */
    private fun isPartial(base: Index, i: Int, blockSize: Int) =
        i == base.hashes.size - 1 && base.size % blockSize != 0L

    /** A delta pays off: there is a base and not too much changed. */
    fun deltaWorthIt(scan: Scan, base: Index?): Boolean =
        base != null && base.blockSize == scan.blockSize && scan.changedBytes <= minOf(scan.size / 2, MAX_DELTA_BYTES)

    /** VLDD v1: header, block indices, then the changed blocks in order (all big-endian). */
    fun writeVldd(out: OutputStream, base: Index, scan: Scan) {
        val o = DataOutputStream(out)
        o.write("VLDD".toByteArray(Charsets.US_ASCII))
        o.writeByte(1)
        o.write(ByteArray(3))
        o.writeInt(scan.blockSize)
        o.writeLong(base.size)
        o.writeLong(scan.size)
        o.writeInt(scan.changed.size)
        scan.changed.forEach { o.writeInt(it) }
        scan.changed.forEach { o.write(scan.blocks.getValue(it)) }
        o.flush()
    }

    fun vlddBytes(base: Index, scan: Scan): ByteArray = ByteArrayOutputStream().also { writeVldd(it, base, scan) }.toByteArray()

    fun writeIndex(file: File, index: Index) {
        val tmp = File(file.parentFile, "${file.name}.tmp")
        DataOutputStream(tmp.outputStream().buffered()).use { o ->
            o.write(INDEX_MAGIC.toByteArray(Charsets.US_ASCII))
            o.writeByte(1)
            o.write(ByteArray(3))
            o.writeInt(index.blockSize)
            o.writeLong(index.size)
            o.write(index.sha256.hexToBytes())
            o.writeInt(index.hashes.size)
            index.hashes.forEach { o.writeLong(it) }
        }
        if (!tmp.renameTo(file)) {
            file.delete()
            tmp.renameTo(file)
        }
    }

    fun readIndex(file: File): Index? = runCatching {
        DataInputStream(file.inputStream().buffered()).use { i ->
            val magic = ByteArray(4).also { i.readFully(it) }
            if (String(magic, Charsets.US_ASCII) != INDEX_MAGIC || i.readByte().toInt() != 1) return null
            i.readFully(ByteArray(3))
            val bs = i.readInt()
            val size = i.readLong()
            val sha = ByteArray(32).also { i.readFully(it) }.toHex()
            val n = i.readInt()
            Index(bs, size, sha, LongArray(n) { i.readLong() })
        }
    }.getOrNull()

    private fun blockHash(block: ByteArray): Long {
        val d = MessageDigest.getInstance("SHA-256").digest(block)
        var v = 0L
        for (k in 0 until 8) v = (v shl 8) or (d[k].toLong() and 0xFF)
        return v
    }

    private fun u16(b: ByteArray, off: Int) = ((b[off].toInt() and 0xFF) shl 8) or (b[off + 1].toInt() and 0xFF)

    private fun u32(b: ByteArray, off: Int): Long =
        ((b[off].toLong() and 0xFF) shl 24) or ((b[off + 1].toLong() and 0xFF) shl 16) or
            ((b[off + 2].toLong() and 0xFF) shl 8) or (b[off + 3].toLong() and 0xFF)

    fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

    private fun String.hexToBytes(): ByteArray = ByteArray(length / 2) { substring(2 * it, 2 * it + 2).toInt(16).toByte() }
}
