package de.vitalocal.companion

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DeloadTest {
    @Test
    fun nextDayCarriesTheDeloadWeekAndItsTargets() {
        val n = parseNext(JSONObject("""
            {"plan": "GK", "plan_day": "Push", "exercises": [
              {"exercise": "Bank", "current": "3×10 @ 20 kg", "differs": false, "deload": true,
               "next": {"text": "2×8 @ 20 kg", "reason": "deload"}, "last": null}],
             "deload": {"active": true, "until": "2026-10-30", "suggested": false, "reasons": []}}
        """))!!
        assertEquals("2×8 @ 20 kg", n.entries.single().next)
        assertTrue(n.entries.single().deload)
        assertTrue(n.deload!!.active)
        val s = parseNext(JSONObject("""
            {"plan": "GK", "plan_day": "Push", "exercises": [],
             "deload": {"active": false, "until": null, "suggested": true,
                        "reasons": [{"key": "stalls", "exercises": ["Bank", "Rudern"]}, {"key": "scheduled", "weeks": 7}]}}
        """))!!
        assertFalse(s.deload!!.active)
        assertEquals(listOf("zweimal in Folge zu schwer: Bank, Rudern", "7 Wochen seit dem letzten Deload"), s.deload!!.reasons)
    }
}
