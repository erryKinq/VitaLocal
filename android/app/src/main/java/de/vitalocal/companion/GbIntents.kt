package de.vitalocal.companion

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.PowerManager

/**
 * Gadgetbridge's Intent API (Developer options → Intent API, every switch off by default).
 * Commands go to the installed Gadgetbridge only; its answers are implicit broadcasts, so they
 * reach a receiver registered at runtime while live sync runs.
 */
object GbIntents {
    private const val COMMAND = "nodomain.freeyourgadget.gadgetbridge.command"
    private const val ACTION = "nodomain.freeyourgadget.gadgetbridge.action"

    const val ACTIVITY_SYNC = "${COMMAND}.ACTIVITY_SYNC"
    const val TRIGGER_DATABASE_EXPORT = "${COMMAND}.TRIGGER_DATABASE_EXPORT"
    const val TRIGGER_ZIP_EXPORT = "${COMMAND}.TRIGGER_ZIP_EXPORT"

    const val SYNC_FINISHED = "${ACTION}.ACTIVITY_SYNC_FINISH"
    const val DATABASE_EXPORT_SUCCESS = "${ACTION}.DATABASE_EXPORT_SUCCESS"
    const val DATABASE_EXPORT_FAIL = "${ACTION}.DATABASE_EXPORT_FAIL"
    const val ZIP_EXPORT_SUCCESS = "${ACTION}.ZIP_EXPORT_SUCCESS"
    const val ZIP_EXPORT_FAIL = "${ACTION}.ZIP_EXPORT_FAIL"

    /** Release first, then the nightly builds. */
    val PACKAGES = listOf(
        "nodomain.freeyourgadget.gadgetbridge",
        "nodomain.freeyourgadget.gadgetbridge.nightly",
        "nodomain.freeyourgadget.gadgetbridge.nightly_nopebble",
    )

    /** The package to talk to: the configured one if installed, else the first installed. */
    fun pick(installed: Set<String>, preferred: String?): String? =
        preferred?.takeIf { it in installed } ?: PACKAGES.firstOrNull { it in installed }

    fun installedPackages(context: Context): Set<String> =
        PACKAGES.filter { versionOf(context, it) != null }.toSet()

    fun packageFor(context: Context): String? =
        pick(installedPackages(context), Settings(context).gbPackage.ifBlank { null })

    fun versionOf(context: Context, pkg: String): String? = try {
        context.packageManager.getPackageInfo(pkg, 0).versionName ?: "?"
    } catch (_: PackageManager.NameNotFoundException) {
        null
    }

    /** "0.94.1" >= "0.94": numeric parts compared one by one ("-nightly" suffixes ignored). */
    fun atLeast(version: String, min: String): Boolean {
        fun parts(v: String) = v.substringBefore('-').split('.').map { it.toIntOrNull() ?: 0 }
        val a = parts(version)
        val b = parts(min)
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrElse(i) { 0 }
            val y = b.getOrElse(i) { 0 }
            if (x != y) return x > y
        }
        return true
    }

    /** Gadgetbridge must not be battery-restricted, or Android may delay its export in Doze. */
    fun unrestricted(context: Context, pkg: String): Boolean =
        context.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(pkg)

    fun send(context: Context, pkg: String, action: String) {
        context.sendBroadcast(Intent(action).setPackage(pkg))
    }
}
