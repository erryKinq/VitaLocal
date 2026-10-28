package de.vitalocal.companion

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.util.UUID
import java.util.concurrent.TimeUnit

/** Periodic and manual sync of all sources. */
class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        Store.init(applicationContext)
        DayRepo.init(applicationContext)
        val only = inputData.getString("sources")?.split(",")?.mapNotNull { k -> Source.entries.find { it.key == k } }?.toSet()
        Syncer(applicationContext, inputData.getString("trigger") ?: "auto").run(only ?: Source.entries.toSet())
        // Keeps widget and tile current even when the app is not opened.
        DayRepo.refresh(applicationContext)
        Result.success()
    }
}

/** Uploads files shared into the app; retried with backoff until the server is reachable. */
class ShareWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        Store.init(applicationContext)
        val reached = Syncer(applicationContext, "share").run(setOf(Source.SHARED))
        when {
            Inbox.files(applicationContext).isEmpty() -> Result.success()
            !reached && runAttemptCount < 20 -> Result.retry()
            reached && runAttemptCount < 5 -> Result.retry()
            else -> Result.failure()
        }
    }
}

object Scheduler {
    private const val PERIODIC = "sync-periodic"
    const val MANUAL = "sync-manual"
    private const val SHARE = "share-upload"

    private val network = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    fun schedulePeriodic(context: Context, update: Boolean = false) {
        val hours = Settings(context).intervalHours.toLong()
        val req = PeriodicWorkRequestBuilder<SyncWorker>(hours, TimeUnit.HOURS)
            .setConstraints(network)
            .setInputData(workDataOf("trigger" to "auto"))
            .build()
        val policy = if (update) ExistingPeriodicWorkPolicy.UPDATE else ExistingPeriodicWorkPolicy.KEEP
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(PERIODIC, policy, req)
    }

    /** Health Connect reads work while the app is in front: one run when it is opened. */
    fun syncHealthConnect(context: Context) {
        if (Settings(context).dataSource != "health_connect") return
        val req = OneTimeWorkRequestBuilder<SyncWorker>().setConstraints(network)
            .setInputData(workDataOf("trigger" to "resume", "sources" to Source.HEALTH_CONNECT.key)).build()
        WorkManager.getInstance(context).enqueueUniqueWork("sync-hc", ExistingWorkPolicy.KEEP, req)
    }

    fun syncNow(context: Context) {
        val req = OneTimeWorkRequestBuilder<SyncWorker>().setInputData(workDataOf("trigger" to "manual")).build()
        WorkManager.getInstance(context).enqueueUniqueWork(MANUAL, ExistingWorkPolicy.KEEP, req)
    }

    fun uploadShared(context: Context) {
        val req = OneTimeWorkRequestBuilder<ShareWorker>()
            .setConstraints(network)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(SHARE, ExistingWorkPolicy.APPEND_OR_REPLACE, req)
    }
}

/** Files shared into the app wait here (own copy: the sender's URI grant expires). */
object Inbox {
    private const val SEP = "__"

    private fun dir(context: Context) = File(context.filesDir, "inbox").apply { mkdirs() }

    fun files(context: Context): List<File> = dir(context).listFiles().orEmpty().filter { it.isFile }.sortedBy { it.name }

    fun displayName(file: File): String = file.name.substringAfter(SEP)

    /** Copies a shared URI into the inbox; returns the display name. */
    fun add(context: Context, uri: Uri): String {
        val name = context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
            ?.use { c -> if (c.moveToFirst()) c.getString(0) else null }
            ?.replace(Regex("[^A-Za-z0-9._-]"), "_")
            ?: "shared"
        val target = File(dir(context), "${System.currentTimeMillis()}-${UUID.randomUUID()}${SEP}${name}")
        val input = context.contentResolver.openInputStream(uri) ?: error("cannot open ${uri}")
        input.use { src -> target.outputStream().use { src.copyTo(it) } }
        return name
    }
}

object Notifier {
    private const val CHANNEL = "sync-errors"
    private var nextId = 100

    fun createChannel(context: Context) {
        val channel = NotificationChannel(CHANNEL, tr("Sync-Fehler"), NotificationManager.IMPORTANCE_DEFAULT)
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    fun error(context: Context, title: String, text: String) {
        if (!Settings(context).notifyErrors) return
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) return
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(nextId++, n)
    }
}
