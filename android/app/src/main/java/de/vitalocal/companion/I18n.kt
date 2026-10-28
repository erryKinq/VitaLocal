package de.vitalocal.companion

import android.content.Context
import java.util.Locale

/**
 * App language: the one chosen in the WebUI's setup (stored with the capabilities), else German
 * when the phone is set to German and English otherwise. Texts are written in German in the code
 * and looked up here (`tr("Heute")`); `{0}`, `{1}` … take the arguments. A text without an
 * English entry stays German. Many texts are read once, so a new choice applies from the next start.
 */
object I18n {
    private var chosen: String? = null

    /** Before any text is read (Application.onCreate). */
    fun init(context: Context) {
        chosen = Caps.storedLang(context)
    }

    val german: Boolean get() = (chosen ?: Locale.getDefault().language) == "de"
    val locale: Locale get() = if (german) Locale.GERMANY else Locale.getDefault()

    /** Date patterns are written German style ("EEEE, d. MMMM"); English drops the day's dot. */
    fun pattern(p: String): String = if (german) p else p.replace("d. ", "d ")

    fun format(text: String, args: Array<out Any?>): String {
        var out = if (german) text else EN[text] ?: text
        args.forEachIndexed { i, a -> out = out.replace("{$i}", a.toString()) }
        return out
    }
}

fun tr(text: String, vararg args: Any?): String = I18n.format(text, args)
