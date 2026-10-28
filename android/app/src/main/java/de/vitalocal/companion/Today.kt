package de.vitalocal.companion

import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId

/** Parsed `GET /api/v1/today` (see docs/API.md), plus local edits not yet on the server. */
data class TodayData(
    val day: String,
    val metrics: Map<String, Metric>,
    val sleep: Sleep,
    val drinks: List<Drink>,
    val checkins: List<Checkin>,
    val nutrition: Nutrition?,
    val activities: List<Activity>,
    val habits: List<Habit>,
    val lastWatchData: String?,
    val fetchedAt: Long,
    /** The server's drink goal for the day (formula), null when switched off or from an older server. */
    val drinkGoal: DrinkGoal? = null,
    val supplements: List<Supplement> = emptyList(),
    /** Next workout of the active training plan; null without a plan or from an older server. */
    val training: NextWorkout? = null,
    /** Flames, level and records across habits; null from an older server. */
    val rewards: HabitRewards? = null,
    /** Burned energy (watch, or estimated from profile + workouts); null without a basis or from an older server. */
    val energy: Energy? = null,
) {
    val drinksTotal: Double get() = drinks.sumOf { it.ml }

    /** Server goal, else the local setting. */
    fun drinkGoalMl(localGoal: Int): Double = drinkGoal?.goalMl ?: localGoal.toDouble()

    companion object {
        fun empty(day: String) = TodayData(
            day, emptyMap(), Sleep(null, null, null), emptyList(), emptyList(), null, emptyList(), emptyList(), null, 0,
        )

        fun parse(o: JSONObject, fetchedAt: Long): TodayData = TodayData(
            day = o.getString("day"),
            energy = o.optJSONObject("energy")?.let { e ->
                Energy(e.optDouble("total"), e.optDouble("rest"), e.optDouble("active"), e.optString("source", "watch"))
            },
            metrics = o.optJSONObject("metrics")?.let { m ->
                m.keys().asSequence().associateWith { k ->
                    val x = m.getJSONObject(k)
                    Metric(x.optDoubleOrNull("value"), x.optStringOrNull("unit"), x.optInt("decimals"),
                        x.optStringOrNull("better"), x.optDoubleOrNull("baseline"))
                }
            } ?: emptyMap(),
            sleep = o.optJSONObject("sleep").let { s ->
                Sleep(s?.optIntOrNull("asleep_minutes"), s?.optStringOrNull("bed_start"), s?.optStringOrNull("bed_end"))
            },
            drinks = o.optJSONObject("drinks")?.optJSONArray("entries").objects().map {
                // drink/ml since server 0.2; label/value before; caffeine since 0.3
                val kind = it.optStringOrNull("drink") ?: it.optStringOrNull("label") ?: "water"
                Drink(it.getString("id"), it.getString("ts"), kind, it.optDoubleOrNull("ml") ?: it.optDoubleOrNull("value") ?: 0.0,
                    it.optBooleanOrNull("caffeine") ?: caffeinatedByDefault(kind))
            },
            checkins = o.optJSONArray("checkins").objects().map {
                Checkin(it.getString("id"), it.getString("ts"), it.getString("kind"), it.optDoubleOrNull("value"),
                    it.optStringOrNull("label"), it.optStringOrNull("note"))
            },
            nutrition = o.optJSONObject("nutrition")?.let { n ->
                val t = n.optJSONObject("totals")
                Nutrition(n.optInt("entries"), t?.optDoubleOrNull("calories"), t?.optDoubleOrNull("proteins"),
                    t?.optDoubleOrNull("carbohydrates"), t?.optDoubleOrNull("fat"))
            },
            activities = o.optJSONArray("activities").objects().map {
                Activity(it.optStringOrNull("name") ?: it.optStringOrNull("sport") ?: "Aktivität", it.getString("start_ts"),
                    it.optDoubleOrNull("elapsed_s"), it.optDoubleOrNull("energy_kcal"), it.optDoubleOrNull("distance_m"))
            },
            habits = o.optJSONArray("habits").objects().map {
                val today = it.optJSONObject("today")
                Habit(
                    id = it.getString("id"),
                    name = it.getString("name"),
                    icon = it.optStringOrNull("icon"),
                    kind = it.optString("kind", "check"),
                    unit = it.optStringOrNull("unit"),
                    target = it.optDoubleOrNull("target"),
                    automatic = !it.isNull("metric"),
                    streak = it.optInt("streak"),
                    streakUnit = it.optString("streak_unit", "days"),
                    jokers = it.optInt("jokers"),
                    hard = it.optBoolean("hard"),
                    value = today?.optDoubleOrNull("value"),
                    done = today?.optBoolean("done"),
                    display = today?.optStringOrNull("display"),
                    delta = today?.optDoubleOrNull("delta"),
                    none = today?.optBoolean("none") == true,
                )
            },
            lastWatchData = o.optJSONObject("sync")?.optStringOrNull("last_heart_rate_at"),
            supplements = o.optJSONObject("supplements")?.optJSONArray("items").objects().map {
                Supplement(it.getString("id"), it.getString("name"), it.optString("form", "capsule"),
                    it.optInt("per_day", 1), it.optInt("count"), it.optInt("streak"),
                    perWeek = it.optInt("per_week", 7), weekCount = it.optInt("week_count"),
                    streakUnit = it.optString("streak_unit", "days"))
            },
            training = o.optJSONObject("training")?.let { t ->
                NextWorkout(
                    t.optString("plan_day"), t.optString("plan"), t.optInt("unreviewed_sessions"),
                    t.optJSONArray("exercises").objects().map {
                        PlannedExercise(it.getString("exercise"), it.optString("target"), it.optStringOrNull("next"))
                    },
                )
            },
            drinkGoal = o.optJSONObject("drinks")?.let { dr ->
                val goal = dr.optDoubleOrNull("goal_ml") ?: return@let null
                val parts = dr.optJSONObject("goal")
                DrinkGoal(goal, parts?.optDoubleOrNull("base_ml"), parts?.optDoubleOrNull("training_ml"))
            },
            rewards = o.optJSONObject("habit_rewards")?.let(HabitRewards::parse),
            fetchedAt = fetchedAt,
        )
    }
}

/** Taken [count] of [perDay] units today; some supplements only on [perWeek] days a week. */
data class Supplement(
    val id: String, val name: String, val form: String, val perDay: Int, val count: Int, val streak: Int,
    val perWeek: Int = 7,
    /** Days this week (up to today) it was taken completely. */
    val weekCount: Int = 0,
    val streakUnit: String = "days",
) {
    val done: Boolean get() = count >= perDay
    /** Nothing left to take today: done today, or this week's goal is already reached. */
    val settled: Boolean get() = done || (perWeek < 7 && weekCount >= perWeek)

    /** The local count changed: today's share of the week count follows it. */
    fun withCount(n: Int): Supplement {
        val before = if (done) 1 else 0
        val after = if (n >= perDay) 1 else 0
        return copy(count = n, weekCount = (weekCount - before + after).coerceAtLeast(0))
    }
}

/** [next] is the progression's suggestion when it differs from the current [target]. */
data class PlannedExercise(val name: String, val target: String, val next: String?)
data class NextWorkout(val day: String, val plan: String, val unreviewed: Int, val exercises: List<PlannedExercise>)

data class DrinkGoal(val goalMl: Double, val baseMl: Double?, val trainingMl: Double?)
data class Metric(val value: Double?, val unit: String?, val decimals: Int, val better: String?, val baseline: Double?)
data class Sleep(val asleepMinutes: Int?, val bedStart: String?, val bedEnd: String?)
data class Checkin(val id: String, val ts: String, val kind: String, val value: Double?, val label: String?, val note: String?)
data class Drink(val id: String, val ts: String, val drink: String, val ml: Double, val caffeine: Boolean = caffeinatedByDefault(drink))

/** Coffee and tea count as caffeinated unless the entry says otherwise (same default as the server). */
fun caffeinatedByDefault(drink: String) = drink == "coffee" || drink == "tea"
data class Nutrition(val entries: Int, val calories: Double?, val protein: Double?, val carbs: Double?, val fat: Double?)
data class Activity(val name: String, val start: String, val elapsedS: Double?, val kcal: Double?, val distanceM: Double?)

/** Habit gamification summary (server `habit_rewards`). */
data class HabitRewards(
    val flames: Int,
    val flamesToday: Int,
    val level: Int,
    val levelTitle: String,
    val levelProgress: Float,
    val toNextLevel: Int,
    val bestStreak: StreakRecord?,
    val record: StreakRecord?,
    val perfectStreak: Int,
    val perfectToday: Boolean,
    val unlocked: Int,
    val achievements: Int,
    /** Icons of achievements not yet acknowledged (in the WebUI). */
    val newIcons: List<String>,
    val bonusFlames: Int = 0,
    val garden: List<Plant> = emptyList(),
    val challenges: List<Challenge> = emptyList(),
) {
    /** The level title in German (the server sends the English source string). */
    val levelTitleDe: String get() = LEVEL_TITLES_DE[levelTitle] ?: levelTitle

    companion object {
        private val LEVEL_TITLES_DE = mapOf(
            "Spark" to tr("Funke"), "Ember" to tr("Glut"), "Flame" to tr("Flamme"), "Blaze" to tr("Lohe"), "Bonfire" to tr("Lagerfeuer"),
            "Beacon" to tr("Leuchtfeuer"), "Firestorm" to tr("Feuersturm"), "Phoenix" to tr("Phönix"),
        )

        fun parse(o: JSONObject): HabitRewards {
            val f = o.optJSONObject("flames")
            val l = o.optJSONObject("level")
            val p = o.optJSONObject("perfect")
            fun streak(x: JSONObject?) = x?.let {
                StreakRecord(it.getString("name"), it.optStringOrNull("icon"), it.optInt("streak"), it.optString("unit", "days"))
            }
            return HabitRewards(
                flames = f?.optInt("total") ?: 0,
                flamesToday = f?.optInt("today") ?: 0,
                level = l?.optInt("level", 1) ?: 1,
                levelTitle = l?.optString("title").orEmpty(),
                levelProgress = (l?.optDouble("progress") ?: 0.0).toFloat(),
                toNextLevel = (l?.optInt("next") ?: 0) - (l?.optInt("flames") ?: 0),
                bestStreak = streak(o.optJSONObject("best_current")),
                record = streak(o.optJSONObject("best_ever")),
                perfectStreak = p?.optInt("current") ?: 0,
                perfectToday = p?.optBoolean("today") == true,
                unlocked = o.optInt("unlocked"),
                achievements = o.optInt("achievements"),
                newIcons = o.optJSONArray("new").objects().map { it.optString("icon") },
                bonusFlames = f?.optInt("bonus") ?: 0,
                garden = o.optJSONArray("garden").objects().map {
                    Plant(it.getString("id"), it.getString("name"), it.optStringOrNull("icon"), it.optString("plant", "🌰"),
                        it.optInt("stage_index"), it.optString("stage_title"), it.optString("health", "healthy"),
                        it.optDouble("growth", 0.0), it.optDoubleOrNull("next_at"), it.optStringOrNull("next_plant"))
                },
                challenges = o.optJSONArray("challenges").objects().map {
                    Challenge(it.optString("kind"), it.optStringOrNull("habit"), it.optStringOrNull("routine"),
                        it.optInt("target"), it.optInt("progress"), it.optInt("bonus"), !it.isNull("completed_on"))
                },
            )
        }
    }
}

/** A habit's plant in the garden: grows with done days, `health` is healthy / thirsty / wilting. */
data class Plant(
    val id: String, val name: String, val icon: String?, val plant: String, val stage: Int, val stageTitle: String,
    val health: String, val growth: Double, val nextAt: Double?, val nextPlant: String?,
) {
    val stageDe: String get() = STAGES_DE[stageTitle] ?: stageTitle

    companion object {
        private val STAGES_DE = mapOf("Seed" to tr("Samen"), "Sprout" to tr("Keimling"), "Seedling" to tr("Pflänzchen"), "Bush" to tr("Busch"),
            "In bloom" to tr("In Blüte"), "Tree" to tr("Baum"), "Mighty tree" to tr("Mächtiger Baum"))
    }
}

/** A weekly challenge (server `habit_rewards.challenges`). */
data class Challenge(
    val kind: String, val habit: String?, val routine: String?, val target: Int, val progress: Int, val bonus: Int,
    val done: Boolean,
) {
    val text: String get() = when (kind) {
        "habit" -> tr("{0} diese Woche {1}×", habit, target)
        "perfect" -> if (target == 1) tr("Ein perfekter Tag diese Woche") else tr("{0} perfekte Tage diese Woche", target)
        "routine" -> tr("Routine „{0}“ diese Woche {1}×", routine, target)
        else -> tr("Diese Woche {0} Flammen sammeln", target)
    }
}

data class StreakRecord(val name: String, val icon: String?, val streak: Int, val unit: String) {
    val label: String get() = "${icon?.let { "${it} " } ?: ""}${name}"
    val span: String get() = if (unit == "weeks") "${streak} ${if (streak == 1) tr("Woche") else tr("Wochen")}" else "${streak} ${if (streak == 1) tr("Tag") else tr("Tage")}"
}

data class Habit(
    val id: String,
    val name: String,
    val icon: String? = null,
    val kind: String,
    val unit: String?,
    val target: Double?,
    val automatic: Boolean,
    val streak: Int,
    val streakUnit: String,
    val value: Double?,
    val done: Boolean?,
    /** Automatic habits on clock metrics: the measured time ("22:07") and minutes outside the window. */
    val display: String? = null,
    val delta: Double? = null,
    /** e.g. no caffeinated drink that day: nothing to be late with. */
    val none: Boolean = false,
    /** Jokers left: each bridges one missed day of the streak. */
    val jokers: Int = 0,
    /** Two flames per done day. */
    val hard: Boolean = false,
) {
    /** Same rule as the server: check = logged at least once, count = target reached. */
    fun withValue(v: Double) = copy(value = v, done = if (kind == "count") v >= (target ?: 1.0) else v >= 1)
}

/** A write waiting for the server. Replayed in order; every operation is idempotent. */
sealed class PendingOp {
    abstract val opId: String

    /** The day the op belongs to; null for deletions, which apply to whatever day holds the entry. */
    val opDay: String? get() = when (this) {
        is AddCheckin -> day
        is AddDrink -> day
        is UpdateDrink -> day
        is LogHabit -> day
        is LogSupplement -> day
        is AddFoodEntry -> entry.day
        is UpdateFoodEntry -> day
        is AddBodyEntry -> day
        is DeleteCheckin, is DeleteDrink, is DeleteFoodEntry, is AddFood, is UpdateFood, is DeleteFood, is AddTemplate,
        is UpdateTemplate, is DeleteTemplate, is AddWorkout, is Unknown -> null
    }

    /** Food diary and catalog writes (the diary view reloads after they were sent). */
    val isFood: Boolean get() = this is AddFood || this is UpdateFood || this is DeleteFood || this is AddFoodEntry ||
        this is UpdateFoodEntry || this is DeleteFoodEntry || this is AddTemplate || this is UpdateTemplate || this is DeleteTemplate

    data class AddCheckin(override val opId: String, val checkin: Checkin, val day: String) : PendingOp()
    data class DeleteCheckin(override val opId: String, val checkinId: String) : PendingOp()
    data class AddDrink(override val opId: String, val drink: Drink, val day: String) : PendingOp()
    data class DeleteDrink(override val opId: String, val drinkId: String) : PendingOp()
    data class UpdateDrink(override val opId: String, val drink: Drink, val day: String) : PendingOp()
    data class LogHabit(override val opId: String, val habitId: String, val day: String, val value: Double) : PendingOp()
    data class LogSupplement(override val opId: String, val supplementId: String, val day: String, val count: Int) : PendingOp()

    /** A food created in the app (offline too); sent before the entries that use it (FIFO). */
    data class AddFood(override val opId: String, val food: Food) : PendingOp()
    data class UpdateFood(override val opId: String, val food: Food) : PendingOp()
    data class AddFoodEntry(override val opId: String, val entry: FoodEntryIn) : PendingOp()
    /** Deleted on the server when never used, else archived there. */
    data class DeleteFood(override val opId: String, val foodId: String) : PendingOp()
    data class AddTemplate(override val opId: String, val template: MealTemplate) : PendingOp()
    data class UpdateTemplate(override val opId: String, val template: MealTemplate) : PendingOp()
    data class DeleteTemplate(override val opId: String, val templateId: String) : PendingOp()

    /** The full corrected entry; without a new food the server scales the entry's snapshot. A quick
     *  entry can get a new [name] and [nutrients] (per piece). */
    data class UpdateFoodEntry(
        override val opId: String, val id: String, val day: String, val loggedAt: String, val mealIndex: Int,
        val amount: Double?, val quantity: Double, val name: String? = null, val nutrients: Map<String, Double>? = null,
        /** Take the food's current nutrients (the food was just corrected). */
        val recompute: Boolean = false,
    ) : PendingOp()
    data class DeleteFoodEntry(override val opId: String, val id: String) : PendingOp()
    data class AddBodyEntry(
        override val opId: String, val id: String, val ts: String, val day: String, val metric: String, val value: Double,
    ) : PendingOp()

    /** A workout logged in the app: the body of `POST /training/sessions` (its `manual_key` makes resending safe). */
    data class AddWorkout(override val opId: String, val body: String, val title: String) : PendingOp()

    /**
     * An entry this app version cannot read (unknown type, e.g. after a downgrade, or damaged). Kept as
     * it was written so nothing is lost; it is never sent and never shown.
     */
    data class Unknown(override val opId: String, val raw: String) : PendingOp()

    fun toJson(): JSONObject = when (this) {
        is AddCheckin -> JSONObject().put("type", "add").put("op", opId).put("day", day)
            .put("id", checkin.id).put("ts", checkin.ts).put("kind", checkin.kind)
            .put("value", checkin.value ?: JSONObject.NULL).put("label", checkin.label ?: JSONObject.NULL)
            .put("note", checkin.note ?: JSONObject.NULL)
        is DeleteCheckin -> JSONObject().put("type", "delete").put("op", opId).put("id", checkinId)
        is AddDrink -> JSONObject().put("type", "drink").put("op", opId).put("day", day)
            .put("id", drink.id).put("ts", drink.ts).put("drink", drink.drink).put("ml", drink.ml).put("caffeine", drink.caffeine)
        is DeleteDrink -> JSONObject().put("type", "delete_drink").put("op", opId).put("id", drinkId)
        is UpdateDrink -> JSONObject().put("type", "update_drink").put("op", opId).put("day", day)
            .put("id", drink.id).put("ts", drink.ts).put("drink", drink.drink).put("ml", drink.ml).put("caffeine", drink.caffeine)
        is LogSupplement -> JSONObject().put("type", "supplement").put("op", opId).put("supplement", supplementId)
            .put("day", day).put("count", count)
        is LogHabit -> JSONObject().put("type", "habit").put("op", opId).put("habit", habitId).put("day", day).put("value", value)
        is AddFood -> JSONObject().put("type", "food_add").put("op", opId).put("food", food.toJson())
        is UpdateFood -> JSONObject().put("type", "food_update").put("op", opId).put("food", food.toJson())
        is DeleteFood -> JSONObject().put("type", "food_delete").put("op", opId).put("id", foodId)
        is AddFoodEntry -> JSONObject().put("type", "food_entry_add").put("op", opId).put("entry", entry.toJson())
        is UpdateFoodEntry -> JSONObject().put("type", "food_entry_update").put("op", opId).put("id", id).put("day", day)
            .put("logged_at", loggedAt).put("meal_index", mealIndex).put("amount", amount ?: JSONObject.NULL).put("quantity", quantity)
            .apply {
                if (name != null) put("name", name)
                if (nutrients != null) put("nutrients", JSONObject(nutrients as Map<*, *>))
                if (recompute) put("recompute", true)
            }
        is DeleteFoodEntry -> JSONObject().put("type", "food_entry_delete").put("op", opId).put("id", id)
        is AddTemplate -> JSONObject().put("type", "template_add").put("op", opId).put("template", template.toJson())
        is UpdateTemplate -> JSONObject().put("type", "template_update").put("op", opId).put("template", template.toJson())
        is DeleteTemplate -> JSONObject().put("type", "template_delete").put("op", opId).put("id", templateId)
        is AddBodyEntry -> JSONObject().put("type", "body_add").put("op", opId).put("id", id).put("ts", ts).put("day", day)
            .put("metric", metric).put("value", value)
        is AddWorkout -> JSONObject().put("type", "workout_add").put("op", opId).put("body", JSONObject(body)).put("title", title)
        is Unknown -> JSONObject(raw)
    }

    companion object {
        /** Never throws: an entry that cannot be read becomes [Unknown] instead of emptying the outbox. */
        fun fromJson(o: JSONObject): PendingOp = runCatching { parse(o) }.getOrNull()
            ?: Unknown(o.optStringOrNull("op") ?: "unknown-${o.toString().hashCode()}", o.toString())

        private fun parse(o: JSONObject): PendingOp? = when (o.getString("type")) {
            "add" -> AddCheckin(o.getString("op"), Checkin(o.getString("id"), o.getString("ts"), o.getString("kind"),
                o.optDoubleOrNull("value"), o.optStringOrNull("label"), o.optStringOrNull("note")), o.getString("day"))
            "delete" -> DeleteCheckin(o.getString("op"), o.getString("id"))
            "drink" -> AddDrink(o.getString("op"), o.drink(), o.getString("day"))
            "delete_drink" -> DeleteDrink(o.getString("op"), o.getString("id"))
            "update_drink" -> UpdateDrink(o.getString("op"), o.drink(), o.getString("day"))
            "supplement" -> LogSupplement(o.getString("op"), o.getString("supplement"), o.getString("day"), o.getInt("count"))
            "habit" -> LogHabit(o.getString("op"), o.getString("habit"), o.getString("day"), o.getDouble("value"))
            "food_add" -> AddFood(o.getString("op"), Food.parse(o.getJSONObject("food")))
            "food_update" -> UpdateFood(o.getString("op"), Food.parse(o.getJSONObject("food")))
            "food_delete" -> DeleteFood(o.getString("op"), o.getString("id"))
            "food_entry_add" -> AddFoodEntry(o.getString("op"), FoodEntryIn.parse(o.getJSONObject("entry")))
            "food_entry_update" -> UpdateFoodEntry(o.getString("op"), o.getString("id"), o.getString("day"),
                o.getString("logged_at"), o.getInt("meal_index"), o.optDoubleOrNull("amount"), o.getDouble("quantity"),
                o.optStringOrNull("name"), o.optJSONObject("nutrients")?.numbers(), o.optBoolean("recompute"))
            "food_entry_delete" -> DeleteFoodEntry(o.getString("op"), o.getString("id"))
            "template_add" -> AddTemplate(o.getString("op"), MealTemplate.parse(o.getJSONObject("template")))
            "template_update" -> UpdateTemplate(o.getString("op"), MealTemplate.parse(o.getJSONObject("template")))
            "template_delete" -> DeleteTemplate(o.getString("op"), o.getString("id"))
            "body_add" -> AddBodyEntry(o.getString("op"), o.getString("id"), o.getString("ts"), o.getString("day"),
                o.getString("metric"), o.getDouble("value"))
            "workout_add" -> AddWorkout(o.getString("op"), o.getJSONObject("body").toString(), o.optString("title"))
            else -> null
        }

        /** Parses a stored outbox array entry by entry (one bad entry no longer loses the rest). */
        fun listFromJson(a: JSONArray): List<PendingOp> = (0 until a.length()).mapNotNull { i -> a.optJSONObject(i)?.let { fromJson(it) } }
    }
}

private fun JSONObject.drink() = getString("drink").let { kind ->
    Drink(getString("id"), getString("ts"), kind, getDouble("ml"), optBooleanOrNull("caffeine") ?: caffeinatedByDefault(kind))
}

/** Server state plus local edits still in the outbox, so the UI shows what the user just did. */
fun TodayData.withPending(ops: List<PendingOp>): TodayData = ops.fold(this) { d, op ->
    when (op) {
        is PendingOp.AddCheckin -> when {
            op.day != d.day -> d
            op.checkin.kind == "water" -> d.addDrink(op.checkin.asDrink()) // queued by app ≤ 0.2
            else ->
                if (d.checkins.any { it.id == op.checkin.id }) d else d.copy(checkins = (d.checkins + op.checkin).sortedBy { it.ts })
        }
        is PendingOp.DeleteCheckin -> d.copy(
            drinks = d.drinks.filterNot { it.id == op.checkinId },
            checkins = d.checkins.filterNot { it.id == op.checkinId },
        )
        is PendingOp.AddDrink -> if (op.day != d.day) d else d.addDrink(op.drink)
        is PendingOp.DeleteDrink -> d.copy(drinks = d.drinks.filterNot { it.id == op.drinkId })
        is PendingOp.UpdateDrink -> d.copy(drinks = d.drinks.map { if (it.id == op.drink.id) op.drink else it })
        is PendingOp.LogSupplement -> if (op.day != d.day) d
            else d.copy(supplements = d.supplements.map { if (it.id == op.supplementId) it.withCount(op.count) else it })
        is PendingOp.LogHabit ->
            if (op.day != d.day) d else d.copy(habits = d.habits.map { if (it.id == op.habitId) it.withValue(op.value) else it })
        is PendingOp.AddBodyEntry -> if (op.day != d.day) d else d.metrics[op.metric]?.let { m ->
            d.copy(metrics = d.metrics + (op.metric to m.copy(value = op.value)))
        } ?: d
        is PendingOp.Unknown, is PendingOp.AddFood, is PendingOp.UpdateFood, is PendingOp.DeleteFood, is PendingOp.AddFoodEntry,
        is PendingOp.UpdateFoodEntry, is PendingOp.DeleteFoodEntry, is PendingOp.AddTemplate, is PendingOp.UpdateTemplate,
        is PendingOp.DeleteTemplate, is PendingOp.AddWorkout -> d
    }
}

private fun TodayData.addDrink(x: Drink) = if (drinks.any { it.id == x.id }) this else copy(drinks = (drinks + x).sortedBy { it.ts })

private fun Checkin.asDrink() = Drink(id, ts, label ?: "water", value ?: 0.0)

/** Timestamp for a new entry: now for today, local noon for another day (as the WebUI does). */
fun timestampFor(day: String, zone: ZoneId = ZoneId.systemDefault()): String {
    val d = LocalDate.parse(day)
    return if (d == LocalDate.now(zone)) OffsetDateTime.now(zone).toString()
    else d.atTime(12, 0).atZone(zone).toOffsetDateTime().toString()
}

internal fun JSONArray?.objects(): List<JSONObject> = if (this == null) emptyList() else List(length()) { getJSONObject(it) }
internal fun JSONObject.optStringOrNull(key: String): String? = if (!has(key) || isNull(key)) null else optString(key)
internal fun JSONObject.optDoubleOrNull(key: String): Double? = if (!has(key) || isNull(key)) null else optDouble(key)
internal fun JSONObject.optIntOrNull(key: String): Int? = if (!has(key) || isNull(key)) null else optInt(key)
internal fun JSONObject.optBooleanOrNull(key: String): Boolean? = if (!has(key) || isNull(key)) null else optBoolean(key)

/** A day's burned energy; `source` is watch, estimate (profile + workouts) or manual (own daily burn). */
data class Energy(val total: Double, val rest: Double, val active: Double, val source: String) {
    val estimated: Boolean get() = source != "watch"
}
