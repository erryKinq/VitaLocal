package de.vitalocal.companion

import android.content.Context
import androidx.core.content.edit
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.HealthConnectFeatures
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.ActiveCaloriesBurnedRecord
import androidx.health.connect.client.records.BasalMetabolicRateRecord
import androidx.health.connect.client.records.BodyFatRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.HeartRateVariabilityRmssdRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.TotalCaloriesBurnedRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import org.json.JSONArray
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.temporal.ChronoUnit
import kotlin.reflect.KClass

/**
 * Health Connect as data source instead of a watch via Gadgetbridge: steps, energy, sleep, weight,
 * heart rate and workouts that other apps (Samsung Health, Fitbit, Google Fit, …) write there.
 * Everything of a window goes to the server as one batch (`POST /ingest/health-connect`); the
 * window starts a little before the last successful sync, so late writes are caught.
 */
object HealthConnect {
    private const val PREFS = "health_connect"
    private val FIRST_DAYS = Duration.ofDays(30)
    private val OVERLAP = Duration.ofDays(2)

    private val TYPES: List<KClass<out Record>> = listOf(
        StepsRecord::class, ActiveCaloriesBurnedRecord::class, TotalCaloriesBurnedRecord::class,
        BasalMetabolicRateRecord::class, WeightRecord::class, BodyFatRecord::class, HeartRateRecord::class,
        RestingHeartRateRecord::class, OxygenSaturationRecord::class, HeartRateVariabilityRmssdRecord::class,
        SleepSessionRecord::class, ExerciseSessionRecord::class,
    )
    val PERMISSIONS: Set<String> = TYPES.map { HealthPermission.getReadPermission(it) }.toSet()
    const val BACKGROUND = HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND

    fun available(context: Context): Boolean =
        HealthConnectClient.getSdkStatus(context) == HealthConnectClient.SDK_AVAILABLE

    private fun client(context: Context) = HealthConnectClient.getOrCreate(context)

    /** Read in the background (periodic sync) is a feature of newer Health Connect versions. */
    fun backgroundSupported(context: Context): Boolean = available(context) &&
        client(context).features.getFeatureStatus(HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND) ==
        HealthConnectFeatures.FEATURE_STATUS_AVAILABLE

    suspend fun granted(context: Context): Set<String> =
        if (available(context)) client(context).permissionController.getGrantedPermissions() else emptySet()

    fun requested(context: Context): Set<String> = if (backgroundSupported(context)) PERMISSIONS + BACKGROUND else PERMISSIONS

    /** Reads the window and uploads it; returns the batch id. Throws when reading is not allowed. */
    suspend fun sync(context: Context, api: Api, clientMeta: JSONObject): Long {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val now = Instant.now()
        val last = prefs.getLong("synced_until", 0L).takeIf { it > 0 }?.let(Instant::ofEpochMilli)
        val from = (last?.minus(OVERLAP) ?: now.minus(FIRST_DAYS)).truncatedTo(ChronoUnit.DAYS)
        val batch = read(context, from, now).put("client", clientMeta)
        val id = api.uploadHealthConnect(batch)
        prefs.edit { putLong("synced_until", now.toEpochMilli()) }
        return id
    }

    private suspend fun <T : Record> all(c: HealthConnectClient, type: KClass<T>, from: Instant, to: Instant): List<T> {
        val out = mutableListOf<T>()
        var page: String? = null
        do {
            val r = c.readRecords(ReadRecordsRequest(type, TimeRangeFilter.between(from, to), pageToken = page))
            out += r.records
            page = r.pageToken
        } while (page != null)
        return out
    }

    private fun iso(t: Instant, offset: java.time.ZoneOffset?): String =
        OffsetDateTime.ofInstant(t, offset ?: ZoneId.systemDefault().rules.getOffset(t)).toString()

    suspend fun read(context: Context, from: Instant, to: Instant): JSONObject {
        val c = client(context)
        val apps = mutableSetOf<String>()
        fun <T : Record> List<T>.seen(): List<T> = onEach { apps += it.metadata.dataOrigin.packageName }
        fun arr(items: List<JSONObject>) = JSONArray().apply { items.forEach { put(it) } }
        fun obj(vararg kv: Pair<String, Any?>) = JSONObject().apply { kv.forEach { (k, v) -> put(k, v) } }

        val o = JSONObject()
        o.put("range", obj("from" to iso(from, null), "to" to iso(to, null)))
        o.put("steps", arr(all(c, StepsRecord::class, from, to).seen().map {
            obj("start" to iso(it.startTime, it.startZoneOffset), "end" to iso(it.endTime, it.endZoneOffset), "count" to it.count)
        }))
        o.put("active_calories", arr(all(c, ActiveCaloriesBurnedRecord::class, from, to).seen().map {
            obj("start" to iso(it.startTime, it.startZoneOffset), "end" to iso(it.endTime, it.endZoneOffset), "kcal" to it.energy.inKilocalories)
        }))
        o.put("total_calories", arr(all(c, TotalCaloriesBurnedRecord::class, from, to).seen().map {
            obj("start" to iso(it.startTime, it.startZoneOffset), "end" to iso(it.endTime, it.endZoneOffset), "kcal" to it.energy.inKilocalories)
        }))
        o.put("basal_metabolic_rate", arr(all(c, BasalMetabolicRateRecord::class, from, to).seen().map {
            obj("time" to iso(it.time, it.zoneOffset), "kcal_per_day" to it.basalMetabolicRate.inKilocaloriesPerDay)
        }))
        o.put("weight", arr(all(c, WeightRecord::class, from, to).seen().map {
            obj("time" to iso(it.time, it.zoneOffset), "kg" to it.weight.inKilograms)
        }))
        o.put("body_fat", arr(all(c, BodyFatRecord::class, from, to).seen().map {
            obj("time" to iso(it.time, it.zoneOffset), "percent" to it.percentage.value)
        }))
        // heart rate as minute means: some sources write every second
        val minutes = sortedMapOf<Instant, MutableList<Long>>()
        all(c, HeartRateRecord::class, from, to).seen().forEach { r ->
            r.samples.forEach { s -> minutes.getOrPut(s.time.truncatedTo(ChronoUnit.MINUTES)) { mutableListOf() } += s.beatsPerMinute }
        }
        o.put("heart_rate", arr(minutes.map { (t, v) -> obj("time" to iso(t, null), "bpm" to v.average()) }))
        o.put("resting_heart_rate", arr(all(c, RestingHeartRateRecord::class, from, to).seen().map {
            obj("time" to iso(it.time, it.zoneOffset), "bpm" to it.beatsPerMinute)
        }))
        o.put("oxygen_saturation", arr(all(c, OxygenSaturationRecord::class, from, to).seen().map {
            obj("time" to iso(it.time, it.zoneOffset), "percent" to it.percentage.value)
        }))
        o.put("hrv_rmssd", arr(all(c, HeartRateVariabilityRmssdRecord::class, from, to).seen().map {
            obj("time" to iso(it.time, it.zoneOffset), "ms" to it.heartRateVariabilityMillis)
        }))
        o.put("sleep", arr(all(c, SleepSessionRecord::class, from, to).seen().map { s ->
            obj("start" to iso(s.startTime, s.startZoneOffset), "end" to iso(s.endTime, s.endZoneOffset),
                "stages" to arr(s.stages.map { st ->
                    obj("start" to iso(st.startTime, s.startZoneOffset), "end" to iso(st.endTime, s.endZoneOffset), "stage" to st.stage)
                }))
        }))
        o.put("exercise", arr(all(c, ExerciseSessionRecord::class, from, to).seen().map {
            obj("id" to it.metadata.id, "start" to iso(it.startTime, it.startZoneOffset), "end" to iso(it.endTime, it.endZoneOffset),
                "type" to it.exerciseType, "title" to it.title)
        }))
        o.put("source_apps", JSONArray(apps.sorted()))
        return o
    }
}
