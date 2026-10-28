package de.vitalocal.companion

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.time.LocalDate

data class FoodState(
    /** The server's catalog as last synced (`files/foods.json`). */
    val foods: Map<String, Food> = emptyMap(),
    val days: Map<String, NutritionDay> = emptyMap(),
    /** Favorites and recently used foods as the server last listed them (usage is not in the cache sync). */
    val recent: List<Food> = emptyList(),
    /** Meal templates as the server last listed them (`files/templates.json`). */
    val templates: List<MealTemplate> = emptyList(),
    /** Last amount per logged food as the server last listed it (`files/food_usage.json`). */
    val usage: Map<String, FoodUsage> = emptyMap(),
    /** Food writes already sent, shown until a reload of their day that started later has them. */
    val sent: List<PendingOp> = emptyList(),
    val refreshing: Boolean = false,
    val error: String? = null,
) {
    /** Catalog incl. foods created or changed in the app that the server may not have yet. */
    fun catalog(pending: List<PendingOp>): Map<String, Food> {
        val out = LinkedHashMap(foods)
        (sent + pending).forEach { op ->
            when (op) {
                is PendingOp.AddFood -> out.putIfAbsent(op.food.id, op.food)
                is PendingOp.UpdateFood -> out[op.food.id] = op.food
                is PendingOp.DeleteFood -> out[op.foodId]?.let { out[op.foodId] = it.copy(archived = true) }
                else -> Unit
            }
        }
        return out
    }

    /** Templates incl. ones saved, changed or deleted in the app that the server may not have yet. */
    fun templates(pending: List<PendingOp>, includeArchived: Boolean = false): List<MealTemplate> {
        val out = LinkedHashMap<String, MealTemplate>()
        templates.forEach { out[it.id] = it }
        (sent + pending).forEach { op ->
            when (op) {
                is PendingOp.AddTemplate -> out.putIfAbsent(op.template.id, op.template)
                is PendingOp.UpdateTemplate -> out[op.template.id] = op.template
                is PendingOp.DeleteTemplate -> out.remove(op.templateId)
                else -> Unit
            }
        }
        return out.values.filter { includeArchived || !it.archived }
            .sortedWith(compareByDescending<MealTemplate> { it.favorite }.thenBy { it.name.lowercase() })
    }

    /** The amount [foodId] was last logged with: entries made here after the server's list win. */
    fun lastUse(foodId: String, pending: List<PendingOp>): FoodUsage? {
        val local = (sent + pending).filterIsInstance<PendingOp.AddFoodEntry>().lastOrNull { it.entry.foodId == foodId }?.entry
        if (local?.amount != null) return FoodUsage(local.amount, local.quantity)
        return usage[foodId]?.takeIf { it.lastAmount != null }
    }

    fun meals(): List<String> = days.values.maxByOrNull { it.fetchedAt }?.meals ?: NutritionDay.DEFAULT_MEALS

    fun view(day: String, pending: List<PendingOp>): NutritionDay {
        val cat = catalog(pending)
        return (days[day] ?: NutritionDay.empty(day, meals())).withPending(sent + pending) { cat[it] }
    }
}

/**
 * Food diary on the phone: a local copy of the catalog (kept in step with `updated_since`), the
 * last 8 days of the diary, and the writes in [DayRepo]'s outbox on top. Works offline; the outbox
 * sends a food before the entries that use it.
 */
object FoodRepo {
    private const val KEEP_DAYS = 8
    private lateinit var dir: File
    private val _state = MutableStateFlow(FoodState())
    val state: StateFlow<FoodState> = _state.asStateFlow()
    private var since: String? = null
    private val sent = ArrayList<Pair<Long, PendingOp>>()
    private var sendSeq = 0L

    @Synchronized
    fun init(context: Context) {
        if (::dir.isInitialized) return
        dir = context.filesDir
        val foods = runCatching {
            val o = JSONObject(File(dir, "foods.json").readText())
            since = o.optStringOrNull("since")
            o.optJSONArray("foods").objects().map { Food.parse(it) }.associateBy { it.id }
        }.getOrDefault(emptyMap())
        // caches before 1.2 kept no ingredients: fetch the whole catalog once
        if (foods.values.any { it.dish && it.components == null }) since = null
        val days = runCatching {
            val o = JSONObject(File(dir, "nutrition_days.json").readText())
            o.keys().asSequence().mapNotNull { k ->
                runCatching { o.getJSONObject(k).let { k to NutritionDay.parse(it.getJSONObject("data"), it.getLong("at")) } }.getOrNull()
            }.toMap()
        }.getOrDefault(emptyMap())
        val templates = runCatching {
            JSONArray(File(dir, "templates.json").readText()).objects().map { MealTemplate.parse(it) }
        }.getOrDefault(emptyList())
        val usage = runCatching {
            val o = JSONObject(File(dir, "food_usage.json").readText())
            o.keys().asSequence().associateWith { k ->
                o.getJSONObject(k).let { FoodUsage(it.optDoubleOrNull("a"), it.optDoubleOrNull("q")) }
            }
        }.getOrDefault(emptyMap())
        _state.value = FoodState(foods = foods, days = days, templates = templates, usage = usage)
    }

    /** Called by the outbox for every food write the server accepted. */
    @Synchronized
    fun onSent(op: PendingOp) {
        sent += ++sendSeq to op
        while (sent.size > 200) sent.removeAt(0)
        _state.value = _state.value.copy(sent = sent.map { it.second })
    }

    /** Sends pending writes, syncs the catalog, reloads [day] (and the recent foods). */
    suspend fun refresh(context: Context, day: String) {
        _state.value = _state.value.copy(refreshing = true)
        try {
            DayRepo.flush(context)
            val settings = Settings(context)
            if (!settings.isConfigured) return
            withContext(Dispatchers.IO) {
                val api = Api(settings.serverUrl, settings.token)
                val seenUpTo = synchronized(this@FoodRepo) { sendSeq }
                syncCatalog(api)
                val json = api.nutritionDay(day)
                val recent = runCatching { api.recentFoods() }.getOrNull()
                val templates = runCatching { api.templates() }.getOrNull()
                val usage = runCatching { api.foodUsage() }.getOrNull()
                synchronized(this@FoodRepo) {
                    val days = (_state.value.days + (day to NutritionDay.parse(json, System.currentTimeMillis())))
                        .toList().sortedByDescending { it.first }.take(KEEP_DAYS).toMap()
                    sent.removeAll { (seq, op) -> seq <= seenUpTo && (op.opDay == null || op.opDay == day) }
                    _state.value = _state.value.copy(
                        days = days, recent = recent ?: _state.value.recent, sent = sent.map { it.second }, error = null,
                        templates = templates ?: _state.value.templates, usage = usage ?: _state.value.usage,
                    )
                    saveDays()
                    if (usage != null) {
                        val o = JSONObject()
                        usage.forEach { (k, u) -> o.put(k, JSONObject().put("a", u.lastAmount ?: JSONObject.NULL).put("q", u.lastQuantity ?: JSONObject.NULL)) }
                        writeAtomic(File(dir, "food_usage.json"), o.toString())
                    }
                    if (templates != null) {
                        writeAtomic(File(dir, "templates.json"), JSONArray().apply { templates.forEach { put(it.toJson()) } }.toString())
                    }
                }
            }
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = if (e is IOException && e !is ApiException) tr("Server nicht erreichbar") else e.describe())
        } finally {
            _state.value = _state.value.copy(refreshing = false)
        }
    }

    private fun syncCatalog(api: Api) {
        val changed = api.foodsSince(since)
        if (changed.isEmpty()) return
        synchronized(this) {
            val foods = _state.value.foods + changed.associateBy { it.id }
            since = changed.mapNotNull { it.updatedAt }.maxOrNull() ?: since
            _state.value = _state.value.copy(foods = foods)
            val o = JSONObject().put("since", since ?: JSONObject.NULL)
                .put("foods", JSONArray().apply { foods.values.forEach { put(it.toJson()) } })
            writeAtomic(File(dir, "foods.json"), o.toString())
        }
    }

    /** A barcode in the local catalog (also foods created offline). */
    fun byBarcode(code: String, pending: List<PendingOp>): Food? =
        _state.value.catalog(pending).values.filter { sameBarcode(it.barcode, code) }.minByOrNull { if (it.archived) 1 else 0 }

    /** Takes a food the server returned (barcode lookup, OFF takeover) into the local catalog. */
    @Synchronized
    fun remember(food: Food) {
        if (food.id.isEmpty()) return
        _state.value = _state.value.copy(foods = _state.value.foods + (food.id to food))
    }

    private fun saveDays() {
        val o = JSONObject()
        _state.value.days.forEach { (k, d) ->
            // the parsed form is enough to show the day offline
            o.put(k, JSONObject().put("data", d.toServerJson()).put("at", d.fetchedAt))
        }
        writeAtomic(File(dir, "nutrition_days.json"), o.toString())
    }

    private fun writeAtomic(f: File, text: String) {
        val tmp = File(f.parentFile, f.name + ".tmp")
        tmp.writeText(text)
        tmp.renameTo(f)
    }

    val today: String get() = LocalDate.now().toString()
}

/** Back to the shape of `GET /nutrition/{day}` for the offline cache. */
private fun NutritionDay.toServerJson(): JSONObject = JSONObject().put("day", day)
    .put("meals", JSONArray().apply {
        meals.forEachIndexed { i, name ->
            put(JSONObject().put("index", i).put("name", name).put("entries", JSONArray().apply {
                entries.filter { it.mealIndex == i && !it.pending }.forEach { e ->
                    put(JSONObject().put("id", e.id).put("day", e.day).put("logged_at", e.loggedAt ?: JSONObject.NULL)
                        .put("meal_index", e.mealIndex).put("food_id", e.foodId ?: JSONObject.NULL).put("name", e.name)
                        .put("brand", e.brand ?: JSONObject.NULL).put("amount", e.amount ?: JSONObject.NULL)
                        .put("unit", e.unit ?: JSONObject.NULL).put("quantity", e.quantity)
                        .put("nutrients", JSONObject(e.nutrients as Map<*, *>)).put("source", e.source).put("editable", e.editable))
                }
            }))
        }
    })
    .put("goals", JSONArray().apply {
        goals.forEach { g ->
            put(JSONObject().put("metric", g.metric).put("label", g.label).put("unit", g.unit ?: JSONObject.NULL)
                .put("min_value", g.min ?: JSONObject.NULL).put("max_value", g.max ?: JSONObject.NULL))
        }
    })
