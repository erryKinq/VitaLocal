package de.vitalocal.companion

import de.vitalocal.companion.DbDelta.toHex
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream
import java.security.MessageDigest
import java.time.Duration
import java.time.LocalDateTime
import java.time.LocalTime

class LiveSyncTest {
    @get:Rule val tmp = TemporaryFolder()

    private val bs = 512
    private fun block(c: Char) = ByteArray(bs) { c.code.toByte() }
    private val base = block('A') + block('B') + block('C')
    private val new = block('A') + block('b') + block('C') + block('D')

    private fun index(data: ByteArray) = DbDelta.scan(ByteArrayInputStream(data), data.size.toLong(), bs, null).toIndex()

    @Test
    fun `delta matches the server's golden vector`() {
        val scan = DbDelta.scan(ByteArrayInputStream(new), new.size.toLong(), bs, index(base))
        assertEquals(listOf(1, 3), scan.changed)
        val payload = DbDelta.vlddBytes(index(base), scan)
        // server/tests/test_dbdelta.py: GOLDEN_SHA256
        assertEquals(
            "28d173eb516f7e5009f09a00128dc2289aeb19b684e36085ea5ee69ab61186a3",
            MessageDigest.getInstance("SHA-256").digest(payload).toHex(),
        )
        assertEquals(MessageDigest.getInstance("SHA-256").digest(new).toHex(), scan.sha256)
    }

    @Test
    fun `unchanged database has no changed blocks and the same hash`() {
        val scan = DbDelta.scan(ByteArrayInputStream(base), base.size.toLong(), bs, index(base))
        assertTrue(scan.changed.isEmpty())
        assertEquals(index(base).sha256, scan.sha256)
    }

    @Test
    fun `too many changes or another block size mean the whole file`() {
        val other = block('x') + block('y') + block('z')
        val scan = DbDelta.scan(ByteArrayInputStream(other), other.size.toLong(), bs, index(base))
        assertFalse(DbDelta.deltaWorthIt(scan, index(base)))
        val small = DbDelta.scan(ByteArrayInputStream(new), new.size.toLong(), bs, index(base))
        assertTrue(DbDelta.deltaWorthIt(small, index(base)))
        val wrongSize = DbDelta.scan(ByteArrayInputStream(new), new.size.toLong(), 1024, index(base))
        assertFalse(DbDelta.deltaWorthIt(wrongSize, index(base)))
        assertFalse(DbDelta.deltaWorthIt(small, null))
    }

    @Test
    fun `index survives a round trip`() {
        val f = tmp.newFile("gbdb.index")
        val i = index(new)
        DbDelta.writeIndex(f, i)
        val back = DbDelta.readIndex(f)!!
        assertEquals(i.sha256, back.sha256)
        assertEquals(i.size, back.size)
        assertArrayEquals(i.hashes, back.hashes)
        assertNull(DbDelta.readIndex(tmp.newFile("junk").apply { writeText("nope") }))
    }

    private fun sqliteHeader(pageSize: Int, pages: Long, changeCounter: Long, validFor: Long): ByteArray {
        val h = ByteArray(100)
        "SQLite format 3\u0000".toByteArray(Charsets.US_ASCII).copyInto(h)
        h[16] = (pageSize shr 8).toByte(); h[17] = (pageSize and 0xFF).toByte()
        fun u32(off: Int, v: Long) { for (k in 0 until 4) h[off + k] = (v shr (24 - 8 * k)).toByte() }
        u32(24, changeCounter); u32(28, pages); u32(92, validFor)
        return h
    }

    @Test
    fun `header trims trailing bytes and spots files still being written`() {
        // 3 pages announced, the file is longer (Gadgetbridge's "w" mode did not truncate)
        assertEquals(DbDelta.Header(4096, 3 * 4096L), DbDelta.header(sqliteHeader(4096, 3, 7, 7), 5 * 4096L))
        // stale page count: fall back to whole pages of the file
        assertEquals(DbDelta.Header(4096, 2 * 4096L), DbDelta.header(sqliteHeader(4096, 3, 8, 7), 2 * 4096L + 100))
        // shorter than announced: not finished yet
        assertNull(DbDelta.header(sqliteHeader(4096, 3, 7, 7), 2 * 4096L))
        assertNull(DbDelta.header(ByteArray(100), 4096L))
    }

    private val cfg = LiveConfig()
    private fun at(h: Int, m: Int) = LocalDateTime.of(2026, 10, 3, h, m)

    @Test
    fun `day every 12 minutes, night every 4 hours, never past the morning`() {
        assertEquals(at(22, 11), LiveSchedule.next(at(21, 59), cfg))
        assertEquals(at(2, 11).plusDays(1), LiveSchedule.next(at(22, 11), cfg))
        assertEquals(at(6, 0), LiveSchedule.next(at(2, 11), cfg))
        assertEquals(at(6, 12), LiveSchedule.next(at(6, 0), cfg))
        assertTrue(LiveSchedule.inNight(LocalTime.of(23, 0), cfg))
        assertFalse(LiveSchedule.inNight(LocalTime.of(12, 0), cfg))
        val sameDayNight = LiveConfig(nightStart = LocalTime.of(1, 0), nightEnd = LocalTime.of(5, 0))
        assertTrue(LiveSchedule.inNight(LocalTime.of(3, 0), sameDayNight))
        assertFalse(LiveSchedule.inNight(LocalTime.of(23, 0), sameDayNight))
    }

    @Test
    fun `backoff stretches the day interval up to an hour`() {
        assertEquals(at(12, 24), LiveSchedule.next(at(12, 0), cfg, backoff = 2))
        assertEquals(at(13, 0), LiveSchedule.next(at(12, 0), cfg, backoff = 8))
    }

    @Test
    fun `foreground budget slows down and pauses`() {
        val now = 10_000_000_000L
        val entries = listOf(now - 3_600_000L to 60_000L, now - 90_000_000L to 9_000_000L) // second is older than 24 h
        assertEquals(Duration.ofMinutes(1), LiveSchedule.usedLast24h(entries, now))
        assertEquals(1, LiveSchedule.budgetFactor(Duration.ofHours(1)))
        assertEquals(2, LiveSchedule.budgetFactor(Duration.ofMinutes(280)))
        assertEquals(0, LiveSchedule.budgetFactor(Duration.ofMinutes(340)))
    }

    @Test
    fun `sync wait adapts to how long Gadgetbridge usually takes`() {
        assertEquals(60_000L, LiveSchedule.syncWait(emptyList(), 0))
        assertEquals(30_000L, LiveSchedule.syncWait(listOf(5_000L, 6_000L, 7_000L), 0))
        assertEquals(44_000L, LiveSchedule.syncWait(listOf(20_000L, 25_000L, 22_000L), 0))
        assertEquals(90_000L, LiveSchedule.syncWait(listOf(80_000L, 70_000L, 60_000L), 0))
        assertEquals(20_000L, LiveSchedule.syncWait(listOf(20_000L, 25_000L, 22_000L), 3))
    }

    @Test
    fun `gadgetbridge package and version`() {
        val all = GbIntents.PACKAGES.toSet()
        assertEquals("nodomain.freeyourgadget.gadgetbridge", GbIntents.pick(all, null))
        assertEquals("nodomain.freeyourgadget.gadgetbridge.nightly", GbIntents.pick(all, "nodomain.freeyourgadget.gadgetbridge.nightly"))
        assertEquals("nodomain.freeyourgadget.gadgetbridge.nightly", GbIntents.pick(setOf("nodomain.freeyourgadget.gadgetbridge.nightly"), "x"))
        assertNull(GbIntents.pick(emptySet(), null))
        assertTrue(GbIntents.atLeast("0.94.0", "0.94"))
        assertTrue(GbIntents.atLeast("0.100.1", "0.94"))
        assertFalse(GbIntents.atLeast("0.89.2", "0.94"))
        assertTrue(GbIntents.atLeast("0.95.0-nightly", "0.94"))
    }

    @Test
    fun `live state survives a round trip`() {
        val s = LiveData(
            nextAt = 5, lastSuccessAt = 6, lastUploadAt = 7, syncDurations = listOf(1000L, 2000L), finishWorks = true,
            watchSilent = 1, gbSilent = 2, backoff = 4, runtime = listOf(1L to 2L), paused = true, reportedError = "x",
            last = CycleRecord(9, "live", listOf(LiveStep("Uhr-Sync", "fertig", 1200, true)), true, "Übertragen"),
        )
        assertEquals(s, LiveState.fromJson(LiveState.toJson(s)))
    }
}
