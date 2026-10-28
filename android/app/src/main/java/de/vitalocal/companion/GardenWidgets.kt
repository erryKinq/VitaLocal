package de.vitalocal.companion

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.view.View
import android.widget.RemoteViews
import de.vitalocal.companion.ui.plantRes
import de.vitalocal.companion.ui.stageKey

/** Home screen widgets for the habit rewards: the garden and the flame counter. */
private fun openApp(context: Context) = PendingIntent.getActivity(
    context, 1, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)

private fun rewards(): HabitRewards? = DayRepo.state.value.view(DayRepo.today).rewards

/** Up to eight plants, the ones that need water first; the rest by size. */
internal fun gardenOrder(garden: List<Plant>): List<Plant> =
    garden.sortedWith(compareBy<Plant> { if (it.health == "healthy") 1 else 0 }.thenByDescending { it.growth })

class GardenWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, mgr: AppWidgetManager, ids: IntArray) {
        DayRepo.init(context)
        render(context, mgr, ids)
    }

    companion object {
        private val SLOTS = listOf(R.id.g_plant1, R.id.g_plant2, R.id.g_plant3, R.id.g_plant4,
            R.id.g_plant5, R.id.g_plant6, R.id.g_plant7, R.id.g_plant8)

        fun render(context: Context, mgr: AppWidgetManager, ids: IntArray) {
            val r = rewards()
            val views = RemoteViews(context.packageName, R.layout.widget_garden).apply {
                setOnClickPendingIntent(R.id.g_root, openApp(context))
                if (r == null || r.garden.isEmpty()) {
                    setTextViewText(R.id.g_title, tr("Gewohnheiten-Garten"))
                    SLOTS.forEach { setViewVisibility(it, View.INVISIBLE) }
                    setViewVisibility(R.id.g_row2, View.GONE)
                    setTextViewText(R.id.g_hint, tr("Noch keine Daten – App einmal öffnen"))
                    return@apply
                }
                setTextViewText(R.id.g_title, tr("{0} Flammen · Level {1} {2}", r.flames, r.level, r.levelTitleDe))
                val plants = gardenOrder(r.garden)
                SLOTS.forEachIndexed { i, slot ->
                    val p = plants.getOrNull(i)
                    if (p == null) {
                        setViewVisibility(slot, View.INVISIBLE)
                    } else {
                        setViewVisibility(slot, View.VISIBLE)
                        // the same illustration as in the app and the WebUI, the name below
                        setTextViewCompoundDrawables(slot, 0, plantRes(stageKey(p.stage)), 0, 0)
                        setTextViewText(slot, plantText(p))
                    }
                }
                setViewVisibility(R.id.g_row2, if (plants.size > 4) View.VISIBLE else View.GONE)
                val thirsty = r.garden.count { it.health != "healthy" }
                val more = if (plants.size > SLOTS.size) tr(" · +{0} weitere", plants.size - SLOTS.size) else ""
                setTextViewText(R.id.g_hint, (if (thirsty > 0) tr("{0} brauchen Wasser", thirsty) else tr("Alles grün")) + more)
            }
            mgr.updateAppWidget(ids, views)
        }

        /** The habit's name under its plant; thirsty plants say so, wilting ones are greyed. */
        private fun plantText(p: Plant): CharSequence {
            val sb = SpannableStringBuilder()
            val start = sb.length
            sb.append(p.name)
            if (p.health != "healthy") {
                val s = sb.length
                sb.append(if (p.health == "wilting") tr("\nwelkt") else tr("\ndurstig"))
                sb.setSpan(RelativeSizeSpan(0.85f), s, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            }
            if (p.health == "wilting") sb.setSpan(ForegroundColorSpan(Color.parseColor("#99F6E4")), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            return sb
        }
    }
}

class FlameWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, mgr: AppWidgetManager, ids: IntArray) {
        DayRepo.init(context)
        render(context, mgr, ids)
    }

    companion object {
        fun render(context: Context, mgr: AppWidgetManager, ids: IntArray) {
            val r = rewards()
            val views = RemoteViews(context.packageName, R.layout.widget_flames).apply {
                setOnClickPendingIntent(R.id.f_root, openApp(context))
                setTextViewText(R.id.f_flames, tr("{0} Flammen{1}", r?.flames ?: "–", if ((r?.flamesToday ?: 0) > 0) "  +${r!!.flamesToday}" else ""))
                setTextViewText(R.id.f_level, r?.let { tr("Level {0} · {1} · noch {2}", it.level, it.levelTitleDe, it.toNextLevel) } ?: tr("App einmal öffnen"))
                setProgressBar(R.id.f_progress, 100, ((r?.levelProgress ?: 0f) * 100).toInt(), false)
                setTextViewText(R.id.f_streak, r?.bestStreak?.let { tr("Beste Serie: {0}, {1}", it.label, it.span) } ?: tr("Noch keine laufende Serie"))
            }
            mgr.updateAppWidget(ids, views)
        }
    }
}
