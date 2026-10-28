package de.vitalocal.companion

import java.time.Duration
import java.time.LocalDateTime
import java.time.LocalTime

/** When live sync runs: every few minutes by day, rarely at night. Times are local. */
data class LiveConfig(
    val dayMinutes: Int = 12,
    val nightHours: Int = 4,
    val nightStart: LocalTime = LocalTime.of(22, 0),
    val nightEnd: LocalTime = LocalTime.of(6, 0),
)

/** Pure scheduling rules for live sync (unit-tested). */
object LiveSchedule {
    /** The longest gap backoff stretches the day interval to. */
    const val MAX_BACKOFF_MINUTES = 60L

    /** Android 15 allows a dataSync foreground service 6 h per 24 h; stay well below. */
    val SLOW_DOWN_AT: Duration = Duration.ofMinutes(270)
    val PAUSE_AT: Duration = Duration.ofMinutes(330)

    fun inNight(t: LocalTime, c: LiveConfig): Boolean =
        if (c.nightStart == c.nightEnd) false
        else if (c.nightStart < c.nightEnd) !t.isBefore(c.nightStart) && t.isBefore(c.nightEnd)
        else !t.isBefore(c.nightStart) || t.isBefore(c.nightEnd) // across midnight

    /**
     * The next run after [now]. By day every `dayMinutes` (times [backoff], at most an hour); at
     * night every `nightHours`, but never past the end of the night: 21:59 → 22:11 → 02:11 →
     * 06:00 → 06:12.
     */
    fun next(now: LocalDateTime, c: LiveConfig, backoff: Int = 1): LocalDateTime {
        if (inNight(now.toLocalTime(), c)) {
            var end = now.toLocalDate().atTime(c.nightEnd)
            if (!end.isAfter(now)) end = end.plusDays(1)
            return minOf(now.plusHours(c.nightHours.toLong()), end)
        }
        val minutes = minOf(c.dayMinutes.toLong() * backoff.coerceAtLeast(1), MAX_BACKOFF_MINUTES.coerceAtLeast(c.dayMinutes.toLong()))
        return now.plusMinutes(minutes)
    }

    /** Runtime of the foreground service in the last 24 h, from (start ms, duration ms) entries. */
    fun usedLast24h(entries: List<Pair<Long, Long>>, nowMs: Long): Duration =
        Duration.ofMillis(entries.filter { it.first > nowMs - 86_400_000L }.sumOf { it.second })

    /** 1 normally, 2 when the day's budget gets tight, 0 = pause until the app is opened. */
    fun budgetFactor(used: Duration): Int = when {
        used >= PAUSE_AT -> 0
        used >= SLOW_DOWN_AT -> 2
        else -> 1
    }

    /** How long to wait for Gadgetbridge's "sync finished": twice the usual, 30–90 s, 20 s once the watch kept quiet. */
    fun syncWait(recentSyncsMs: List<Long>, watchSilentStreak: Int): Long {
        if (watchSilentStreak >= 3) return 20_000L
        if (recentSyncsMs.size < 3) return 60_000L
        val sorted = recentSyncsMs.sorted()
        val p90 = sorted[((sorted.size - 1) * 0.9).toInt()]
        return (2 * p90).coerceIn(30_000L, 90_000L)
    }
}
