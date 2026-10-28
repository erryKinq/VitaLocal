package de.vitalocal.companion

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

class ApiException(val code: Int, message: String) : IOException(message)

/** 409 on a versioned text: someone else saved first; [current] is the server's text and version. */
class StaleTextException(val current: JSONObject) : IOException("stale version")

data class BatchInfo(val id: Long, val source: String, val status: String, val error: String?) {
    val finished: Boolean get() = status !in setOf("pending", "processing")
    val succeeded: Boolean get() = status in SUCCESS

    companion object {
        val SUCCESS = setOf("completed", "completed_with_errors")

        fun from(o: JSONObject) = BatchInfo(
            id = o.getLong("id"),
            source = o.optString("source"),
            status = o.getString("status"),
            error = o.optString("error").takeIf { it.isNotEmpty() && it != "null" },
        )
    }
}

data class UploadResult(val batchId: Long, val created: Boolean, val source: String)

data class GbDbResult(val batchId: Long, val created: Boolean, val sha256: String)

/** What the server can take and which files it wants (`GET /ingest/status`). */
data class IngestStatus(val capabilities: Set<String>, val reasons: List<String>, val lastWatchData: String?)

/** Thin client for the VitaLocal REST API (see docs/API.md). Blocking; call from Dispatchers.IO. */
class Api(baseUrl: String, private val token: String) {
    private val base = Settings.normalizeUrl(baseUrl)

    /** Server version; also proves that the token is accepted. */
    fun checkAccess(): String {
        request("/health", auth = false).use { r -> r.requireOk() }
        return request("/api/v1/meta").use { r -> JSONObject(r.requireOk()).optString("version") }
    }

    fun isReachable(): Boolean = runCatching { request("/health", auth = false).use { it.code in 200..503 } }
        .getOrDefault(false)

    /** Imports of exactly this file, newest first; null if the server has never seen it. */
    fun artifactBatches(sha256: String): List<BatchInfo>? = request("/api/v1/ingest/artifacts/${sha256}").use { r ->
        if (r.code == 404) return null
        val arr = JSONObject(r.requireOk()).getJSONArray("batches")
        List(arr.length()) { BatchInfo.from(arr.getJSONObject(it)) }
    }

    fun upload(file: File, filename: String, clientMeta: JSONObject): UploadResult {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", filename, file.asRequestBody("application/octet-stream".toMediaType()))
            .addFormDataPart("client_meta", clientMeta.toString())
            .build()
        val req = builder("/api/v1/ingest/upload").post(body).build()
        return uploadClient.newCall(req).execute().use { r ->
            val o = JSONObject(r.requireOk())
            UploadResult(o.getLong("batch_id"), o.getBoolean("created"), o.getString("source"))
        }
    }

    /** A window of Health Connect records (see HealthConnect.read); returns the batch id. */
    fun uploadHealthConnect(batch: JSONObject): Long {
        val req = builder("/api/v1/ingest/health-connect").post(batch.toString().toRequestBody(JSON)).build()
        return uploadClient.newCall(req).execute().use { r -> JSONObject(r.requireOk()).getLong("batch_id") }
    }

    /** Live database: gzip of the whole file or of a VLDD delta (see DbDelta). 409/422: send it whole. */
    fun uploadGbDb(meta: JSONObject, gzip: File): GbDbResult {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("meta", meta.toString())
            .addFormDataPart("data", "db.gz", gzip.asRequestBody("application/gzip".toMediaType()))
            .build()
        val req = builder("/api/v1/ingest/gadgetbridge/db").post(body).build()
        return uploadClient.newCall(req).execute().use { r ->
            val o = JSONObject(r.requireOk())
            GbDbResult(o.getLong("batch_id"), o.getBoolean("created"), o.getString("sha256"))
        }
    }

    fun ingestStatus(): IngestStatus = request("/api/v1/ingest/status").use { r ->
        val o = JSONObject(r.requireOk())
        val caps = o.optJSONArray("capabilities")
        val reasons = o.optJSONObject("files_wanted")?.optJSONArray("reasons")
        IngestStatus(
            List(caps?.length() ?: 0) { caps!!.getString(it) }.toSet(),
            List(reasons?.length() ?: 0) { reasons!!.getJSONObject(it).optString("kind") },
            o.optString("last_heart_rate_at").takeIf { it.isNotEmpty() && it != "null" },
        )
    }

    /** Of these SHA-256s, the files the server lacks. */
    fun missingArtifacts(shas: List<String>): List<String> {
        val body = JSONObject().put("sha256", org.json.JSONArray(shas)).toString().toRequestBody(JSON)
        return client.newCall(builder("/api/v1/ingest/artifacts/missing").post(body).build()).execute().use { r ->
            val arr = JSONObject(r.requireOk()).getJSONArray("missing")
            List(arr.length()) { arr.getString(it) }
        }
    }

    /** A file set by manifest with the bytes of the files the server lacks. */
    fun uploadFiles(manifest: JSONObject, clientMeta: JSONObject, files: List<Pair<String, ByteArray>>): UploadResult {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("manifest", manifest.toString())
            .addFormDataPart("client_meta", clientMeta.toString())
            .apply { files.forEach { (sha, bytes) -> addFormDataPart("files", sha, bytes.toRequestBody("application/octet-stream".toMediaType())) } }
            .build()
        return uploadClient.newCall(builder("/api/v1/ingest/gadgetbridge/files").post(body).build()).execute().use { r ->
            val o = JSONObject(r.requireOk())
            UploadResult(o.getLong("batch_id"), o.getBoolean("created"), o.getString("source"))
        }
    }

    /** Uploads a lab report (PDF/image); the server's model reads it and it is reviewed in the WebUI. */
    fun uploadLabReport(file: File, filename: String, clientMeta: JSONObject): String {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", filename, file.asRequestBody("application/octet-stream".toMediaType()))
            .addFormDataPart("client_meta", clientMeta.toString())
            .build()
        val req = builder("/api/v1/labs/imports").post(body).build()
        return uploadClient.newCall(req).execute().use { r -> JSONObject(r.requireOk()).getString("id") }
    }

    fun batch(id: Long): BatchInfo = request("/api/v1/ingest/batches/${id}").use { r ->
        BatchInfo.from(JSONObject(r.requireOk()))
    }

    /** The build published for download (WebUI "Android app"), null when none is. */
    fun publishedApp(): PublishedApp? = request("/api/v1/app/android").use { r ->
        val text = r.requireOk().trim()
        if (text == "null" || text.isEmpty()) return null
        val o = JSONObject(text)
        PublishedApp(o.getString("version"), o.getInt("version_code"), o.getLong("size"), o.getString("sha256"))
    }

    /** Streams the published APK into [target], reporting the bytes received. */
    fun downloadApp(target: File, progress: (Long) -> Unit) {
        uploadClient.newCall(builder("/api/v1/app/android/apk").build()).execute().use { r ->
            if (!r.isSuccessful) throw ApiException(r.code, "HTTP ${r.code}")
            val body = r.body ?: throw IOException("empty response")
            target.outputStream().use { out ->
                body.byteStream().use { input ->
                    val buf = ByteArray(1 shl 16)
                    var done = 0L
                    var reported = 0L
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        done += n
                        if (done - reported > 512 * 1024) { progress(done); reported = done }
                    }
                    progress(done)
                }
            }
        }
    }

    fun today(day: String): JSONObject = request("/api/v1/today?day=${day}").use { r -> JSONObject(r.requireOk()) }

    /** The day's journal text with its version (`GET /journal/{day}`). */
    fun journalDay(day: String): JSONObject = request("/api/v1/journal/${day}").use { r -> JSONObject(r.requireOk()) }

    /** Saves the day's journal text over [version]; throws [StaleTextException] when it changed meanwhile. */
    fun putJournalDay(day: String, text: String, version: Int): JSONObject {
        val body = JSONObject().put("text", text).put("version", version).put("source", "android")
        return client.newCall(builder("/api/v1/journal/${day}").put(body.toString().toRequestBody(JSON)).build()).execute().use { r ->
            if (r.code == 409) {
                val current = runCatching { JSONObject(r.body?.string().orEmpty()).getJSONObject("detail").getJSONObject("current") }.getOrNull()
                if (current != null) throw StaleTextException(current)
            }
            JSONObject(r.requireOk())
        }
    }

    /** Next day of the active plan with targets and suggestions; null without an active plan. */
    fun nextWorkout(): JSONObject? = request("/api/v1/training/next").use { r ->
        val text = r.requireOk().trim()
        if (text == "null" || text.isEmpty()) null else JSONObject(text)
    }

    /** Today's training readiness: own estimate with its components and Garmin's value. */
    fun readiness(): JSONObject = request("/api/v1/readiness").use { r -> JSONObject(r.requireOk()) }

    fun trainingHistory(limit: Int): JSONObject =
        request("/api/v1/training/history?limit=${limit}").use { r -> JSONObject(r.requireOk()) }

    fun trainingSession(id: Long): JSONObject = request("/api/v1/training/sessions/${id}").use { r -> JSONObject(r.requireOk()) }

    /** Full rating state of a session; returns the updated session. */
    fun putRatings(id: Long, ratings: org.json.JSONArray): JSONObject {
        val req = builder("/api/v1/training/sessions/${id}/ratings")
            .put(JSONObject().put("ratings", ratings).toString().toRequestBody(JSON)).build()
        return client.newCall(req).execute().use { r -> JSONObject(r.requireOk()) }
    }

    /** Starts (POST) or ends (DELETE) the active plan's deload week. */
    fun deload(start: Boolean) {
        val b = builder("/api/v1/training/deload")
        val req = if (start) b.post(JSONObject().toString().toRequestBody(JSON)).build() else b.delete().build()
        client.newCall(req).execute().use { r -> r.requireOk() }
    }

    /** `null` from a server before capabilities existed (404). */
    fun capabilities(): JSONObject? = request("/api/v1/capabilities").use { r ->
        if (r.code == 404) null else JSONObject(r.requireOk())
    }

    /** Whether the AI coach is set up (`GET /coach/status`). */
    fun coachStatus(): JSONObject = request("/api/v1/coach/status").use { r -> JSONObject(r.requireOk()) }

    /** Starts the AI debrief of a workout; the answer follows in [coachMessages]. */
    fun startDebrief(activityId: Long): JSONObject {
        val lang = if (I18n.german) "de" else "en"
        val req = builder("/api/v1/coach/debrief")
            .post(JSONObject().put("activity_id", activityId).put("lang", lang).toString().toRequestBody(JSON)).build()
        return client.newCall(req).execute().use { r -> JSONObject(r.requireOk()) }
    }

    fun coachMessages(activityId: Long): org.json.JSONArray =
        request("/api/v1/coach/messages?activity_id=${activityId}&limit=1").use { r -> org.json.JSONArray(r.requireOk()) }

    /** The active plan with every day's targets (`GET /training/plan/current`), null without one. */
    fun currentPlan(): JSONObject? = request("/api/v1/training/plan/current").use { r ->
        r.requireOk().trim().let { if (it == "null" || it.isEmpty()) null else JSONObject(it) }
    }

    fun exercises(): org.json.JSONArray = request("/api/v1/training/exercises").use { r -> org.json.JSONArray(r.requireOk()) }

    fun plates(): JSONObject = request("/api/v1/training/plates").use { r -> JSONObject(r.requireOk()) }

    /** Recovery per muscle (`GET /training/muscles/state`). */
    fun muscleState(): JSONObject = request("/api/v1/training/muscles/state").use { r -> JSONObject(r.requireOk()) }

    /** Skips the next day of the active plan; returns the skip's id (for undo). */
    fun skipNext(note: String?): String {
        val req = builder("/api/v1/training/skips")
            .post(JSONObject().put("note", note ?: JSONObject.NULL).toString().toRequestBody(JSON)).build()
        return client.newCall(req).execute().use { r -> JSONObject(r.requireOk()).getString("id") }
    }

    fun undoSkip(id: String) {
        client.newCall(builder("/api/v1/training/skips/${id}").delete().build()).execute().use { r -> r.requireOk() }
    }

    /** The full habit rewards (achievements with progress, garden, challenges) for the habits tab. */
    fun habitRewards(): JSONObject = request("/api/v1/habits/rewards").use { r -> JSONObject(r.requireOk()) }

    fun habitTemplates(): org.json.JSONArray = request("/api/v1/habits/templates").use { r -> org.json.JSONArray(r.requireOk()) }

    fun habitMetrics(): org.json.JSONArray = request("/api/v1/habits/metrics").use { r -> org.json.JSONArray(r.requireOk()) }

    /** Creates a habit (online only: a new habit has no offline state yet). */
    fun createHabit(body: JSONObject) {
        client.newCall(builder("/api/v1/habits").post(body.toString().toRequestBody(JSON)).build()).execute().use { r -> r.requireOk() }
    }

    fun markRewardsSeen() {
        client.newCall(builder("/api/v1/habits/rewards/seen").post(ByteArray(0).toRequestBody(null)).build()).execute().use { r -> r.requireOk() }
    }

    fun symptoms(): List<String> = request("/api/v1/symptoms").use { r ->
        val arr = org.json.JSONArray(r.requireOk())
        List(arr.length()) { arr.getJSONObject(it).getString("label") }
    }

    // --- food diary -------------------------------------------------------------------------------

    fun nutritionDay(day: String): JSONObject = request("/api/v1/nutrition/${day}").use { r -> JSONObject(r.requireOk()) }

    /** Catalog changes since [since] (all foods when null), oldest first. */
    fun foodsSince(since: String?): List<Food> {
        val q = "/api/v1/foods?limit=5000&updated_since=" + java.net.URLEncoder.encode(since ?: "1970-01-01T00:00:00Z", "UTF-8")
        return request(q).use { r -> org.json.JSONArray(r.requireOk()).objects().map { Food.parse(it) } }
    }

    fun templates(): List<MealTemplate> =
        request("/api/v1/templates").use { r -> org.json.JSONArray(r.requireOk()).objects().map { MealTemplate.parse(it) } }

    /** Last amount, count and last use per logged food. */
    fun foodUsage(): Map<String, FoodUsage> = request("/api/v1/foods/usage").use { r ->
        val o = JSONObject(r.requireOk())
        o.keys().asSequence().associateWith { k ->
            o.getJSONObject(k).let {
                FoodUsage(it.optDoubleOrNull("last_amount"), it.optDoubleOrNull("last_quantity"), it.optInt("count"), it.optStringOrNull("last_used"))
            }
        }
    }

    /** Nutrient goals: from today on (PUT), or none from today on (both null → DELETE). */
    fun setGoal(metric: String, min: Double?, max: Double?) {
        val b = builder("/api/v1/goals/${metric}")
        val req = if (min == null && max == null) b.delete()
            else b.put(JSONObject().put("min_value", min ?: JSONObject.NULL).put("max_value", max ?: JSONObject.NULL).toString().toRequestBody(JSON))
        client.newCall(req.build()).execute().use { it.requireOk() }
    }

    /** Favorites and recently used foods with their last amount. */
    fun recentFoods(limit: Int = 40): List<Food> =
        request("/api/v1/foods?limit=${limit}").use { r -> org.json.JSONArray(r.requireOk()).objects().map { Food.parse(it) } }

    /** From the catalog or Open Food Facts (stored there); null when unknown. 503/429 throw. */
    fun foodByBarcode(code: String): Food? = request("/api/v1/foods/barcode/${code}").use { r ->
        if (r.code == 404) null else Food.parse(JSONObject(r.requireOk()))
    }

    /** Open Food Facts search; hits are drafts (`id` = the catalog id when already taken over, else ""). */
    fun searchOnline(q: String): List<Food> {
        val path = "/api/v1/foods/online?q=" + java.net.URLEncoder.encode(q, "UTF-8")
        return request(path).use { r ->
            org.json.JSONArray(r.requireOk()).objects().map { o ->
                Food.parse(o.put("id", o.optStringOrNull("food_id") ?: ""))
            }
        }
    }

    fun takeOverOnline(code: String): Food = client.newCall(
        builder("/api/v1/foods/online/${code}").post(ByteArray(0).toRequestBody(null)).build(),
    ).execute().use { r -> Food.parse(JSONObject(r.requireOk())) }

    /** Sends one outbox write. Returns the HTTP status; throws only on network failure. */
    fun send(op: PendingOp): Int {
        val req = when (op) {
            is PendingOp.AddCheckin -> builder("/api/v1/checkins").post(
                JSONObject()
                    .put("id", op.checkin.id).put("kind", op.checkin.kind).put("ts", op.checkin.ts)
                    .put("value", op.checkin.value ?: JSONObject.NULL).put("label", op.checkin.label ?: JSONObject.NULL)
                    .put("note", op.checkin.note ?: JSONObject.NULL).put("source", "android")
                    .toString().toRequestBody(JSON),
            )
            is PendingOp.DeleteCheckin -> builder("/api/v1/checkins/${op.checkinId}").delete()
            is PendingOp.AddDrink -> builder("/api/v1/drinks").post(
                JSONObject()
                    .put("id", op.drink.id).put("ts", op.drink.ts).put("drink", op.drink.drink).put("ml", op.drink.ml)
                    .put("caffeine", op.drink.caffeine).put("source", "android")
                    .toString().toRequestBody(JSON),
            )
            is PendingOp.DeleteDrink -> builder("/api/v1/drinks/${op.drinkId}").delete()
            // the full corrected entry: replaying it gives the same result
            is PendingOp.UpdateDrink -> builder("/api/v1/drinks/${op.drink.id}").put(
                JSONObject()
                    .put("ts", op.drink.ts).put("drink", op.drink.drink).put("ml", op.drink.ml).put("caffeine", op.drink.caffeine)
                    .toString().toRequestBody(JSON),
            )
            is PendingOp.LogSupplement -> builder("/api/v1/supplements/${op.supplementId}/log/${op.day}")
                .put(JSONObject().put("count", op.count).toString().toRequestBody(JSON))
            is PendingOp.LogHabit -> builder("/api/v1/habits/${op.habitId}/log/${op.day}")
                .put(JSONObject().put("value", op.value).toString().toRequestBody(JSON))
            is PendingOp.AddFood -> builder("/api/v1/foods").post(op.food.toApiJson().toString().toRequestBody(JSON))
            is PendingOp.UpdateFood -> builder("/api/v1/foods/${op.food.id}").put(op.food.toApiJson().toString().toRequestBody(JSON))
            is PendingOp.DeleteFood -> builder("/api/v1/foods/${op.foodId}").delete()
            is PendingOp.AddFoodEntry -> builder("/api/v1/nutrition/entries").post(op.entry.toApiJson().toString().toRequestBody(JSON))
            is PendingOp.UpdateFoodEntry -> builder("/api/v1/nutrition/entries/${op.id}").put(
                JSONObject().put("logged_at", op.loggedAt).put("meal_index", op.mealIndex)
                    .put("amount", op.amount ?: JSONObject.NULL).put("quantity", op.quantity)
                    .apply {
                        if (op.name != null) put("name", op.name)
                        if (op.nutrients != null) put("nutrients", JSONObject(op.nutrients as Map<*, *>))
                        if (op.recompute) put("recompute", true)
                    }
                    .toString().toRequestBody(JSON),
            )
            is PendingOp.DeleteFoodEntry -> builder("/api/v1/nutrition/entries/${op.id}").delete()
            is PendingOp.AddTemplate -> builder("/api/v1/templates").post(op.template.toApiJson().toString().toRequestBody(JSON))
            is PendingOp.UpdateTemplate ->
                builder("/api/v1/templates/${op.template.id}").put(op.template.toApiJson().toString().toRequestBody(JSON))
            is PendingOp.DeleteTemplate -> builder("/api/v1/templates/${op.templateId}").delete()
            is PendingOp.AddBodyEntry -> builder("/api/v1/body").post(
                JSONObject().put("id", op.id).put("ts", op.ts).put("metric", op.metric).put("value", op.value)
                    .put("source", "android").toString().toRequestBody(JSON),
            )
            is PendingOp.AddWorkout -> builder("/api/v1/training/sessions").post(op.body.toRequestBody(JSON))
            is PendingOp.Unknown -> error("unknown outbox entry ${op.opId} is never sent")
        }
        return client.newCall(req.build()).execute().use { it.code }
    }

    private fun builder(path: String, auth: Boolean = true) = Request.Builder().url(base + path).apply {
        if (auth) header("Authorization", "Bearer ${token}")
    }

    private fun request(path: String, auth: Boolean = true): Response =
        client.newCall(builder(path, auth).build()).execute()

    private fun Response.requireOk(): String {
        val text = body?.string().orEmpty()
        if (!isSuccessful) {
            val detail = runCatching { JSONObject(text).opt("detail")?.toString() }.getOrNull()
            throw ApiException(code, when (code) {
                401 -> tr("Token abgelehnt")
                else -> "HTTP ${code}${detail?.let { ": ${it}" } ?: ""}"
            })
        }
        return text
    }

    companion object {
        private val JSON = "application/json".toMediaType()
        private val client = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .build()

        // Exports can be large and the server stores them before answering.
        private val uploadClient = client.newBuilder()
            .writeTimeout(5, TimeUnit.MINUTES)
            .readTimeout(5, TimeUnit.MINUTES)
            .build()
    }
}
