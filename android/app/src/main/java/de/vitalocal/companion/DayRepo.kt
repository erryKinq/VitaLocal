package de.vitalocal.companion

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.time.LocalDate
import java.util.UUID
import java.util.concurrent.TimeUnit

data class DayState(
    val days: Map<String, TodayData> = emptyMap(),
    val pending: List<PendingOp> = emptyList(),
    /** Sent, but possibly not yet in the cached server state of their day (until the next reload). */
    val applied: List<PendingOp> = emptyList(),
    val symptoms: List<String> = emptyList(),
    val refreshing: Boolean = false,
    val error: String? = null,
) {
    /** What the user sees: last server state of that day plus edits still in the outbox. */
    fun view(day: String): TodayData = (days[day] ?: TodayData.empty(day)).withPending(applied + pending)
}

/**
 * Manual entries (drinks, check-ins, habit logs) and the cached day overview. Every entry goes to
 * an outbox first and is shown immediately; [OutboxWorker] sends it once the server is reachable.
 */
object DayRepo {
    private const val KEEP_DAYS = 7
    private lateinit var dir: File
    private val raw = LinkedHashMap<String, String>() // day -> /today JSON
    private val fetched = HashMap<String, Long>()
    private val _state = MutableStateFlow(DayState())
    val state: StateFlow<DayState> = _state.asStateFlow()
    private val net = Mutex()
    /** Sent ops with the send sequence number; dropped once a reload that started later has them. */
    private val sent = ArrayList<Pair<Long, PendingOp>>()
    private var sendSeq = 0L

    val today: String get() = LocalDate.now().toString()

    @Synchronized
    fun init(context: Context) {
        if (::dir.isInitialized) return
        dir = context.filesDir
        runCatching {
            val o = JSONObject(File(dir, "days.json").readText())
            o.optJSONObject("days")?.let { d ->
                d.keys().forEach { k -> d.getJSONObject(k).let { raw[k] = it.getString("data"); fetched[k] = it.getLong("at") } }
            }
            val symptoms = o.optJSONArray("symptoms")?.let { a -> List(a.length()) { a.getString(it) } } ?: emptyList()
            _state.value = _state.value.copy(symptoms = symptoms)
        }
        val pending = runCatching { PendingOp.listFromJson(JSONArray(File(dir, "outbox.json").readText())) }.getOrDefault(emptyList())
        pending.filterIsInstance<PendingOp.Unknown>().takeIf { it.isNotEmpty() }?.let {
            Store.log(Level.WARN, null, tr("{0} Einträge in der Warteschlange kann diese App-Version nicht senden, sie bleiben erhalten", it.size))
        }
        _state.value = _state.value.copy(days = parsedDays(), pending = pending)
    }

    // --- Local edits --------------------------------------------------------------------------

    fun addDrink(context: Context, day: String, ml: Double, drink: String, caffeine: Boolean = caffeinatedByDefault(drink)) {
        val x = Drink(UUID.randomUUID().toString(), timestampFor(day), drink, ml, caffeine)
        enqueue(context, PendingOp.AddDrink(UUID.randomUUID().toString(), x, day))
    }

    /** Corrects a logged drink (e.g. the widget's water was a coffee). */
    fun updateDrink(context: Context, day: String, drink: Drink) {
        synchronized(this) {
            val pending = _state.value.pending
            val localAdd = pending.firstOrNull { it is PendingOp.AddDrink && it.drink.id == drink.id }
            if (localAdd is PendingOp.AddDrink) {
                // Not on the server yet: correct the queued entry itself.
                setPending(pending.map { if (it === localAdd) localAdd.copy(drink = drink) else it })
                changed(context)
                return
            }
            // Only the latest correction counts.
            setPending(pending.filterNot { it is PendingOp.UpdateDrink && it.drink.id == drink.id })
        }
        enqueue(context, PendingOp.UpdateDrink(UUID.randomUUID().toString(), drink, day))
    }

    fun deleteDrink(context: Context, id: String) {
        synchronized(this) {
            val pending = _state.value.pending
            val localAdd = pending.firstOrNull {
                (it is PendingOp.AddDrink && it.drink.id == id) || (it is PendingOp.AddCheckin && it.checkin.id == id)
            }
            if (localAdd != null) {
                setPending(pending - localAdd)
                changed(context)
                return
            }
            setPending(pending.filterNot { it is PendingOp.UpdateDrink && it.drink.id == id })
        }
        enqueue(context, PendingOp.DeleteDrink(UUID.randomUUID().toString(), id))
    }

    fun addCheckin(context: Context, day: String, kind: String, value: Double?, label: String?, note: String?) {
        val c = Checkin(UUID.randomUUID().toString(), timestampFor(day), kind, value, label, note)
        enqueue(context, PendingOp.AddCheckin(UUID.randomUUID().toString(), c, day))
    }

    fun deleteCheckin(context: Context, id: String) {
        synchronized(this) {
            val pending = _state.value.pending
            val localAdd = pending.firstOrNull { it is PendingOp.AddCheckin && it.checkin.id == id }
            if (localAdd != null) {
                // Never reached the server: just drop it.
                setPending(pending - localAdd)
                changed(context)
                return
            }
        }
        enqueue(context, PendingOp.DeleteCheckin(UUID.randomUUID().toString(), id))
    }

    fun logHabit(context: Context, day: String, habitId: String, value: Double) {
        synchronized(this) {
            // Only the last value counts; earlier unsent logs for the same habit and day are obsolete.
            setPending(_state.value.pending.filterNot { it is PendingOp.LogHabit && it.habitId == habitId && it.day == day })
        }
        enqueue(context, PendingOp.LogHabit(UUID.randomUUID().toString(), habitId, day, value))
    }

    /** +1/−1 on a count habit, from the value shown right now (fast taps each count). */
    fun adjustHabit(context: Context, day: String, habitId: String, delta: Double) {
        val v = synchronized(this) {
            val h = _state.value.view(day).habits.firstOrNull { it.id == habitId } ?: return
            ((h.value ?: 0.0) + delta).coerceAtLeast(0.0)
        }
        logHabit(context, day, habitId, v)
    }

    fun adjustSupplement(context: Context, day: String, supplementId: String, delta: Int) {
        val n = synchronized(this) {
            val s = _state.value.view(day).supplements.firstOrNull { it.id == supplementId } ?: return
            s.count + delta
        }
        logSupplement(context, day, supplementId, n)
    }

    fun logSupplement(context: Context, day: String, supplementId: String, count: Int) {
        synchronized(this) {
            // only the last count of the day matters
            setPending(_state.value.pending.filterNot { it is PendingOp.LogSupplement && it.supplementId == supplementId && it.day == day })
        }
        enqueue(context, PendingOp.LogSupplement(UUID.randomUUID().toString(), supplementId, day, count.coerceAtLeast(0)))
    }

    // --- Food diary and body (shown by FoodRepo / the Today view) ---------------------------------

    fun addFood(context: Context, food: Food) = enqueue(context, PendingOp.AddFood(UUID.randomUUID().toString(), food))

    fun updateFood(context: Context, food: Food) {
        synchronized(this) {
            val pending = _state.value.pending
            val localAdd = pending.firstOrNull { it is PendingOp.AddFood && it.food.id == food.id }
            if (localAdd != null) {
                setPending(pending.map { if (it === localAdd) PendingOp.AddFood(it.opId, food) else it })
                changed(context)
                return
            }
            setPending(pending.filterNot { it is PendingOp.UpdateFood && it.food.id == food.id })
        }
        enqueue(context, PendingOp.UpdateFood(UUID.randomUUID().toString(), food))
    }

    /** Deleted when never used, else archived (the server decides); one not yet sent is dropped here. */
    fun deleteFood(context: Context, food: Food) {
        synchronized(this) {
            val pending = _state.value.pending
            val localAdd = pending.firstOrNull { it is PendingOp.AddFood && it.food.id == food.id }
            val used = pending.any { (it is PendingOp.AddFoodEntry && it.entry.foodId == food.id) ||
                (it is PendingOp.AddTemplate && it.template.items.any { i -> i.foodId == food.id }) ||
                (it is PendingOp.UpdateTemplate && it.template.items.any { i -> i.foodId == food.id }) ||
                ((it is PendingOp.AddFood || it is PendingOp.UpdateFood) && it.foodOf()?.components.orEmpty().any { c -> c.foodId == food.id }) }
            if (localAdd != null && !used) {
                setPending(pending.filterNot { it === localAdd || (it is PendingOp.UpdateFood && it.food.id == food.id) })
                changed(context)
                return
            }
            setPending(pending.filterNot { it is PendingOp.UpdateFood && it.food.id == food.id })
        }
        enqueue(context, PendingOp.DeleteFood(UUID.randomUUID().toString(), food.id))
    }

    private fun PendingOp.foodOf(): Food? = when (this) { is PendingOp.AddFood -> food; is PendingOp.UpdateFood -> food; else -> null }

    fun addTemplate(context: Context, template: MealTemplate) =
        enqueue(context, PendingOp.AddTemplate(UUID.randomUUID().toString(), template))

    /** The full new template; one not yet sent is changed in the outbox itself. */
    fun updateTemplate(context: Context, template: MealTemplate) {
        synchronized(this) {
            val pending = _state.value.pending
            val localAdd = pending.firstOrNull { it is PendingOp.AddTemplate && it.template.id == template.id }
            if (localAdd is PendingOp.AddTemplate) {
                setPending(pending.map { if (it === localAdd) localAdd.copy(template = template) else it })
                changed(context)
                return
            }
            setPending(pending.filterNot { it is PendingOp.UpdateTemplate && it.template.id == template.id })
        }
        enqueue(context, PendingOp.UpdateTemplate(UUID.randomUUID().toString(), template))
    }

    /** Entries logged from it stay. */
    fun deleteTemplate(context: Context, id: String) {
        synchronized(this) {
            val pending = _state.value.pending
            val localAdd = pending.firstOrNull { it is PendingOp.AddTemplate && it.template.id == id }
            // entries waiting with this template id are logged without it by the server
            if (localAdd != null) {
                setPending(pending.filterNot { it === localAdd || (it is PendingOp.UpdateTemplate && it.template.id == id) })
                changed(context)
                return
            }
            setPending(pending.filterNot { it is PendingOp.UpdateTemplate && it.template.id == id })
        }
        enqueue(context, PendingOp.DeleteTemplate(UUID.randomUUID().toString(), id))
    }

    /** A template becomes one ordinary entry per food (offline too), marked with the template. */
    fun logTemplate(context: Context, template: MealTemplate, day: String, loggedAt: String, mealIndex: Int) {
        val ops = template.items.map { i ->
            PendingOp.AddFoodEntry(UUID.randomUUID().toString(), FoodEntryIn(
                id = UUID.randomUUID().toString(), day = day, loggedAt = loggedAt, mealIndex = mealIndex,
                foodId = i.foodId, amount = i.amount, quantity = i.quantity, templateId = template.id,
            ))
        }
        synchronized(this) { setPending(_state.value.pending + ops) }
        changed(context)
        OutboxWorker.schedule(context)
    }

    fun addFoodEntry(context: Context, entry: FoodEntryIn) =
        enqueue(context, PendingOp.AddFoodEntry(UUID.randomUUID().toString(), entry))

    /** Corrects an entry; one not yet on the server is corrected in the outbox itself. */
    fun updateFoodEntry(context: Context, op: PendingOp.UpdateFoodEntry) {
        val send = synchronized(this) {
            val pending = _state.value.pending
            val localAdd = pending.firstOrNull { it is PendingOp.AddFoodEntry && it.entry.id == op.id }
            if (localAdd is PendingOp.AddFoodEntry) {
                val e = localAdd.entry
                val fixed = e.copy(day = op.day, loggedAt = op.loggedAt, mealIndex = op.mealIndex, quantity = op.quantity,
                    amount = if (e.foodId != null) op.amount ?: e.amount else null,
                    name = if (e.foodId == null) op.name ?: e.name else e.name,
                    nutrients = if (e.foodId == null) op.nutrients ?: e.nutrients else e.nutrients)
                // recomputed: behind the food's correction, so the server computes it from the new values
                setPending(if (op.recompute) pending.filterNot { it === localAdd } + localAdd.copy(entry = fixed)
                    else pending.map { if (it === localAdd) localAdd.copy(entry = fixed) else it })
                changed(context)
                return
            }
            // only the latest correction counts (a recomputation stays one)
            val recompute = op.recompute || pending.any { it is PendingOp.UpdateFoodEntry && it.id == op.id && it.recompute }
            setPending(pending.filterNot { it is PendingOp.UpdateFoodEntry && it.id == op.id })
            op.copy(recompute = recompute)
        }
        enqueue(context, send)
    }

    fun deleteFoodEntry(context: Context, id: String) {
        synchronized(this) {
            val pending = _state.value.pending
            val localAdd = pending.firstOrNull { it is PendingOp.AddFoodEntry && it.entry.id == id }
            if (localAdd != null) {
                setPending(pending.filterNot { it === localAdd || (it is PendingOp.UpdateFoodEntry && it.id == id) })
                changed(context)
                return
            }
            setPending(pending.filterNot { it is PendingOp.UpdateFoodEntry && it.id == id })
        }
        enqueue(context, PendingOp.DeleteFoodEntry(UUID.randomUUID().toString(), id))
    }

    fun addBody(context: Context, day: String, metric: String, value: Double) {
        val id = UUID.randomUUID().toString()
        enqueue(context, PendingOp.AddBodyEntry(UUID.randomUUID().toString(), id, timestampFor(day), day, metric, value))
    }

    /** A workout logged in the app; sent with the other edits (offline too). */
    fun addWorkout(context: Context, body: JSONObject, title: String) =
        enqueue(context, PendingOp.AddWorkout(UUID.randomUUID().toString(), body.toString(), title))

    private fun enqueue(context: Context, op: PendingOp) {
        synchronized(this) { setPending(_state.value.pending + op) }
        changed(context)
        OutboxWorker.schedule(context)
    }

    // --- Server ---------------------------------------------------------------------------------

    /** Sends queued edits in order. True when nothing sendable is left ([PendingOp.Unknown] entries stay). */
    suspend fun flush(context: Context): Boolean = net.withLock {
        withContext(Dispatchers.IO) {
            val settings = Settings(context)
            if (!settings.isConfigured) return@withContext false
            val api = Api(settings.serverUrl, settings.token)
            while (true) {
                val op = _state.value.pending.firstOrNull { it !is PendingOp.Unknown } ?: return@withContext true
                val code = try {
                    api.send(op)
                } catch (e: IOException) {
                    return@withContext false
                }
                when {
                    code in 200..299 -> Unit
                    code == 401 || code == 408 || code == 429 || code >= 500 -> return@withContext false
                    else -> Store.log(Level.ERROR, null, tr("Eintrag vom Server abgelehnt (HTTP {0}), verworfen: {1}", code, describeOp(op)))
                }
                synchronized(this@DayRepo) {
                    // keep showing the change until a reload that started after this point has it
                    if (code in 200..299) {
                        sent += ++sendSeq to op
                        while (sent.size > 200) sent.removeAt(0)
                        if (op.isFood) FoodRepo.onSent(op)
                    }
                    _state.value = _state.value.copy(applied = sent.map { it.second })
                    setPending(_state.value.pending.filterNot { it.opId == op.opId })
                }
                changed(context)
            }
            @Suppress("UNREACHABLE_CODE")
            true
        }
    }

    /** Sends pending edits, then reloads [day] from the server. */
    suspend fun refresh(context: Context, day: String = today) {
        _state.value = _state.value.copy(refreshing = true)
        try {
            flush(context)
            val settings = Settings(context)
            if (!settings.isConfigured) return
            withContext(Dispatchers.IO) {
                val api = Api(settings.serverUrl, settings.token)
                val seenUpTo = synchronized(this@DayRepo) { sendSeq }
                val json = api.today(day)
                val symptoms = runCatching { api.symptoms() }.getOrNull()
                Caps.refresh(context, api)
                synchronized(this@DayRepo) {
                    raw[day] = json.toString()
                    fetched[day] = System.currentTimeMillis()
                    while (raw.size > KEEP_DAYS) raw.keys.minOrNull()?.let { raw.remove(it); fetched.remove(it) }
                    sent.removeAll { (seq, op) -> seq <= seenUpTo && (op.opDay == null || op.opDay == day) }
                    _state.value = _state.value.copy(
                        applied = sent.map { it.second },
                        days = parsedDays(),
                        symptoms = symptoms ?: _state.value.symptoms,
                        error = null,
                    )
                    saveDays()
                }
            }
            changed(context)
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = if (e is IOException && e !is ApiException) tr("Server nicht erreichbar") else e.describe())
        } finally {
            _state.value = _state.value.copy(refreshing = false)
        }
    }

    // --- Persistence ------------------------------------------------------------------------------

    private fun parsedDays(): Map<String, TodayData> = raw.mapNotNull { (day, json) ->
        runCatching { day to TodayData.parse(JSONObject(json), fetched[day] ?: 0) }.getOrNull()
    }.toMap()

    private fun setPending(ops: List<PendingOp>) {
        _state.value = _state.value.copy(pending = ops)
        val a = JSONArray().apply { ops.forEach { put(it.toJson()) } }
        writeAtomic(File(dir, "outbox.json"), a.toString())
    }

    private fun saveDays() {
        val days = JSONObject()
        raw.forEach { (k, v) -> days.put(k, JSONObject().put("data", v).put("at", fetched[k] ?: 0)) }
        val o = JSONObject().put("days", days).put("symptoms", JSONArray(_state.value.symptoms))
        writeAtomic(File(dir, "days.json"), o.toString())
    }

    private fun writeAtomic(f: File, text: String) {
        val tmp = File(f.parentFile, f.name + ".tmp")
        tmp.writeText(text)
        tmp.renameTo(f)
    }

    /** Keeps widget and quick settings tile in step with the data. */
    private fun changed(context: Context) = Surfaces.update(context)

    private fun describeOp(op: PendingOp) = when (op) {
        is PendingOp.AddCheckin -> "${op.checkin.kind} ${op.checkin.value ?: ""} ${op.checkin.label ?: ""}".trim()
        is PendingOp.DeleteCheckin -> tr("Löschen")
        is PendingOp.AddDrink -> tr("Getränk {0} {1}", op.drink.drink, op.drink.ml)
        is PendingOp.DeleteDrink -> tr("Getränk löschen")
        is PendingOp.UpdateDrink -> tr("Getränk ändern {0} {1}", op.drink.drink, op.drink.ml)
        is PendingOp.LogHabit -> tr("Gewohnheit {0}", op.day)
        is PendingOp.LogSupplement -> tr("Supplement {0}: {1}", op.day, op.count)
        is PendingOp.AddFood -> tr("Lebensmittel {0}", op.food.name)
        is PendingOp.UpdateFood -> tr("Lebensmittel ändern {0}", op.food.name)
        is PendingOp.DeleteFood -> tr("Lebensmittel löschen")
        is PendingOp.UpdateTemplate -> tr("Vorlage ändern {0}", op.template.name)
        is PendingOp.DeleteTemplate -> tr("Vorlage löschen")
        is PendingOp.AddFoodEntry -> tr("Essen {0}", op.entry.day)
        is PendingOp.UpdateFoodEntry -> tr("Essen ändern {0}", op.day)
        is PendingOp.DeleteFoodEntry -> tr("Essen löschen")
        is PendingOp.AddTemplate -> tr("Vorlage {0}", op.template.name)
        is PendingOp.AddBodyEntry -> "${op.metric} ${op.value}"
        is PendingOp.AddWorkout -> tr("Training {0}", op.title)
        is PendingOp.Unknown -> tr("unbekannt")
    }
}

/** Sends the outbox when there is a network; retries with backoff while the server is unreachable. */
class OutboxWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        Store.init(applicationContext)
        DayRepo.init(applicationContext)
        val empty = DayRepo.flush(applicationContext)
        if (empty) DayRepo.refresh(applicationContext)
        return if (empty || runAttemptCount >= 50) Result.success() else Result.retry()
    }

    companion object {
        fun schedule(context: Context) {
            val req = OneTimeWorkRequestBuilder<OutboxWorker>()
                // fast taps are sent together; REPLACE restarts the wait with every new entry
                .setInitialDelay(2, TimeUnit.SECONDS)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork("outbox", ExistingWorkPolicy.REPLACE, req)
        }
    }
}
