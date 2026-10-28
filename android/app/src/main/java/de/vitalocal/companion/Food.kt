package de.vitalocal.companion

import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId

/** Energy and the three macros: the diary's headline numbers (server `MAIN_KEYS`). */
val MAIN_NUTRIENTS = listOf("calories", "proteins", "carbohydrates", "fat")

data class Portion(val label: String, val amount: Double)

/** An ingredient of a dish; [amount] in the ingredient's base unit. */
data class Component(val foodId: String, val name: String, val amount: Double, val unit: String? = null)

/** A nutrient of the catalog (server `/nutrients`), in the order of a nutrition label. */
data class NutrientDef(val key: String, val label: String, val unit: String)

/** The editors' default fields, in the order of a nutrition label. */
val FORM_NUTRIENTS = listOf(
    NutrientDef("calories", tr("Energie"), "kcal"), NutrientDef("fat", tr("Fett"), "g"),
    NutrientDef("saturated-fat", tr("Ges. Fettsäuren"), "g"), NutrientDef("carbohydrates", tr("Kohlenhydrate"), "g"),
    NutrientDef("sugars", tr("Zucker"), "g"), NutrientDef("fiber", tr("Ballaststoffe"), "g"),
    NutrientDef("proteins", tr("Eiweiß"), "g"), NutrientDef("salt", tr("Salz"), "g"),
)

/** Everything else the server knows (folded away in the editors). */
val MORE_NUTRIENTS = listOf(
    NutrientDef("kilojoules", tr("Energie (kJ)"), "kJ"),
    NutrientDef("monounsaturated-fat", tr("Einfach unges. Fettsäuren"), "g"),
    NutrientDef("polyunsaturated-fat", tr("Mehrfach unges. Fettsäuren"), "g"),
    NutrientDef("omega-3-fat", tr("Omega-3-Fettsäuren"), "g"), NutrientDef("trans-fat", tr("Transfette"), "g"),
    NutrientDef("cholesterol", tr("Cholesterin"), "mg"), NutrientDef("sodium", tr("Natrium"), "mg"),
    NutrientDef("caffeine", tr("Koffein"), "mg"), NutrientDef("alcohol", tr("Alkohol"), "g"),
    NutrientDef("potassium", tr("Kalium"), "mg"), NutrientDef("calcium", tr("Calcium"), "mg"),
    NutrientDef("magnesium", tr("Magnesium"), "mg"), NutrientDef("iron", tr("Eisen"), "mg"), NutrientDef("zinc", tr("Zink"), "mg"),
    NutrientDef("phosphorus", tr("Phosphor"), "mg"), NutrientDef("copper", tr("Kupfer"), "mg"), NutrientDef("manganese", tr("Mangan"), "mg"),
    NutrientDef("selenium", tr("Selen"), "µg"), NutrientDef("vitamin-a", tr("Vitamin A"), "µg"), NutrientDef("vitamin-b1", tr("Vitamin B1"), "mg"),
    NutrientDef("vitamin-b2", tr("Vitamin B2"), "mg"), NutrientDef("vitamin-pp", tr("Niacin (B3)"), "mg"),
    NutrientDef("pantothenic-acid", tr("Pantothensäure (B5)"), "mg"), NutrientDef("vitamin-b6", tr("Vitamin B6"), "mg"),
    NutrientDef("biotin", tr("Biotin (B7)"), "µg"), NutrientDef("vitamin-b9", tr("Folat (B9)"), "µg"),
    NutrientDef("vitamin-b12", tr("Vitamin B12"), "µg"), NutrientDef("vitamin-c", tr("Vitamin C"), "mg"),
    NutrientDef("vitamin-d", tr("Vitamin D"), "µg"), NutrientDef("vitamin-e", tr("Vitamin E"), "mg"), NutrientDef("vitamin-k", tr("Vitamin K"), "µg"),
)

/** What a food was last logged with (server `GET /foods/usage`): the next entry's suggestion. */
data class FoodUsage(val lastAmount: Double?, val lastQuantity: Double?, val count: Int = 0, val lastUsed: String? = null)

/** A catalog food (server `GET /foods`); nutrients are per [baseAmount] of [baseUnit]. */
data class Food(
    val id: String,
    val name: String,
    val brand: String? = null,
    val barcode: String? = null,
    val baseAmount: Double = 100.0,
    val baseUnit: String = "g",
    val portions: List<Portion> = emptyList(),
    val nutrients: Map<String, Double> = emptyMap(),
    val source: String = "vitalocal",
    val favorite: Boolean = false,
    val archived: Boolean = false,
    val dish: Boolean = false,
    val updatedAt: String? = null,
    val uses: Int = 0,
    val lastUsed: String? = null,
    val lastAmount: Double? = null,
    val lastQuantity: Double? = null,
    /** A dish's ingredients; null when not known here (the server keeps them). */
    val components: List<Component>? = null,
) {
    /** Same rule as the server: nutrients × amount / base amount × quantity. */
    fun nutrientsFor(amount: Double, quantity: Double): Map<String, Double> = scaled(nutrients, amount / baseAmount * quantity)

    val unitLabel: String get() = unitLabel(baseUnit)

    /** The body of `POST /foods` / `PUT /foods/{id}`; without known [components] a dish keeps its own on the server. */
    fun toApiJson(): JSONObject = JSONObject()
        .put("id", id).put("name", name).put("brand", brand ?: JSONObject.NULL).put("barcode", barcode ?: JSONObject.NULL)
        .put("base_amount", baseAmount).put("base_unit", baseUnit)
        .put("portions", JSONArray().apply { portions.forEach { put(JSONObject().put("label", it.label).put("amount", it.amount)) } })
        .put("nutrients", JSONObject(nutrients as Map<*, *>))
        .put("favorite", favorite).put("archived", archived).apply {
            if (dish && !components.isNullOrEmpty()) {
                put("components", JSONArray().apply { components.forEach { put(JSONObject().put("food_id", it.foodId).put("amount", it.amount)) } })
            }
        }

    /** Local cache form (API fields plus what the list shows). */
    fun toJson(): JSONObject = toApiJson()
        .put("components", JSONArray().apply {
            components.orEmpty().forEach {
                put(JSONObject().put("food_id", it.foodId).put("name", it.name).put("amount", it.amount).put("unit", it.unit ?: JSONObject.NULL))
            }
        })
        .put("source", source).put("dish", dish).put("updated_at", updatedAt ?: JSONObject.NULL)
        .put("usage", JSONObject().put("count", uses).put("last_used", lastUsed ?: JSONObject.NULL)
            .put("last_amount", lastAmount ?: JSONObject.NULL).put("last_quantity", lastQuantity ?: JSONObject.NULL))

    companion object {
        fun parse(o: JSONObject): Food {
            val usage = o.optJSONObject("usage")
            return Food(
                id = o.getString("id"),
                name = o.getString("name"),
                brand = o.optStringOrNull("brand"),
                barcode = o.optStringOrNull("barcode"),
                baseAmount = o.optDouble("base_amount", 100.0),
                baseUnit = o.optString("base_unit", "g"),
                portions = o.optJSONArray("portions").objects().map { Portion(it.getString("label"), it.getDouble("amount")) },
                nutrients = o.optJSONObject("nutrients").numbers(),
                source = o.optString("source", "vitalocal"),
                favorite = o.optBoolean("favorite"),
                archived = o.optBoolean("archived"),
                dish = o.optBoolean("dish") || (o.optJSONArray("components")?.length() ?: 0) > 0,
                updatedAt = o.optStringOrNull("updated_at"),
                uses = usage?.optInt("count") ?: 0,
                lastUsed = usage?.optStringOrNull("last_used"),
                lastAmount = usage?.optDoubleOrNull("last_amount"),
                lastQuantity = usage?.optDoubleOrNull("last_quantity"),
                components = o.optJSONArray("components")?.objects()?.map {
                    Component(it.getString("food_id"), it.optString("name"), it.getDouble("amount"), it.optStringOrNull("unit"))
                }?.takeIf { it.isNotEmpty() },
            )
        }
    }
}

/** One diary entry (server `GET /nutrition/{day}`), or one still in the outbox ([pending]). */
data class FoodEntry(
    val id: String,
    val day: String,
    val loggedAt: String?,
    val mealIndex: Int,
    val foodId: String?,
    val name: String,
    val brand: String?,
    val amount: Double?,
    val unit: String?,
    val quantity: Double,
    val nutrients: Map<String, Double>,
    val source: String = "vitalocal",
    val editable: Boolean = true,
    val pending: Boolean = false,
) {
    val kcal: Double? get() = nutrients["calories"]

    /** "150 g", "2 × 1 Portion" */
    val amountText: String get() = when {
        amount == null -> if (quantity != 1.0) "${fmt(quantity)} ×" else ""
        quantity == 1.0 -> "${fmt(amount)} ${unitLabel(unit)}"
        else -> "${fmt(quantity)} × ${fmt(amount)} ${unitLabel(unit)}"
    }

    companion object {
        fun parse(o: JSONObject) = FoodEntry(
            id = o.getString("id"),
            day = o.getString("day"),
            loggedAt = o.optStringOrNull("logged_at"),
            mealIndex = o.optIntOrNull("meal_index") ?: 0,
            foodId = o.optStringOrNull("food_id"),
            name = o.getString("name"),
            brand = o.optStringOrNull("brand"),
            amount = o.optDoubleOrNull("amount"),
            unit = o.optStringOrNull("unit"),
            quantity = o.optDoubleOrNull("quantity") ?: 1.0,
            nutrients = o.optJSONObject("nutrients").numbers(),
            source = o.optString("source", "vitalocal"),
            editable = o.optBoolean("editable", true),
        )
    }
}

/** A nutrient goal valid on the day (server `goals_of_day`, nutrients only). */
data class NutrientGoal(val metric: String, val label: String, val unit: String?, val min: Double?, val max: Double?)

/** The metric of each main nutrient, for its goal. */
val NUTRIENT_METRIC = mapOf(
    "calories" to "nutrition.energy", "proteins" to "nutrition.protein",
    "carbohydrates" to "nutrition.carbohydrates", "fat" to "nutrition.fat",
    "saturated-fat" to "nutrition.saturated_fat", "sugars" to "nutrition.sugars",
    "fiber" to "nutrition.fiber", "salt" to "nutrition.salt",
)

/** Metrics a goal can be set for (server `GOAL_METRICS`), with label and unit. */
val GOAL_METRICS = listOf(
    Triple("nutrition.energy", tr("Energie"), "kcal"), Triple("nutrition.fat", tr("Fett"), "g"),
    Triple("nutrition.saturated_fat", tr("Ges. Fettsäuren"), "g"), Triple("nutrition.carbohydrates", tr("Kohlenhydrate"), "g"),
    Triple("nutrition.sugars", tr("Zucker"), "g"), Triple("nutrition.fiber", tr("Ballaststoffe"), "g"),
    Triple("nutrition.protein", tr("Eiweiß"), "g"), Triple("nutrition.salt", tr("Salz"), "g"),
    Triple("nutrition.caffeine", tr("Koffein"), "mg"), Triple("nutrition.alcohol", tr("Alkohol"), "g"),
)

val NUTRIENT_LABEL = mapOf("calories" to tr("Energie"), "proteins" to tr("Eiweiß"), "carbohydrates" to tr("Kohlenhydrate"), "fat" to tr("Fett"))

/** Diary of one day: server state plus edits still in the outbox. */
data class NutritionDay(
    val day: String,
    val meals: List<String>,
    val entries: List<FoodEntry>,
    val goals: List<NutrientGoal>,
    val fetchedAt: Long,
) {
    /** In the order they were added (the server's order; entries still waiting come last). */
    fun mealEntries(index: Int) = entries.filter { it.mealIndex == index }

    /** Sum over the entries (an entry without the nutrient counts as 0, as on the server), null
     *  without entries; `missing` = entries that do not declare it. */
    fun total(key: String, of: List<FoodEntry> = entries): Pair<Double?, Int> {
        val have = of.mapNotNull { it.nutrients[key] }
        return (if (of.isEmpty()) null else have.sum()) to (of.size - have.size)
    }

    fun goal(key: String): NutrientGoal? = goals.firstOrNull { it.metric == NUTRIENT_METRIC[key] }

    companion object {
        val DEFAULT_MEALS = listOf(tr("Frühstück"), tr("Mittagessen"), tr("Abendessen"), tr("Snacks"))

        fun empty(day: String, meals: List<String> = DEFAULT_MEALS) = NutritionDay(day, meals, emptyList(), emptyList(), 0)

        fun parse(o: JSONObject, fetchedAt: Long): NutritionDay {
            val meals = o.optJSONArray("meals").objects()
            return NutritionDay(
                day = o.getString("day"),
                meals = meals.filter { !it.isNull("index") }.sortedBy { it.getInt("index") }.map { it.getString("name") },
                entries = meals.flatMap { m -> m.optJSONArray("entries").objects().map { FoodEntry.parse(it) } },
                goals = o.optJSONArray("goals").objects().map {
                    NutrientGoal(it.getString("metric"), it.optString("label"), it.optStringOrNull("unit"),
                        it.optDoubleOrNull("min_value"), it.optDoubleOrNull("max_value"))
                },
                fetchedAt = fetchedAt,
            )
        }
    }
}

/** Server state plus local food edits still in the outbox. Foods come from the local catalog. */
fun NutritionDay.withPending(ops: List<PendingOp>, foods: (String) -> Food?): NutritionDay = ops.fold(this) { d, op ->
    when (op) {
        is PendingOp.AddFoodEntry -> if (op.entry.day != d.day || d.entries.any { it.id == op.entry.id }) d
            else d.copy(entries = d.entries + op.entry.toEntry(foods))
        is PendingOp.UpdateFoodEntry -> d.copy(entries = d.entries.mapNotNull { e ->
            when {
                e.id != op.id -> e
                op.day != d.day -> null // moved to another day: shown there after its reload
                else -> e.updatedBy(op).let { u ->
                    // the corrected food's values, as the server will compute them
                    val f = if (op.recompute) e.foodId?.let(foods) else null
                    if (f != null && u.amount != null) u.copy(nutrients = f.nutrientsFor(u.amount, u.quantity)) else u
                }
            }
        })
        is PendingOp.DeleteFoodEntry -> d.copy(entries = d.entries.filterNot { it.id == op.id })
        else -> d
    }
}

/** A correction of an entry: amount and count scale its snapshot, as the server does; a quick entry
 *  may get new values (per piece, × count). */
fun FoodEntry.updatedBy(op: PendingOp.UpdateFoodEntry): FoodEntry {
    val oldTotal = (amount ?: 1.0) * quantity
    val newAmount = if (amount != null) op.amount ?: amount else null
    val newTotal = (newAmount ?: 1.0) * op.quantity
    return copy(
        day = op.day, loggedAt = op.loggedAt, mealIndex = op.mealIndex, amount = newAmount, quantity = op.quantity,
        name = op.name ?: name,
        nutrients = op.nutrients?.let { scaled(it, op.quantity) } ?: scaled(nutrients, if (oldTotal > 0) newTotal / oldTotal else 1.0),
        pending = true,
    )
}

/** One food of a meal template; amount in the food's base unit. */
data class TemplateItem(
    val foodId: String,
    val name: String,
    val amount: Double,
    val quantity: Double = 1.0,
    val unit: String? = null,
    val nutrients: Map<String, Double> = emptyMap(),
)

/** A meal template (server `GET /templates`): logged as one entry per food. */
data class MealTemplate(
    val id: String,
    val name: String,
    val items: List<TemplateItem>,
    val favorite: Boolean = false,
    val archived: Boolean = false,
) {
    val nutrients: Map<String, Double> get() = items.flatMap { it.nutrients.entries }
        .groupBy({ it.key }, { it.value }).mapValues { it.value.sum() }

    fun toApiJson(): JSONObject = JSONObject().put("id", id).put("name", name).put("favorite", favorite).put("archived", archived)
        .put("items", JSONArray().apply {
            items.forEach { put(JSONObject().put("food_id", it.foodId).put("amount", it.amount).put("quantity", it.quantity)) }
        })

    /** Cache form: API fields plus what the list shows. */
    fun toJson(): JSONObject = toApiJson().put("items", JSONArray().apply {
        items.forEach {
            put(JSONObject().put("food_id", it.foodId).put("name", it.name).put("amount", it.amount).put("quantity", it.quantity)
                .put("unit", it.unit ?: JSONObject.NULL).put("nutrients", JSONObject(it.nutrients as Map<*, *>)))
        }
    })

    companion object {
        fun parse(o: JSONObject) = MealTemplate(
            id = o.getString("id"),
            name = o.getString("name"),
            items = o.optJSONArray("items").objects().map {
                TemplateItem(it.getString("food_id"), it.optString("name"), it.getDouble("amount"),
                    it.optDoubleOrNull("quantity") ?: 1.0, it.optStringOrNull("unit"), it.optJSONObject("nutrients").numbers())
            },
            favorite = o.optBoolean("favorite"),
            archived = o.optBoolean("archived"),
        )
    }
}

/** What the app sends for a new entry (`POST /nutrition/entries`). */
data class FoodEntryIn(
    val id: String,
    val day: String,
    val loggedAt: String,
    val mealIndex: Int,
    val foodId: String? = null,
    val amount: Double? = null,
    val quantity: Double = 1.0,
    val name: String? = null,
    val nutrients: Map<String, Double>? = null,
    /** Logged from this meal template. */
    val templateId: String? = null,
) {
    fun toApiJson(): JSONObject = JSONObject().put("id", id).put("logged_at", loggedAt).put("meal_index", mealIndex)
        .put("quantity", quantity).put("entered_via", "android").apply {
            if (templateId != null) put("template_id", templateId)
            if (foodId != null) put("food_id", foodId).put("amount", amount ?: JSONObject.NULL)
            else put("name", name).put("nutrients", JSONObject((nutrients ?: emptyMap()) as Map<*, *>))
        }

    fun toJson(): JSONObject = toApiJson().put("day", day)

    /** The entry as the server will compute it (same formula), shown while it waits. */
    fun toEntry(foods: (String) -> Food?): FoodEntry {
        val food = foodId?.let(foods)
        val amt = if (foodId != null) amount ?: food?.baseAmount else null
        return FoodEntry(
            id = id, day = day, loggedAt = loggedAt, mealIndex = mealIndex, foodId = foodId,
            name = food?.name ?: name ?: "…", brand = food?.brand, amount = amt, unit = food?.baseUnit, quantity = quantity,
            nutrients = when {
                food != null && amt != null -> food.nutrientsFor(amt, quantity)
                nutrients != null -> scaled(nutrients, quantity)
                else -> emptyMap()
            },
            pending = true,
        )
    }

    companion object {
        fun parse(o: JSONObject) = FoodEntryIn(
            id = o.getString("id"),
            day = o.getString("day"),
            loggedAt = o.getString("logged_at"),
            mealIndex = o.getInt("meal_index"),
            foodId = o.optStringOrNull("food_id"),
            amount = o.optDoubleOrNull("amount"),
            quantity = o.optDoubleOrNull("quantity") ?: 1.0,
            name = o.optStringOrNull("name"),
            nutrients = o.optJSONObject("nutrients")?.numbers(),
            templateId = o.optStringOrNull("template_id"),
        )
    }
}

/** Catalog search: every word in name, brand or barcode; favorites first, then by use. */
fun searchFoods(foods: Collection<Food>, query: String, limit: Int = 30): List<Food> {
    val words = query.lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
    val live = foods.filter { !it.archived }
    if (words.isEmpty()) {
        return live.filter { it.favorite || it.lastUsed != null }
            .sortedWith(compareByDescending<Food> { it.favorite }.thenByDescending { it.lastUsed ?: "" }).take(limit)
    }
    return live.filter { f ->
        val hay = "${f.name} ${f.brand.orEmpty()}".lowercase()
        words.all { w -> w in hay || f.barcode == w }
    }.sortedWith(compareByDescending<Food> { it.favorite }.thenByDescending { it.uses }.thenBy { it.name.lowercase() }).take(limit)
}

/** A dish's nutrients: the sum of its ingredients' (same rule as the server); unknown ingredients add nothing. */
fun dishNutrients(components: List<Component>, foods: (String) -> Food?): Map<String, Double> {
    val total = LinkedHashMap<String, Double>()
    components.forEach { c ->
        val f = foods(c.foodId) ?: return@forEach
        f.nutrientsFor(c.amount, 1.0).forEach { (k, v) -> total[k] = (total[k] ?: 0.0) + v }
    }
    return total
}

/** Barcodes match without leading zeros (UPC-A vs EAN-13). */
fun sameBarcode(a: String?, b: String): Boolean = a != null && a.trimStart('0') == b.trimStart('0')

fun scaled(nutrients: Map<String, Double>, factor: Double): Map<String, Double> = nutrients.mapValues { it.value * factor }

fun unitLabel(unit: String?): String = when (unit) {
    "portion" -> tr("Portion")
    null -> ""
    else -> unit
}

/** Timestamp for an entry at [clock] ("HH:mm") on [day] in the phone's zone. */
fun entryTimestamp(day: String, clock: String, zone: ZoneId = ZoneId.systemDefault()): String {
    val (h, m) = clock.split(":").map { it.toInt() }
    return LocalDate.parse(day).atTime(h, m).atZone(zone).toOffsetDateTime().toString()
}

/** "HH:mm" of an ISO timestamp in the phone's zone. */
fun clockOf(iso: String?, zone: ZoneId = ZoneId.systemDefault()): String? = iso?.let {
    runCatching { OffsetDateTime.parse(it).atZoneSameInstant(zone).toLocalTime().toString().take(5) }.getOrNull()
}

private fun fmt(v: Double): String = if (v % 1.0 == 0.0) v.toLong().toString() else "%.1f".format(I18n.locale, v)

internal fun JSONObject?.numbers(): Map<String, Double> {
    if (this == null) return emptyMap()
    val out = LinkedHashMap<String, Double>()
    keys().forEach { k -> (opt(k) as? Number)?.let { out[k] = it.toDouble() } }
    return out
}
