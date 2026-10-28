package de.vitalocal.companion

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId

class TodayTest {
    private val serverJson = """
        {"day": "2026-09-28",
         "metrics": {"steps": {"value": 8123, "unit": "steps", "decimals": 0, "better": "higher", "baseline": 7000.5},
                     "weight": {"value": null, "unit": "kg", "decimals": 1, "better": null, "baseline": null}},
         "sleep": {"asleep_minutes": 431, "bed_start": "2026-09-27T21:40:00Z", "bed_end": "2026-09-28T05:10:00Z", "minutes": {}},
         "drinks": {"total_ml": 400, "by_drink": {"water": 250, "tea": 150}, "goal_ml": 2600,
                    "goal": {"goal_ml": 2600, "base_ml": 2261, "training_ml": 350, "weight_kg": 64.6, "training_min": 42},
                    "entries": [{"id": "d1", "ts": "2026-09-28T08:00:00+02:00", "drink": "water", "ml": 250, "source": "webui"},
                                {"id": "d0", "ts": "2026-09-28T07:00:00+02:00", "label": "tea", "value": 150}]},
         "supplements": {"items": [{"id": "s1", "name": "Vitamin D3", "form": "drops", "per_day": 2, "count": 1, "done": false,
                                    "streak": 4, "done_last_30": 20},
                                   {"id": "s2", "name": "D3 20000", "form": "capsule", "per_day": 1, "per_week": 1, "count": 0,
                                    "done": false, "week_count": 1, "week_done": true, "streak": 2, "streak_unit": "weeks"}],
                          "ingredients": []},
         "training": {"plan": "Push/Pull", "plan_day": "Pull", "unreviewed_sessions": 2,
                      "exercises": [{"exercise": "Rudern", "target": "3×10 @ 12 kg", "next": "3×11 @ 12 kg"},
                                    {"exercise": "Curl", "target": "3×12 @ 10 kg", "next": null}]},
         "checkins": [{"id": "c1", "ts": "2026-09-28T09:00:00+02:00", "kind": "mood", "value": 4, "label": null, "note": null}],
         "nutrition": null, "activities": [],
         "habits": [{"id": "h1", "name": "Lesen", "kind": "count", "unit": "Seiten", "target": 20, "metric": null,
                     "streak": 4, "streak_unit": "days", "jokers": 1, "today": {"value": 5, "done": false}},
                    {"id": "h2", "name": "Dehnen", "kind": "check", "unit": null, "target": null, "metric": null,
                     "streak": 0, "streak_unit": "days", "today": null},
                    {"id": "h3", "name": "10k Schritte", "kind": "check", "metric": "steps",
                     "streak": 1, "streak_unit": "days", "today": {"value": 8123, "done": false, "auto": true}},
                    {"id": "h4", "name": "Ins Bett", "kind": "check", "metric": "sleep.bedtime",
                     "streak": 0, "streak_unit": "days",
                     "today": {"value": 1361, "done": false, "auto": true, "delta": 21, "display": "22:41"}},
                    {"id": "h5", "name": "Kein Koffein nach 14 Uhr", "kind": "check", "metric": "drinks.last_caffeine",
                     "streak": 3, "streak_unit": "days", "today": {"value": null, "done": true, "auto": true, "none": true}}],
         "habit_rewards": {"flames": {"total": 45, "today": 3, "week": 12, "best_day": 5},
                           "level": {"level": 3, "title": "Ember", "progress": 0.5, "flames": 45, "next": 60},
                           "best_current": {"name": "Dehnen", "icon": "🤸", "streak": 8, "unit": "days"},
                           "best_ever": {"name": "Sauna", "icon": null, "streak": 3, "unit": "weeks"},
                           "perfect": {"current": 2, "best": 4, "total": 6, "today": false},
                           "unlocked": 7, "achievements": 60, "new": [{"key": "flames:10", "icon": "🔥", "title": "Kindling"}],
                           "garden": [{"id": "h2", "name": "Dehnen", "icon": "🤸", "plant": "🪴", "stage": "bush", "stage_index": 3,
                                       "stage_title": "Bush", "health": "healthy", "growth": 8, "next_at": 21, "next_plant": "🌻"},
                                      {"id": "h1", "name": "Lesen", "icon": null, "plant": "🌱", "stage": "sprout", "stage_index": 1,
                                       "stage_title": "Sprout", "health": "wilting", "growth": 1, "next_at": 3, "next_plant": "🌿"}],
                           "challenges": [{"kind": "habit", "habit": "Lesen", "routine": null, "target": 3, "progress": 1,
                                           "bonus": 15, "completed_on": null}]},
         "sync": {"last_heart_rate_at": "2026-09-28T12:00:00Z", "sources": {}}}
    """.trimIndent()

    private val data = TodayData.parse(JSONObject(serverJson), 1)

    @Test
    fun parsesServerOverview() {
        assertEquals(8123.0, data.metrics["steps"]!!.value!!, 0.0)
        assertEquals(null, data.metrics["weight"]!!.value)
        assertEquals(431, data.sleep.asleepMinutes)
        assertEquals(400.0, data.drinksTotal, 0.0) // drink/ml, and label/value from older servers
        assertEquals(listOf("water", "tea"), data.drinks.map { it.drink })
        assertEquals(listOf("mood"), data.checkins.map { it.kind })
        assertTrue(data.habits.first { it.id == "h3" }.automatic)
        assertEquals(null, data.habits.first { it.id == "h2" }.done)
        // no caffeine field (older server): coffee and tea default to caffeinated
        assertEquals(listOf(false, true), data.drinks.map { it.caffeine })
        val bed = data.habits.first { it.id == "h4" }
        assertEquals("22:41" to 21.0, bed.display to bed.delta)
        assertTrue(data.habits.first { it.id == "h5" }.none)
        assertEquals(DrinkGoal(2600.0, 2261.0, 350.0), data.drinkGoal)
        assertEquals(2600.0, data.drinkGoalMl(2000), 0.0)
        assertEquals(2000.0, TodayData.empty("2026-09-28").drinkGoalMl(2000), 0.0) // local fallback
    }

    @Test
    fun habitRewardsParse() {
        val r = data.rewards!!
        assertEquals(45 to 3, r.flames to r.flamesToday)
        assertEquals("Glut", r.levelTitleDe)
        assertEquals(15, r.toNextLevel)
        assertEquals("🤸 Dehnen" to "8 Tage", r.bestStreak!!.label to r.bestStreak!!.span)
        assertEquals("3 Wochen", r.record!!.span)
        assertEquals(listOf("🔥"), r.newIcons)
        assertEquals(null, TodayData.parse(JSONObject("{\"day\": \"2026-09-28\"}"), 1).rewards)
    }

    @Test
    fun gardenChallengesAndStreakWarning() {
        val r = data.rewards!!
        assertEquals(listOf("Lesen", "Dehnen"), gardenOrder(r.garden).map { it.name }) // the wilting one first
        assertEquals("wilting" to "Keimling", r.garden[1].health to r.garden[1].stageDe)
        assertEquals("Lesen diese Woche 3×", r.challenges.single().text)
        // Lesen: 4 days, open today, one joker; the others are done, too short or automatic and unknown
        assertEquals("Deine Serie endet heute: Lesen (4 T., Joker springt ein)", Reminders.streakWarning(data))
    }

    @Test
    fun weeklySupplementIsSettledOnceTheWeekIsDone() {
        val w = data.supplements.first { it.id == "s2" }
        assertTrue(w.settled && !w.done)
        assertEquals(1 to "weeks", w.perWeek to w.streakUnit)
        assertFalse(data.supplements.first { it.id == "s1" }.settled)
        // logging it today again keeps the week count right
        assertEquals(2, w.withCount(1).weekCount)
        assertEquals(1, w.withCount(1).withCount(0).weekCount)
    }

    @Test
    fun nextWorkoutParses() {
        val t = data.training!!
        assertEquals("Pull", t.day)
        assertEquals(2, t.unreviewed)
        assertEquals(PlannedExercise("Rudern", "3×10 @ 12 kg", "3×11 @ 12 kg"), t.exercises[0])
        assertEquals(null, t.exercises[1].next)
        assertEquals(null, TodayData.parse(JSONObject("{\"day\": \"2026-09-28\"}"), 1).training)
    }

    @Test
    fun supplementsParseAndOverlay() {
        val s = data.supplements.first()
        assertEquals(Supplement("s1", "Vitamin D3", "drops", 2, 1, 4), s)
        val v = data.withPending(listOf(PendingOp.LogSupplement("o1", "s1", "2026-09-28", 2)))
        assertTrue(v.supplements.first().done)
        val op = PendingOp.LogSupplement("o2", "s1", "2026-09-28", 3)
        assertEquals(op, PendingOp.fromJson(JSONObject(op.toJson().toString())))
    }

    @Test
    fun drinkCorrectionsAreOverlaid() {
        val fixed = Drink("d1", "2026-09-28T08:00:00+02:00", "coffee", 300.0, caffeine = false)
        val v = data.withPending(listOf(PendingOp.UpdateDrink("o1", fixed, "2026-09-28")))
        assertEquals(fixed, v.drinks.first { it.id == "d1" })
        assertEquals(450.0, v.drinksTotal, 0.0)
    }

    @Test
    fun pendingEditsAreOverlaid() {
        val drink = Drink("d2", "2026-09-28T10:00:00+02:00", "tea", 123.0)
        val legacy = Checkin("d3", "2026-09-28T11:00:00+02:00", "water", 100.0, "coffee", null) // outbox of app 0.2
        val ops = listOf(
            PendingOp.AddDrink("o1", drink, "2026-09-28"),
            PendingOp.AddDrink("o2", drink.copy(id = "other-day"), "2026-09-27"),
            PendingOp.DeleteCheckin("o3", "c1"),
            PendingOp.LogHabit("o4", "h1", "2026-09-28", 20.0),
            PendingOp.LogHabit("o5", "h2", "2026-09-28", 1.0),
            PendingOp.AddCheckin("o6", legacy, "2026-09-28"),
            PendingOp.DeleteDrink("o7", "d0"),
        )
        val v = data.withPending(ops)
        assertEquals(473.0, v.drinksTotal, 0.0)
        assertEquals(listOf("d1", "d2", "d3"), v.drinks.map { it.id })
        assertTrue(v.checkins.isEmpty())
        assertTrue(v.habits.first { it.id == "h1" }.done!!) // count reaches target
        assertTrue(v.habits.first { it.id == "h2" }.done!!)
        // Once the server has the entry, the overlay does not add it twice.
        assertEquals(3, v.withPending(ops.take(1)).drinks.size)
    }

    @Test
    fun countBelowTargetIsNotDone() {
        assertFalse(data.habits.first { it.id == "h1" }.withValue(19.0).done!!)
        assertFalse(data.habits.first { it.id == "h2" }.withValue(0.0).done!!)
    }

    @Test
    fun opsSurviveSerialization() {
        val ops = listOf(
            PendingOp.AddCheckin("o1", Checkin("x", "2026-09-28T10:00:00+02:00", "note", null, null, "Hallo"), "2026-09-28"),
            PendingOp.DeleteCheckin("o2", "y"),
            PendingOp.LogHabit("o3", "h", "2026-09-28", 2.0),
            PendingOp.AddDrink("o4", Drink("z", "2026-09-28T10:00:00+02:00", "coffee", 200.0), "2026-09-28"),
            PendingOp.DeleteDrink("o5", "z"),
            PendingOp.UpdateDrink("o6", Drink("z", "2026-09-28T10:00:00+02:00", "tea", 250.0, caffeine = false), "2026-09-28"),
        )
        assertEquals(ops, ops.map { PendingOp.fromJson(JSONObject(it.toJson().toString())) })
        // an AddDrink queued by app 0.3.0 has no caffeine field
        val old = JSONObject("""{"type":"drink","op":"o","day":"2026-09-28","id":"q","ts":"2026-09-28T10:00:00+02:00","drink":"coffee","ml":200}""")
        assertTrue((PendingOp.fromJson(old) as PendingOp.AddDrink).drink.caffeine)
    }

    @Test
    fun outboxKeepsWhatItCannotRead() {
        val stored = org.json.JSONArray(
            """[{"type":"habit","op":"o1","habit":"h1","day":"2026-09-28","value":3},
                {"type":"food_entry","op":"o2","id":"e1","food":"f1","amount":30},
                {"type":"drink","op":"o3","day":"2026-09-28"},
                {"type":"delete","op":"o4","id":"c1"}]""",
        )
        val ops = PendingOp.listFromJson(stored)
        assertEquals(listOf("o1", "o2", "o3", "o4"), ops.map { it.opId })
        assertTrue(ops[0] is PendingOp.LogHabit) // no longer the fallback for everything
        assertTrue(ops[1] is PendingOp.Unknown && ops[2] is PendingOp.Unknown)
        // written back unchanged, so a newer app version can still send it
        assertEquals(stored.getJSONObject(1).toString(), ops[1].toJson().toString())
        assertEquals(null, ops[1].opDay)
        assertEquals(data, data.withPending(listOf(ops[1])))
    }

    @Test
    fun pastDaysGetLocalNoon() {
        val ts = timestampFor("2026-01-15", ZoneId.of("Europe/Berlin"))
        assertEquals("2026-01-15T12:00+01:00", ts)
    }

    @Test
    fun sentOpsStayVisibleUntilTheReloadHasThem() {
        // sent, no longer in the outbox, the cached server state is still the old one: no jump back to 5
        val sent = PendingOp.LogHabit("o1", "h1", "2026-09-28", 7.0)
        val state = DayState(days = mapOf("2026-09-28" to data), applied = listOf(sent))
        assertEquals(7.0, state.view("2026-09-28").habits.first { it.id == "h1" }.value)
        assertEquals(null, sent.opDay?.takeIf { it != "2026-09-28" })
        assertEquals(null, PendingOp.DeleteDrink("o2", "d1").opDay)
    }
}
