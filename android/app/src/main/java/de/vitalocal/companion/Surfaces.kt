package de.vitalocal.companion

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.Build
import android.service.quicksettings.Tile
import android.util.SizeF
import android.service.quicksettings.TileService
import android.view.View
import android.widget.RemoteViews

/** Home screen widget and quick settings tile: the two ways to log without opening the app. */
object Surfaces {
    const val QUICK_ML = 250.0

    fun update(context: Context) {
        val app = context.applicationContext
        val mgr = AppWidgetManager.getInstance(app)
        val ids = mgr.getAppWidgetIds(ComponentName(app, TodayWidget::class.java))
        if (ids.isNotEmpty()) TodayWidget.render(app, mgr, ids)
        mgr.getAppWidgetIds(ComponentName(app, GardenWidget::class.java)).takeIf { it.isNotEmpty() }?.let { GardenWidget.render(app, mgr, it) }
        mgr.getAppWidgetIds(ComponentName(app, FlameWidget::class.java)).takeIf { it.isNotEmpty() }?.let { FlameWidget.render(app, mgr, it) }
        runCatching { TileService.requestListeningState(app, ComponentName(app, WaterTile::class.java)) }
    }

    fun waterText(context: Context, d: TodayData): String {
        return "${formatMl(d.drinksTotal)} / ${formatMl(d.drinkGoalMl(Settings(context).waterGoal))}"
    }
}

fun formatMl(ml: Double): String {
    if (ml < 1000) return "${ml.toInt()} ml"
    val nf = java.text.NumberFormat.getNumberInstance(I18n.locale).apply { maximumFractionDigits = 2 }
    return "${nf.format(ml / 1000)} l"
}

class TodayWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, mgr: AppWidgetManager, ids: IntArray) {
        DayRepo.init(context)
        render(context, mgr, ids)
    }

    override fun onReceive(context: Context, intent: Intent) {
        DayRepo.init(context)
        Store.init(context)
        val day = DayRepo.today
        when (intent.action) {
            ACTION_WATER -> DayRepo.addDrink(context, day, intent.getIntExtra(EXTRA_ML, 250).toDouble(), "water")
            ACTION_SUPPLEMENT -> {
                val id = intent.getStringExtra(EXTRA_SUPPLEMENT) ?: return
                DayRepo.adjustSupplement(context, day, id, 1)
            }
            ACTION_HABIT -> {
                val id = intent.getStringExtra(EXTRA_HABIT) ?: return
                val habit = DayRepo.state.value.view(day).habits.firstOrNull { it.id == id } ?: return
                if (habit.kind == "count") DayRepo.adjustHabit(context, day, id, 1.0) else DayRepo.logHabit(context, day, id, 1.0)
            }
            else -> super.onReceive(context, intent)
        }
    }

    companion object {
        private const val ACTION_WATER = "de.vitalocal.companion.WIDGET_WATER"
        private const val ACTION_HABIT = "de.vitalocal.companion.WIDGET_HABIT"
        private const val EXTRA_HABIT = "habit"
        private const val ACTION_SUPPLEMENT = "de.vitalocal.companion.WIDGET_SUPPLEMENT"
        private const val EXTRA_SUPPLEMENT = "supplement"
        private const val EXTRA_ML = "ml"
        private val HABIT_SLOTS = listOf(R.id.w_habit1, R.id.w_habit2, R.id.w_habit3, R.id.w_habit4)
        private val DRINK_BUTTONS = listOf(R.id.w_add_200 to 200, R.id.w_add_250 to 250, R.id.w_add_300 to 300)

        fun render(context: Context, mgr: AppWidgetManager, ids: IntArray) {
            val d = DayRepo.state.value.view(DayRepo.today)
            val views = if (Build.VERSION.SDK_INT >= 31) {
                // The launcher picks the largest layout that fits the widget's current size.
                RemoteViews(mapOf(
                    SizeF(40f, 40f) to small(context, d),
                    SizeF(200f, 40f) to row(context, d),
                    SizeF(180f, 100f) to full(context, d),
                ))
            } else {
                full(context, d)
            }
            mgr.updateAppWidget(ids, views)
        }

        private fun small(context: Context, d: TodayData) = RemoteViews(context.packageName, R.layout.widget_small).apply {
            setTextViewText(R.id.w_water_short, formatMl(d.drinksTotal))
            setOnClickPendingIntent(R.id.w_root, water(context, 250))
        }

        private fun row(context: Context, d: TodayData) = RemoteViews(context.packageName, R.layout.widget_row).apply {
            setTextViewText(R.id.w_water, Surfaces.waterText(context, d))
            DRINK_BUTTONS.forEach { (id, ml) -> setOnClickPendingIntent(id, water(context, ml)) }
            setOnClickPendingIntent(R.id.w_water, openApp(context))
        }

        private fun full(context: Context, d: TodayData) = RemoteViews(context.packageName, R.layout.widget_today).apply {
            setTextViewText(R.id.w_water, Surfaces.waterText(context, d))
            DRINK_BUTTONS.forEach { (id, ml) -> setOnClickPendingIntent(id, water(context, ml)) }
            setOnClickPendingIntent(R.id.w_title, openApp(context))

            // open manual habits first, then supplements not yet complete; a tap counts one up
            val open = d.habits.filter { !it.automatic && it.done != true }
            val openSupplements = d.supplements.filter { !it.settled }
            HABIT_SLOTS.forEachIndexed { i, slot ->
                val h = open.getOrNull(i)
                val s = if (h == null) openSupplements.getOrNull(i - open.size) else null
                when {
                    h != null -> {
                        setViewVisibility(slot, View.VISIBLE)
                        val text = if (h.kind == "count") "+ ${h.name}  ${(h.value ?: 0.0).toInt()}/${h.target?.toInt() ?: 1}"
                        else "○ ${h.name}"
                        setTextViewText(slot, text)
                        setOnClickPendingIntent(slot, broadcast(context, ACTION_HABIT, 10 + i) { putExtra(EXTRA_HABIT, h.id) })
                    }
                    s != null -> {
                        setViewVisibility(slot, View.VISIBLE)
                        setTextViewText(slot, "◦ ${s.name}  ${s.count}/${s.perDay}")
                        setOnClickPendingIntent(slot, broadcast(context, ACTION_SUPPLEMENT, 20 + i) { putExtra(EXTRA_SUPPLEMENT, s.id) })
                    }
                    else -> setViewVisibility(slot, View.GONE)
                }
            }
            val allDone = (d.habits.any { !it.automatic } || d.supplements.isNotEmpty()) && open.isEmpty() && openSupplements.isEmpty()
            setViewVisibility(R.id.w_done, if (allDone) View.VISIBLE else View.GONE)
        }

        private fun water(context: Context, ml: Int) = broadcast(context, ACTION_WATER, ml) { putExtra(EXTRA_ML, ml) }

        private fun openApp(context: Context) = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)

        private fun broadcast(context: Context, action: String, code: Int, extras: Intent.() -> Unit): PendingIntent {
            val i = Intent(context, TodayWidget::class.java).setAction(action).apply(extras)
            return PendingIntent.getBroadcast(context, code, i, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        }
    }
}

/** Quick settings tile: one tap logs a glass of water (250 ml). */
class WaterTile : TileService() {
    override fun onStartListening() {
        super.onStartListening()
        DayRepo.init(this)
        show()
    }

    override fun onClick() {
        super.onClick()
        DayRepo.init(this)
        Store.init(this)
        DayRepo.addDrink(this, DayRepo.today, Surfaces.QUICK_ML, "water")
        show()
    }

    private fun show() {
        val tile = qsTile ?: return
        tile.label = "+250 ml"
        if (Build.VERSION.SDK_INT >= 29) tile.subtitle = Surfaces.waterText(this, DayRepo.state.value.view(DayRepo.today))
        tile.icon = Icon.createWithResource(this, R.drawable.ic_water)
        tile.state = Tile.STATE_INACTIVE
        tile.updateTile()
    }
}
