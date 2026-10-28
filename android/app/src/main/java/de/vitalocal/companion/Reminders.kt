package de.vitalocal.companion

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.core.content.edit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId

/** A daily reminder for a supplement or a manual habit; it only fires while the item is not done. */
data class Reminder(val kind: String, val id: String, val name: String, val time: LocalTime) {
    val key: String get() = "${kind}:${id}"

    fun toJson(): JSONObject = JSONObject().put("kind", kind).put("id", id).put("name", name).put("time", time.toString())

    companion object {
        const val SUPPLEMENT = "supplement"
        const val HABIT = "habit"
        /** One evening check over all habits: streaks that end today unless something is done. */
        const val STREAK = "streak"
        val STREAK_DEFAULT = Reminder(STREAK, "all", tr("Serien in Gefahr"), LocalTime.of(20, 0))

        fun fromJson(o: JSONObject) = Reminder(o.getString("kind"), o.getString("id"), o.getString("name"), LocalTime.parse(o.getString("time")))
    }
}

/**
 * Reminders live on the phone (set in the app's settings). One alarm is kept for the next due
 * reminder; when it goes off, the day is reloaded and only items not yet done are notified.
 */
object Reminders {
    private const val CHANNEL = "reminders"
    private const val PREFS = "reminders"
    /** A reminder missed by more than this (phone off) is skipped instead of shown late. */
    private const val LATE_MINUTES = 120L

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun all(context: Context): List<Reminder> = runCatching {
        val a = JSONArray(prefs(context).getString("list", "[]"))
        List(a.length()) { Reminder.fromJson(a.getJSONObject(it)) }
    }.getOrDefault(emptyList())

    /** The streak warning is on by default (once; switching it off stays off). */
    fun ensureDefaults(context: Context) {
        if (prefs(context).getBoolean("streak_default", false)) return
        prefs(context).edit { putBoolean("streak_default", true) }
        if (all(context).none { it.kind == Reminder.STREAK }) set(context, Reminder.STREAK_DEFAULT)
    }

    fun set(context: Context, reminder: Reminder) = save(context, all(context).filterNot { it.key == reminder.key } + reminder)

    fun remove(context: Context, key: String) = save(context, all(context).filterNot { it.key == key })

    private fun save(context: Context, list: List<Reminder>) {
        prefs(context).edit { putString("list", JSONArray().apply { list.forEach { put(it.toJson()) } }.toString()) }
        schedule(context)
    }

    private fun firedOn(context: Context, key: String): String? = prefs(context).getString("fired:${key}", null)
    private fun markFired(context: Context, key: String, day: String) = prefs(context).edit { putString("fired:${key}", day) }

    fun createChannel(context: Context) {
        val channel = NotificationChannel(CHANNEL, tr("Erinnerungen"), NotificationManager.IMPORTANCE_DEFAULT)
            .apply { description = tr("Supplements und Gewohnheiten, die heute noch offen sind") }
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    /** Next moment a reminder is due: today if not yet fired and still ahead, else tomorrow. */
    internal fun nextDue(list: List<Reminder>, now: LocalDateTime, fired: (String) -> String?): LocalDateTime? {
        val today = now.toLocalDate()
        return list.map { r ->
            val at = today.atTime(r.time)
            if (fired(r.key) != today.toString() && !at.isBefore(now.minusMinutes(LATE_MINUTES))) at.coerceAtLeast(now)
            else today.plusDays(1).atTime(r.time)
        }.minOrNull()
    }

    fun schedule(context: Context) {
        val am = context.getSystemService(AlarmManager::class.java)
        val pi = PendingIntent.getBroadcast(
            context, 0, Intent(context, ReminderReceiver::class.java).setAction(ReminderReceiver.ALARM),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val next = nextDue(all(context), LocalDateTime.now()) { firedOn(context, it) }
        if (next == null) {
            am.cancel(pi)
            return
        }
        val at = next.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
        // exact (USE_EXACT_ALARM); without the permission Android may deliver up to an hour late
        if (Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
        else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
    }

    /** Called by the alarm: notifies every due reminder whose item is still open today. */
    suspend fun fire(context: Context) {
        val now = LocalDateTime.now()
        val today = now.toLocalDate().toString()
        val due = all(context).filter { r ->
            val at = LocalDate.now().atTime(r.time)
            firedOn(context, r.key) != today && !at.isAfter(now)
        }
        if (due.isEmpty()) return schedule(context)
        withTimeoutOrNull(8_000) { DayRepo.refresh(context, today) } // offline or slow: judge by the cached day
        val d = DayRepo.state.value.view(today)
        for (r in due) {
            markFired(context, r.key, today)
            if (now.isAfter(LocalDate.now().atTime(r.time).plusMinutes(LATE_MINUTES))) continue
            if (r.kind == Reminder.STREAK) {
                streakWarning(d)?.let { notify(context, r, it, title = tr("Serien in Gefahr"), doneAction = false) }
                continue
            }
            val text = when (r.kind) {
                Reminder.SUPPLEMENT -> d.supplements.firstOrNull { it.id == r.id }?.takeIf { !it.settled }
                    ?.let { "${it.name}: ${it.count} von ${it.perDay} genommen" }
                else -> d.habits.firstOrNull { it.id == r.id }?.takeIf { it.done != true && !it.automatic }
                    ?.let { h -> if (h.kind == "count") "${h.name}: ${(h.value ?: 0.0).toInt()} von ${h.target?.toInt() ?: 1}" else "${h.name} ist noch offen" }
            } ?: continue
            notify(context, r, text)
        }
        schedule(context)
    }

    /** "Zahnseide (12 T.), Dehnen (5 T., Joker springt ein)" for habits with a streak of 3+ still open today. */
    internal fun streakWarning(d: TodayData): String? {
        val atRisk = d.habits.filter { h ->
            h.streak >= 3 && h.streakUnit == "days" && h.done != true && (!h.automatic || h.done == false)
        }.sortedByDescending { it.streak }
        if (atRisk.isEmpty()) return null
        val parts = atRisk.map { h -> "${h.icon?.let { "${it} " } ?: ""}${h.name} (${h.streak} T.${if (h.jokers > 0) tr(", Joker springt ein") else ""})" }
        val lead = if (atRisk.size == 1) tr("Deine Serie endet heute: ") else tr("Diese Serien enden heute: ")
        return lead + parts.joinToString(", ")
    }

    private fun notify(context: Context, r: Reminder, text: String, title: String? = null, doneAction: Boolean = true) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val nid = r.key.hashCode()
        val open = PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val done = PendingIntent.getBroadcast(
            context, nid,
            Intent(context, ReminderReceiver::class.java).setAction(ReminderReceiver.DONE)
                .putExtra("kind", r.kind).putExtra("id", r.id).putExtra("nid", nid),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(title ?: if (r.kind == Reminder.SUPPLEMENT) tr("Supplement") else tr("Gewohnheit"))
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open)
            .setAutoCancel(true)
            .apply { if (doneAction) addAction(0, tr("Erledigt"), done) }
            .build()
        NotificationManagerCompat.from(context).notify(nid, n)
    }

    /** "Erledigt" in the notification: supplement taken in full, habit ticked (or its goal counted). */
    fun markDone(context: Context, kind: String, id: String) {
        val day = DayRepo.today
        val d = DayRepo.state.value.view(day)
        when (kind) {
            Reminder.SUPPLEMENT -> d.supplements.firstOrNull { it.id == id }?.let {
                DayRepo.logSupplement(context, day, id, maxOf(it.count, it.perDay))
            }
            else -> d.habits.firstOrNull { it.id == id }?.let {
                DayRepo.logHabit(context, day, id, if (it.kind == "count") maxOf(it.value ?: 0.0, it.target ?: 1.0) else 1.0)
            }
        }
    }
}

class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Store.init(context)
        DayRepo.init(context)
        when (intent.action) {
            ALARM -> {
                val result = goAsync()
                CoroutineScope(Dispatchers.IO).launch {
                    try { Reminders.fire(context) } finally { result.finish() }
                }
            }
            DONE -> {
                Reminders.markDone(context, intent.getStringExtra("kind") ?: return, intent.getStringExtra("id") ?: return)
                NotificationManagerCompat.from(context).cancel(intent.getIntExtra("nid", 0))
            }
        }
    }

    companion object {
        const val ALARM = "de.vitalocal.companion.REMINDER"
        const val DONE = "de.vitalocal.companion.REMINDER_DONE"
    }
}

/** After a reboot, an app update or a clock change the alarms have to be set again. */
class ReminderBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Reminders.schedule(context)
        LiveState.init(context)
        if (Settings(context).liveEnabled) LiveScheduler.arm(context) // the cycle itself waits for the alarm
    }
}
