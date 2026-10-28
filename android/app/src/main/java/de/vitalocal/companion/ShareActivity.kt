package de.vitalocal.companion

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.core.content.IntentCompat

/** Share target: copies the files into the inbox and hands them to [ShareWorker]. No UI of its own. */
class ShareActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val uris = when (intent?.action) {
            Intent.ACTION_SEND -> listOfNotNull(IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java))
            Intent.ACTION_SEND_MULTIPLE ->
                IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
            else -> emptyList()
        }
        // Copying a large export must not block the UI thread.
        Thread {
            val names = uris.mapNotNull { uri ->
                runCatching { Inbox.add(this, uri) }
                    .onFailure { Store.log(Level.ERROR, Source.SHARED, tr("Datei nicht lesbar: {0}", it.message)) }
                    .getOrNull()
            }
            if (names.isNotEmpty()) {
                Store.log(Level.INFO, Source.SHARED, tr("Erhalten: {0}", names.joinToString()))
                Scheduler.uploadShared(this)
            }
            val msg = when {
                names.isEmpty() -> tr("Keine Datei erhalten")
                !Settings(this).isConfigured -> tr("Gespeichert; wird hochgeladen, sobald der Server eingerichtet ist")
                else -> tr("{0} wird an VitaLocal übertragen", names.joinToString())
            }
            runOnUiThread {
                Toast.makeText(applicationContext, msg, Toast.LENGTH_LONG).show()
                finish()
            }
        }.start()
    }
}
