package de.vitalocal.companion

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.OffsetDateTime

class WorkoutTest {
    private val now = OffsetDateTime.parse("2026-10-24T18:00:00+02:00")
    private val exercises = mapOf(
        "bench" to JSONObject("""{"id":"bench","name":"Kurzhantel-Bankdrücken","kind":"reps","dumbbells":1,"increment_kg":2,"bodyweight":false}"""),
        "plank" to JSONObject("""{"id":"plank","name":"Plank","kind":"time","dumbbells":0,"increment_kg":2.5,"bodyweight":true}"""),
        "pull" to JSONObject("""{"id":"pull","name":"Klimmzug","kind":"reps","dumbbells":0,"increment_kg":2.5,"bodyweight":true}"""),
    )
    private val day = JSONObject("""
        {"id":"day1","name":"Push","exercises":[
          {"plan_exercise_id":"pe1","exercise_id":"bench","sets":3,"reps_min":8,"reps_max":12,"rest_s":120,
           "current":"3×10 @ 20 kg","target_reps":10,"target_weight_kg":20,
           "next":{"sets":3,"reps":11,"weight_kg":20,"duration_s":null,"text":"3×11 @ 20 kg"}},
          {"plan_exercise_id":"pe2","exercise_id":"plank","sets":2,"duration_s":40,"rest_s":null,"current":"2×40 s",
           "next":{"sets":2,"reps":null,"weight_kg":null,"duration_s":45,"text":"2×45 s"}},
          {"plan_exercise_id":"pe3","exercise_id":"gone","sets":3}
        ]}
    """)
    private val plates = listOf(1.5, 4.0, 6.5, 9.0, 11.5, 14.0, 16.5, 19.0, 21.5)

    private fun workout() = Workout.fromPlanDay(day, "GK", exercises, plates, now)

    @Test
    fun aPlanDayBecomesSetsOfTheProgressionsSuggestion() {
        val w = workout()
        assertEquals("Push", w.title)
        assertEquals("day1", w.planDayId)
        assertEquals(2, w.exercises.size) // an exercise no longer in the catalog is left out
        val bench = w.exercises[0]
        assertEquals(3, bench.sets.size)
        assertTrue(bench.sets.all { it.reps == 11 && it.weightKg == 20.0 && !it.done })
        assertEquals(120, bench.restS)
        assertEquals("3×11 @ 20 kg", bench.targetText)
        val plank = w.exercises[1]
        assertTrue(plank.timed)
        assertEquals(listOf(45, 45), plank.sets.map { it.durationS })
        assertEquals(Workout.DEFAULT_REST_S, plank.restS)
    }

    @Test
    fun weightsStepAlongThePlatesOrByTheIncrement() {
        val bench = workout().exercises[0]
        assertEquals(21.5, bench.stepWeight(20.0, up = true), 1e-9)
        assertEquals(19.0, bench.stepWeight(20.0, up = false), 1e-9)
        assertEquals(21.5, bench.stepWeight(21.5, up = true), 1e-9) // heaviest stays
        val pull = Workout.exerciseFrom(exercises.getValue("pull"), plates)
        assertEquals(2.5, pull.stepWeight(null, up = true), 1e-9)
        assertEquals(0.0, pull.stepWeight(2.5, up = false), 1e-9)
        val two = Workout.exerciseFrom(JSONObject(exercises.getValue("bench").toString()).put("dumbbells", 2), plates)
        assertEquals(43.0, two.stepWeight(40.0, up = true), 1e-9) // the sum of two dumbbells
    }

    @Test
    fun theRatingFollowsTheSetsAgainstThePlan() {
        var w = workout()
        val bench = w.exercises[0]
        assertNull(bench.suggestedRating())
        fun done(reps: List<Int>, weight: Double = 20.0) = w.withExercise(bench.key) { e ->
            e.copy(sets = e.sets.zip(reps).map { (s, r) -> s.copy(reps = r, weightKg = weight, done = true) })
        }.exercise(bench.key)
        assertEquals("good", done(listOf(11, 11, 11)).suggestedRating())
        assertEquals("hard", done(listOf(11, 11, 9)).suggestedRating())
        assertEquals("easy", done(listOf(14, 15, 14)).suggestedRating())
        assertEquals("hard", done(listOf(11, 11, 11), weight = 19.0).suggestedRating())
        w = w.withSet(bench.key, bench.sets[0].id) { it.copy(done = true) }
        assertEquals("hard", w.exercise(bench.key).suggestedRating()) // only one of three sets
    }

    @Test
    fun finishingSendsTickedSetsWithRatingsAsAManualWorkout() {
        var w = workout()
        val bench = w.exercises[0]
        val plank = w.exercises[1]
        w = w.withSet(bench.key, bench.sets[0].id) { it.copy(done = true) }
            .withSet(bench.key, bench.sets[1].id) { it.copy(done = true, reps = 10) }
            .withExercise(bench.key) { it.copy(rating = "hard") }
            .withSet(plank.key, plank.sets[0].id) { it.copy(done = true) }
        val body = w.toManual(now.plusMinutes(47).plusSeconds(20))
        assertEquals(w.manualKey, body.getString("manual_key"))
        assertEquals(47.0, body.getDouble("duration_min"), 1e-9)
        assertEquals("day1", body.getString("plan_day_id"))
        assertTrue(body.getBoolean("reviewed"))
        val sets = body.getJSONArray("sets").objects()
        assertEquals(3, sets.size)
        assertEquals(listOf(11, 10), sets.take(2).map { it.getInt("repetitions") })
        assertEquals("pe1", sets[0].getString("plan_exercise_id"))
        assertTrue(sets[2].isNull("repetitions"))
        assertEquals(45, sets[2].getInt("duration_s"))
        val ratings = body.getJSONArray("ratings").objects()
        assertEquals(listOf("bench" to "hard"), ratings.map { it.getString("exercise_id") to it.getString("rating") })
    }

    @Test
    fun theDraftSurvivesAsJson() {
        var w = workout()
        val bench = w.exercises[0]
        w = w.withSet(bench.key, bench.sets[2].id) { it.copy(done = true, warmup = true) }.copy(restUntil = 123L, restAfter = "Bank")
        val back = Workout.parse(JSONObject(w.toJson().toString()))
        assertEquals(w, back)
        assertFalse(Workout.parse(JSONObject(Workout.empty(now).toJson().toString())).exercises.any())
    }
}
