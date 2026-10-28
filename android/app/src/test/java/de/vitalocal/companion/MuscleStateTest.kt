package de.vitalocal.companion

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MuscleStateTest {
    @Test
    fun parsesRecoveryPerMuscleWithContributingWorkouts() {
        val m = parseMuscles(JSONObject("""
            {"at": "2026-10-24T18:00:00+02:00", "garmin_recovery_h": null, "muscles": [
              {"key": "glutes", "label": "Gesäß", "fatigue": 1.2, "state": "fatigued", "ready_in_h": 81.3,
               "half_life_h": 36, "last_trained": "2026-10-24T08:00:00+02:00", "hard_sets_7d": 8,
               "sessions": [{"id": 7, "ts": "2026-10-24T08:00:00+02:00", "sets": 8, "exercises": ["Goblet Squat"],
                             "remaining": 0.83, "provisional": true}]},
              {"key": "chest_upper", "label": "obere Brust", "fatigue": 0, "state": "ready", "ready_in_h": 0,
               "last_trained": null, "hard_sets_7d": 0, "sessions": []},
              {"key": "chest", "label": "Brust", "fatigue": 0, "state": "ready", "ready_in_h": 0,
               "last_trained": null, "hard_sets_7d": 0, "sessions": [], "alias": true}]}
        """))!!
        assertNull(m.garminRecoveryH)
        val g = m.muscles[0]
        assertEquals("fatigued", g.state)
        assertEquals(81.3, g.readyInH, 0.01)
        assertEquals(listOf("Goblet Squat"), g.sessions.single().exercises)
        assertTrue(g.sessions.single().provisional)
        assertNull(m.muscles[1].lastTrained)
        assertEquals(listOf("glutes", "chest_upper"), m.muscles.map { it.key })  // the old app's alias is skipped
        assertNull(parseMuscles(null))
    }
}
