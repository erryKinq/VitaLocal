package de.vitalocal.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.File
import java.time.LocalDateTime
import java.time.ZoneId

class ExportsTest {
    private val zone = ZoneId.of("Europe/Berlin")
    private fun at(y: Int, mo: Int, d: Int, h: Int) = LocalDateTime.of(y, mo, d, h, 0).atZone(zone).toInstant().toEpochMilli()
    private fun file(name: String, modified: Long) = ExportFile(name, 1, modified, "ref:$name")
    private val now = at(2026, 9, 28, 14)

    @Test
    fun recognisesExportNames() {
        assertTrue(Exports.isGadgetbridgeExport("Gadgetbridge.zip"))
        assertTrue(Exports.isGadgetbridgeExport("gadgetbridge_2026-09-27.zip"))
        assertFalse(Exports.isGadgetbridgeExport("Gadgetbridge"))
        assertFalse(Exports.isGadgetbridgeExport("other.zip"))
    }

    @Test
    fun recognisesLabReportsByContent() {
        assertTrue(Exports.isLabReport("%PDF-1.7".toByteArray()))
        assertTrue(Exports.isLabReport(byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte())))
        assertTrue(Exports.isLabReport(byteArrayOf(0x49, 0x49, 0x2A, 0x00)))
        assertTrue(Exports.isLabReport("RIFF\u0000\u0000\u0000\u0000WEBP".toByteArray()))
        assertFalse(Exports.isLabReport("PK\u0003\u0004".toByteArray())) // ZIP export
        assertFalse(Exports.isLabReport("{\"version\"".toByteArray())) // Waistline JSON
        assertFalse(Exports.isLabReport(ByteArray(0)))
    }

    @Test
    fun cleanupKeepsNewestAutoExportAndToday() {
        val auto = file("Gadgetbridge.zip", at(2026, 9, 25, 10)) // old, but Gadgetbridge writes here
        val manualOld = file("gadgetbridge_2026-09-26.zip", at(2026, 9, 26, 20))
        val manualToday = file("gadgetbridge_2026-09-28.zip", at(2026, 9, 28, 8))
        val newest = file("gadgetbridge_2026-09-28b.zip", at(2026, 9, 28, 13))
        val files = listOf(auto, manualOld, manualToday, newest)
        assertEquals(listOf(manualOld), Exports.cleanupCandidates(files, now, zone))
    }

    @Test
    fun cleanupNeverTouchesASingleOldFile() {
        val only = file("gadgetbridge_2026-01-01.zip", at(2026, 1, 1, 12))
        assertEquals(emptyList<ExportFile>(), Exports.cleanupCandidates(listOf(only), now, zone))
        assertEquals(emptyList<ExportFile>(), Exports.cleanupCandidates(emptyList(), now, zone))
    }

    @Test
    fun deleteOnlyWhenImported() {
        val ok = BatchInfo(1, "gadgetbridge_export", "completed", null)
        val partial = BatchInfo(2, "gadgetbridge_export", "completed_with_errors", null)
        val failed = BatchInfo(3, "gadgetbridge_export", "failed", "boom")
        val running = BatchInfo(4, "gadgetbridge_export", "processing", null)
        assertTrue(Exports.safeToDelete(listOf(ok), newestImported = false))
        assertTrue(Exports.safeToDelete(listOf(partial), newestImported = false))
        assertFalse(Exports.safeToDelete(listOf(failed, running), newestImported = false))
        assertFalse(Exports.safeToDelete(null, newestImported = false))
        assertTrue(Exports.safeToDelete(null, newestImported = true)) // superseded by an imported snapshot
    }

    @Test
    fun settleTime() {
        assertFalse(Exports.isSettled(file("a", now - 10_000), now))
        assertTrue(Exports.isSettled(file("a", now - 61_000), now))
    }

    @Test
    fun copyHashingMatchesKnownDigest() {
        val tmp = File.createTempFile("hash", ".bin")
        val sha = Exports.copyHashing(ByteArrayInputStream("abc".toByteArray()), tmp)
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", sha)
        assertEquals("abc", tmp.readText())
        assertEquals(sha, Exports.hash(ByteArrayInputStream("abc".toByteArray())))
        tmp.delete()
    }

    @Test
    fun plainHttpOnlyForLocalAddresses() {
        listOf("192.168.1.10", "10.8.0.1", "172.20.1.1", "100.64.0.5", "nas", "server.arpa", "vita.lan", "fd00::1")
            .forEach { assertTrue(it, Settings.isLocalHost(it)) }
        listOf("example.com", "8.8.8.8", "172.32.0.1", "192.169.0.1", "vitalocal.de")
            .forEach { assertFalse(it, Settings.isLocalHost(it)) }
    }
}
