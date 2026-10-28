package de.vitalocal.companion

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDateTime
import java.time.LocalTime

class RemindersTrainingTest {
    private val morning = Reminder(Reminder.SUPPLEMENT, "s1", "D3", LocalTime.of(8, 0))
    private val evening = Reminder(Reminder.HABIT, "h1", "Dehnen", LocalTime.of(20, 0))
    private val now = LocalDateTime.of(2026, 9, 30, 9, 0)

    @Test
    fun nextReminderIsTheEarliestNotYetFired() {
        assertEquals(LocalDateTime.of(2026, 9, 30, 20, 0), Reminders.nextDue(listOf(morning, evening), now) { if (it == morning.key) "2026-09-30" else null })
        // missed by an hour (phone busy): still today, right away
        assertEquals(now, Reminders.nextDue(listOf(morning), now) { null })
        // missed by more than two hours: tomorrow
        assertEquals(LocalDateTime.of(2026, 10, 1, 8, 0), Reminders.nextDue(listOf(morning), now.withHour(11)) { null })
        assertNull(Reminders.nextDue(emptyList(), now) { null })
    }

    @Test
    fun ratingsAreSentAsFullState() {
        val s = parseSession(JSONObject("""
            {"activity_id": 7, "activity": {"start_ts": "2026-09-27T15:00:00+02:00"}, "display_title": "Pull",
             "reviewed": true, "unassigned_sets": 0,
             "exercises": [{"exercise_id": "a", "plan_exercise_id": "pa", "exercise": "Rudern", "text": "10 @ 12 kg", "rating": "good"},
                           {"exercise_id": "b", "plan_exercise_id": null, "exercise": "Curl", "text": "12 @ 10 kg", "rating": null}]}
        """.trimIndent()))
        val set = ratingsWith(s, s.exercises[1], "hard")
        assertEquals(2, set.length())
        assertEquals("hard", set.getJSONObject(1).getString("rating"))
        assertEquals("pa", set.getJSONObject(0).getString("plan_exercise_id"))
        // tapping the chosen rating again clears it
        assertEquals(0, ratingsWith(s, s.exercises[0], "good").length())
    }

    @Test
    fun nextDayParsesAndTodayIsLocal() {
        val n = parseNext(JSONObject("""
            {"plan": "Push/Pull", "plan_day": "Push", "exercises": [
              {"exercise": "Bank", "current": "3×8 @ 23 kg", "differs": true,
               "next": {"text": "3×9 @ 23 kg", "reason": "good"}, "last": {"rating": "good", "sets": ["8 @ 23 kg", "8 @ 23 kg"]}},
              {"exercise": "Plank", "current": "2×45 s", "differs": false, "next": {"text": "2×45 s"}, "last": null}]}
        """.trimIndent()))!!
        assertEquals("Push", n.day)
        assertEquals("3×9 @ 23 kg", n.entries[0].next)
        assertEquals("8 @ 23 kg, 8 @ 23 kg", n.entries[0].last)
        assertNull(n.entries[1].next)
        assertNull(parseNext(null))
        // 23:30 UTC on the 29th is already the 30th in Berlin
        val s = SessionView(1, "2026-09-29T23:30:00Z", "x", null, true, 0, emptyList())
        assertEquals(java.time.LocalDate.of(2026, 9, 30), sessionDay(s, java.time.ZoneId.of("Europe/Berlin")))
    }

    @Test
    fun readinessAndSuggestionParse() {
        val r = parseReadiness(JSONObject("""
            {"score": 72, "level": "moderate", "components": [
              {"key": "recovery_time", "value": 20.3, "unit": "h", "impact": -17},
              {"key": "sleep_score", "value": 83, "unit": null, "impact": -10}],
             "garmin": {"score": 86, "level": 4, "ts": "2026-09-27T10:25:58+02:00"}}
        """.trimIndent()))!!
        assertEquals(72, r.score)
        assertEquals(86, r.garmin)
        assertEquals(4, r.garminLevel)
        assertEquals(-17, r.parts[0].impact)
        val none = parseReadiness(JSONObject("""{"score": null, "level": null, "components": [], "garmin": null}"""))!!
        assertNull(none.score)
        assertNull(none.garmin)
        val s = parseSession(JSONObject("""
            {"activity_id": 7, "activity": {"start_ts": "2026-09-27T15:00:00+02:00"}, "reviewed": true,
             "exercises": [{"exercise_id": "a", "plan_exercise_id": "pa", "exercise": "Klimmzug", "text": "10 @ 15 kg, 10 @ 15 kg, 8 @ 15 kg",
                            "rating": null, "planned": "3×10 @ 15 kg", "suggestion": {"rating": "hard", "why": "target missed"}}]}
        """.trimIndent()))
        assertEquals("hard", s.exercises[0].suggestion)
        assertEquals("3×10 @ 15 kg", s.exercises[0].planned)
    }
}
