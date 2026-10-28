package de.vitalocal.companion

import org.json.JSONArray
import org.json.JSONObject
import java.time.Duration
import java.time.OffsetDateTime
import java.util.UUID
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

// A workout logged in the app without the watch: built from a plan day (targets from the
// progression) or empty, kept as a draft on the phone while training, sent as a manual workout.

/** One set: it starts with the planned values; [done] once the user ticked it off. */
data class WSet(
    val id: String, val reps: Int?, val weightKg: Double?, val durationS: Int?,
    val warmup: Boolean = false, val done: Boolean = false,
)

/** One exercise of the workout with what was planned ([targetSets] × [targetReps] @ [targetWeight] or
 *  [targetDuration]) and its sets. [weights]: the weights the dumbbells can be loaded to (else ±[step]). */
data class WExercise(
    val key: String, val exerciseId: String, val planExerciseId: String?, val name: String, val kind: String,
    val bodyweight: Boolean, val restS: Int, val targetText: String?, val targetSets: Int, val targetReps: Int?,
    val targetWeight: Double?, val targetDuration: Int?, val weights: List<Double>, val step: Double,
    val sets: List<WSet>, val rating: String? = null,
) {
    val timed: Boolean get() = kind == "time"
    val workDone: List<WSet> get() = sets.filter { it.done && !it.warmup }
}

data class Workout(
    val manualKey: String, val start: String, val planDayId: String?, val title: String,
    val exercises: List<WExercise>, val restUntil: Long? = null, val restAfter: String? = null,
) {
    fun exercise(key: String) = exercises.first { it.key == key }

    fun withExercise(key: String, f: (WExercise) -> WExercise) = copy(exercises = exercises.map { if (it.key == key) f(it) else it })

    fun withSet(key: String, setId: String, f: (WSet) -> WSet) =
        withExercise(key) { e -> e.copy(sets = e.sets.map { if (it.id == setId) f(it) else it }) }

    val doneSets: Int get() = exercises.sumOf { e -> e.sets.count { it.done } }

    /** The body of `POST /training/sessions`: ticked-off sets only, reviewed, with the ratings. */
    fun toManual(now: OffsetDateTime): JSONObject {
        val minutes = max(1.0, Duration.between(OffsetDateTime.parse(start), now).seconds / 60.0)
        val sets = JSONArray()
        exercises.forEach { e ->
            e.sets.filter { it.done }.forEach { s ->
                sets.put(JSONObject().put("exercise_id", e.exerciseId).put("plan_exercise_id", e.planExerciseId ?: JSONObject.NULL)
                    .put("repetitions", if (e.timed) JSONObject.NULL else s.reps ?: JSONObject.NULL)
                    .put("weight_kg", s.weightKg ?: JSONObject.NULL)
                    .put("duration_s", if (e.timed) s.durationS ?: JSONObject.NULL else JSONObject.NULL)
                    .put("warmup", s.warmup))
            }
        }
        val ratings = JSONArray()
        exercises.filter { it.rating != null && it.workDone.isNotEmpty() }.forEach { e ->
            ratings.put(JSONObject().put("exercise_id", e.exerciseId).put("plan_exercise_id", e.planExerciseId ?: JSONObject.NULL)
                .put("rating", e.rating))
        }
        return JSONObject().put("manual_key", manualKey).put("start_ts", start).put("duration_min", minutes.roundToInt().toDouble())
            .put("title", title).put("plan_day_id", planDayId ?: JSONObject.NULL).put("reviewed", true)
            .put("sets", sets).put("ratings", ratings)
    }

    fun toJson(): JSONObject = JSONObject().put("manual_key", manualKey).put("start", start).put("plan_day_id", planDayId ?: JSONObject.NULL)
        .put("title", title).put("rest_until", restUntil ?: JSONObject.NULL).put("rest_after", restAfter ?: JSONObject.NULL)
        .put("exercises", JSONArray(exercises.map { e ->
            JSONObject().put("key", e.key).put("exercise_id", e.exerciseId).put("plan_exercise_id", e.planExerciseId ?: JSONObject.NULL)
                .put("name", e.name).put("kind", e.kind).put("bodyweight", e.bodyweight).put("rest_s", e.restS)
                .put("target_text", e.targetText ?: JSONObject.NULL).put("target_sets", e.targetSets)
                .put("target_reps", e.targetReps ?: JSONObject.NULL).put("target_weight", e.targetWeight ?: JSONObject.NULL)
                .put("target_duration", e.targetDuration ?: JSONObject.NULL).put("weights", JSONArray(e.weights)).put("step", e.step)
                .put("rating", e.rating ?: JSONObject.NULL)
                .put("sets", JSONArray(e.sets.map { s ->
                    JSONObject().put("id", s.id).put("reps", s.reps ?: JSONObject.NULL).put("weight", s.weightKg ?: JSONObject.NULL)
                        .put("duration", s.durationS ?: JSONObject.NULL).put("warmup", s.warmup).put("done", s.done)
                }))
        }))

    companion object {
        fun parse(o: JSONObject) = Workout(
            o.getString("manual_key"), o.getString("start"), o.optStringOrNull("plan_day_id"), o.optString("title"),
            o.optJSONArray("exercises").objects().map { e ->
                WExercise(e.getString("key"), e.getString("exercise_id"), e.optStringOrNull("plan_exercise_id"), e.optString("name"),
                    e.optString("kind", "reps"), e.optBoolean("bodyweight"), e.optInt("rest_s", DEFAULT_REST_S), e.optStringOrNull("target_text"),
                    e.optInt("target_sets", 3), e.optIntOrNull("target_reps"), e.optDoubleOrNull("target_weight"),
                    e.optIntOrNull("target_duration"), e.optJSONArray("weights").doubles(), e.optDouble("step", 2.5),
                    e.optJSONArray("sets").objects().map { s ->
                        WSet(s.getString("id"), s.optIntOrNull("reps"), s.optDoubleOrNull("weight"), s.optIntOrNull("duration"),
                            s.optBoolean("warmup"), s.optBoolean("done"))
                    }, e.optStringOrNull("rating"))
            },
            if (o.isNull("rest_until")) null else o.optLong("rest_until"), o.optStringOrNull("rest_after"),
        )

        /** A workout of a plan day ([day] from `GET /training/plan/current`): per entry the progression's
         *  suggestion as the sets to do. [exercises]: `GET /training/exercises` by id; [plates]: the weights
         *  one dumbbell can be loaded to. */
        fun fromPlanDay(day: JSONObject, planName: String?, exercises: Map<String, JSONObject>, plates: List<Double>, now: OffsetDateTime) =
            Workout(UUID.randomUUID().toString(), now.toString(), day.getString("id"), day.optString("name").ifEmpty { planName ?: "Training" },
                day.optJSONArray("exercises").objects().mapNotNull { pe ->
                    val ex = exercises[pe.getString("exercise_id")] ?: return@mapNotNull null
                    val next = pe.optJSONObject("next")
                    val timed = ex.optString("kind") == "time"
                    val sets = next?.optIntOrNull("sets") ?: pe.optInt("sets", 3)
                    val reps = if (timed) null else next?.optIntOrNull("reps") ?: pe.optIntOrNull("target_reps") ?: pe.optIntOrNull("reps_min")
                    val weight = next?.optDoubleOrNull("weight_kg") ?: pe.optDoubleOrNull("target_weight_kg")
                    val dur = if (timed) next?.optIntOrNull("duration_s") ?: pe.optIntOrNull("duration_s") ?: 30 else null
                    exerciseFrom(ex, plates, pe.optStringOrNull("plan_exercise_id"), pe.optIntOrNull("rest_s"),
                        next?.optStringOrNull("text") ?: pe.optStringOrNull("current"), sets, reps, weight, dur)
                })

        fun empty(now: OffsetDateTime) = Workout(UUID.randomUUID().toString(), now.toString(), null, tr("Training"), emptyList())

        /** An exercise with [sets] sets of the target (an exercise added freely: three empty sets). */
        fun exerciseFrom(
            ex: JSONObject, plates: List<Double>, planExerciseId: String? = null, restS: Int? = null, targetText: String? = null,
            sets: Int = 3, reps: Int? = null, weight: Double? = null, duration: Int? = null,
        ): WExercise {
            val timed = ex.optString("kind") == "time"
            val weights = when (ex.optInt("dumbbells")) {
                1 -> plates
                2 -> plates.map { it * 2 }
                else -> emptyList()
            }
            return WExercise(
                UUID.randomUUID().toString(), ex.getString("id"), planExerciseId, ex.optString("name"), if (timed) "time" else "reps",
                ex.optBoolean("bodyweight"), restS ?: DEFAULT_REST_S, targetText, sets, reps, weight, duration, weights,
                ex.optDouble("increment_kg", 2.5),
                List(sets) { WSet(UUID.randomUUID().toString(), reps, weight, if (timed) duration ?: 30 else null) },
            )
        }

        const val DEFAULT_REST_S = 90
    }
}

/** The next weight up or down: along the loadable weights, else by the exercise's step. */
fun WExercise.stepWeight(w: Double?, up: Boolean): Double {
    val cur = w ?: 0.0
    if (weights.isNotEmpty()) {
        val sorted = weights.sorted()
        return if (up) sorted.firstOrNull { it > cur + 1e-6 } ?: sorted.last()
        else sorted.lastOrNull { it < cur - 1e-6 } ?: 0.0
    }
    return max(0.0, ((if (up) cur + step else cur - step) * 100).roundToInt() / 100.0)
}

/** A rating from the sets against the plan, as the WebUI suggests it: fewer sets, fewer reps or a
 *  lighter weight → too hard; three reps or more above the target → too easy; else good. */
fun WExercise.suggestedRating(): String? {
    val work = workDone
    if (work.isEmpty()) return null
    if (timed) {
        val target = targetDuration ?: return "good"
        return if (work.size < targetSets || work.take(targetSets).any { (it.durationS ?: 0) < target }) "hard" else "good"
    }
    val tw = targetWeight
    val atWeight = if (tw == null) work else work.filter { (it.weightKg ?: 0.0) >= tw - 1e-6 }
    if (atWeight.size < targetSets) return "hard"
    val target = targetReps ?: return "good"
    val best = atWeight.map { it.reps ?: 0 }.sortedDescending().take(targetSets)
    return when {
        best.min() < target -> "hard"
        best.min() >= target + 3 -> "easy"
        else -> "good"
    }
}

fun formatKg(w: Double?): String = when {
    w == null -> "–"
    abs(w - w.roundToInt()) < 1e-6 -> "${w.roundToInt()}"
    else -> "%.2f".format(I18n.locale, w).trimEnd('0').trimEnd(',', '.')
}

internal fun JSONArray?.doubles(): List<Double> = if (this == null) emptyList() else List(length()) { getDouble(it) }
