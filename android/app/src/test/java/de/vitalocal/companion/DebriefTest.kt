package de.vitalocal.companion

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DebriefTest {
    @Test
    fun parsesDoneAndPendingDebriefs() {
        val d = parseDebrief(JSONObject("""
            {"status": "done", "model": "qwen", "created_at": "2026-10-24T18:00:00+02:00", "error": null,
             "result": {"summary": "3×10 @ 20 kg geschafft.", "score": 8, "highlights": ["Bank 3×10"], "watch": [],
                        "next_time": ["3×11 @ 20 kg"]}}
        """))!!
        assertEquals(8, d.score)
        assertEquals(listOf("Bank 3×10"), d.highlights)
        assertEquals(emptyList<String>(), d.watch)
        val p = parseDebrief(JSONObject("""{"status": "pending", "result": null, "error": null, "created_at": "2026-10-24T18:00:00+02:00"}"""))!!
        assertEquals("pending", p.status)
        assertNull(p.score)
        assertNull(parseDebrief(null))
    }
}
