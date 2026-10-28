package de.vitalocal.companion

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import java.io.File
import java.security.MessageDigest

/** A build published on the server (`GET /api/v1/app/android`). */
data class PublishedApp(val version: String, val versionCode: Int, val size: Long, val sha256: String)

/**
 * Self-update from the VitaLocal server: the WebUI's "Android app" build, installed through the
 * system PackageInstaller (Android asks the user to confirm; the first time it also asks to allow
 * installs from this app). Same signing key, so the update keeps all settings.
 */
object AppUpdate {
    sealed class State {
        data object Idle : State()
        data class Available(val app: PublishedApp) : State()
        data class Downloading(val app: PublishedApp, val done: Long) : State()
        data class Installing(val app: PublishedApp) : State()
        data class Failed(val message: String, val app: PublishedApp?) : State()
    }

    private val _state = MutableStateFlow<State>(State.Idle)
    val state: StateFlow<State> = _state.asStateFlow()

    private const val CHECK_EVERY_MS = 6 * 3600_000L
    private var lastCheck = 0L

    /** Asks the server for a newer build; quietly does nothing when offline or none is published. */
    suspend fun check(context: Context, force: Boolean = false): PublishedApp? {
        val now = System.currentTimeMillis()
        if (!force && now - lastCheck < CHECK_EVERY_MS) return (_state.value as? State.Available)?.app
        val settings = Settings(context)
        if (!settings.isConfigured) return null
        val app = withContext(Dispatchers.IO) {
            runCatching { Api(settings.serverUrl, settings.token).publishedApp() }.getOrNull()
        }
        lastCheck = now
        val newer = app?.takeIf { it.versionCode > BuildConfig.VERSION_CODE }
        if (_state.value is State.Idle || _state.value is State.Available || force) {
            _state.value = if (newer != null) State.Available(newer) else State.Idle
        }
        return newer
    }

    /** Downloads the build, checks its SHA-256 and hands it to the system installer. */
    suspend fun install(context: Context, app: PublishedApp) {
        val settings = Settings(context)
        val file = File(context.cacheDir, "update.apk")
        try {
            _state.value = State.Downloading(app, 0)
            withContext(Dispatchers.IO) {
                Api(settings.serverUrl, settings.token).downloadApp(file) { done -> _state.value = State.Downloading(app, done) }
                val sha = file.inputStream().use { input ->
                    val md = MessageDigest.getInstance("SHA-256")
                    val buf = ByteArray(1 shl 16)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        md.update(buf, 0, n)
                    }
                    md.digest().joinToString("") { "%02x".format(it) }
                }
                if (sha != app.sha256) throw IllegalStateException(tr("Prüfsumme stimmt nicht, Download verworfen"))
                _state.value = State.Installing(app)
                commit(context, file)
            }
        } catch (e: Exception) {
            file.delete()
            _state.value = State.Failed(e.message ?: e.javaClass.simpleName, app)
        }
    }

    private fun commit(context: Context, apk: File) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite("VitaLocal.apk", 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val intent = Intent(context, UpdateReceiver::class.java)
            // mutable: the installer adds the status extras
            val pending = PendingIntent.getBroadcast(context, id, intent, PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            session.commit(pending.intentSender)
        }
        apk.delete()
    }

    internal fun onStatus(context: Context, intent: Intent) {
        val app = when (val s = _state.value) {
            is State.Installing -> s.app
            is State.Available -> s.app
            else -> null
        }
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                else intent.getParcelableExtra(Intent.EXTRA_INTENT)
                confirm?.let { context.startActivity(it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            }
            PackageInstaller.STATUS_SUCCESS -> _state.value = State.Idle // the new version restarts the app
            // also what Android reports right after "allow from this source" was switched on
            PackageInstaller.STATUS_FAILURE_ABORTED ->
                _state.value = State.Failed(tr("abgebrochen. Falls du gerade die Installation erlaubt hast: „Erneut“ tippen"), app)
            else -> {
                val msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: tr("Installation abgebrochen")
                _state.value = State.Failed(msg, app)
            }
        }
    }
}

class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = AppUpdate.onStatus(context, intent)
}
