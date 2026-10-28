package de.vitalocal.companion

import android.content.Context
import android.net.Uri
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.core.content.edit
import java.net.InetAddress
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** User settings. The API token is encrypted with a key that never leaves the Android Keystore. */
class Settings(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString("server_url", "") ?: ""
        set(v) = prefs.edit { putString("server_url", normalizeUrl(v)) }

    var token: String
        get() = prefs.getString("token", null)?.let { runCatching { TokenCipher.decrypt(it) }.getOrNull() } ?: ""
        set(v) = prefs.edit { if (v.isEmpty()) remove("token") else putString("token", TokenCipher.encrypt(v)) }

    val hasToken: Boolean get() = prefs.contains("token")

    /** Folder (SAF tree) that holds Gadgetbridge's automatic ZIP export. */
    var gadgetbridgeFolder: Uri?
        get() = prefs.getString("gb_tree", null)?.let(Uri::parse)
        set(v) = prefs.edit { putString("gb_tree", v?.toString()) }

    /** Where measurements come from: "gadgetbridge" (watch), "health_connect" or "none" (logging only). */
    var dataSource: String
        get() = prefs.getString("data_source", "gadgetbridge") ?: "gadgetbridge"
        set(v) = prefs.edit { putString("data_source", v) }

    var intervalHours: Int
        get() = prefs.getInt("interval_h", 3)
        set(v) = prefs.edit { putInt("interval_h", v) }

    /** Figure of the muscle map: "male" or "female". */
    var bodyFigure: String
        get() = prefs.getString("body_figure", "male") ?: "male"
        set(v) = prefs.edit { putString("body_figure", v) }

    /** Delete processed Gadgetbridge exports from before today (never the newest or the auto-export file). */
    var cleanupEnabled: Boolean
        get() = prefs.getBoolean("cleanup", true)
        set(v) = prefs.edit { putBoolean("cleanup", v) }

    var notifyErrors: Boolean
        get() = prefs.getBoolean("notify_errors", true)
        set(v) = prefs.edit { putBoolean("notify_errors", v) }

    var waterGoal: Int
        get() = prefs.getInt("water_goal", 2000)
        set(v) = prefs.edit { putInt("water_goal", v) }

    /** Live sync: the app lets Gadgetbridge sync the watch and export its database every few minutes. */
    var liveEnabled: Boolean
        get() = prefs.getBoolean("live", false)
        set(v) = prefs.edit { putBoolean("live", v) }

    var liveConfig: LiveConfig
        get() = LiveConfig(
            dayMinutes = prefs.getInt("live_day_min", 12),
            nightHours = prefs.getInt("live_night_h", 4),
            nightStart = java.time.LocalTime.ofSecondOfDay(prefs.getInt("live_night_start", 22 * 60) * 60L),
            nightEnd = java.time.LocalTime.ofSecondOfDay(prefs.getInt("live_night_end", 6 * 60) * 60L),
        )
        set(v) = prefs.edit {
            putInt("live_day_min", v.dayMinutes)
            putInt("live_night_h", v.nightHours)
            putInt("live_night_start", v.nightStart.toSecondOfDay() / 60)
            putInt("live_night_end", v.nightEnd.toSecondOfDay() / 60)
        }

    /** Gadgetbridge package to talk to; empty = the installed one (release before nightly). */
    var gbPackage: String
        get() = prefs.getString("gb_package", "") ?: ""
        set(v) = prefs.edit { putString("gb_package", v) }

    /** The database export file found in the Gadgetbridge folder (document URI). */
    var gbDatabaseDoc: Uri?
        get() = prefs.getString("gb_db_doc", null)?.let(Uri::parse)
        set(v) = prefs.edit { putString("gb_db_doc", v?.toString()) }

    val isConfigured: Boolean get() = serverUrl.isNotEmpty() && hasToken

    companion object {
        val INTERVALS = listOf(1, 3, 6, 12)
        val LIVE_DAY_MINUTES = listOf(10, 12, 15, 30)
        val LIVE_NIGHT_HOURS = listOf(1, 2, 4, 8)

        fun normalizeUrl(raw: String): String = raw.trim().trimEnd('/')

        /** null if acceptable, otherwise a German error message. */
        fun validateUrl(raw: String): String? {
            val uri = Uri.parse(normalizeUrl(raw))
            val host = uri.host
            return when {
                uri.scheme !in setOf("http", "https") || host.isNullOrEmpty() ->
                    tr("Adresse muss mit https:// oder http:// beginnen")
                uri.scheme == "http" && !isLocalHost(host) ->
                    tr("Unverschlüsseltes http:// nur für Adressen im lokalen Netz oder im WireGuard")
                else -> null
            }
        }

        /** Private address space and local-only names: acceptable for plain HTTP. */
        fun isLocalHost(host: String): Boolean {
            val h = host.lowercase().trim('[', ']')
            if ('.' !in h && ':' !in h) return true
            if (listOf(".local", ".lan", ".home", ".internal", ".arpa", ".localhost").any(h::endsWith)) return true
            val parts = h.split('.').mapNotNull(String::toIntOrNull)
            if (parts.size == 4 && h.count { it == '.' } == 3) {
                val (a, b) = parts
                return a == 10 || a == 127 || (a == 172 && b in 16..31) || (a == 192 && b == 168) ||
                    (a == 100 && b in 64..127)
            }
            if (':' in h) {
                return runCatching { InetAddress.getByName(h) }.getOrNull()?.let {
                    it.isLoopbackAddress || it.isSiteLocalAddress || it.isLinkLocalAddress || h.startsWith("fd")
                } ?: false
            }
            return false
        }
    }
}

private object TokenCipher {
    private const val ALIAS = "vitalocal_token"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build(),
        )
        return gen.generateKey()
    }

    fun encrypt(plain: String): String {
        val c = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val out = c.iv + c.doFinal(plain.toByteArray())
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    fun decrypt(stored: String): String {
        val raw = Base64.decode(stored, Base64.NO_WRAP)
        val c = Cipher.getInstance(TRANSFORMATION)
        c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw, 0, 12))
        return String(c.doFinal(raw, 12, raw.size - 12))
    }
}
