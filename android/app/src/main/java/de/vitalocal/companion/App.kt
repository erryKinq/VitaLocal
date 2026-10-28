package de.vitalocal.companion

import android.app.Application

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        I18n.init(this)
        Store.init(this)
        DayRepo.init(this)
        FoodRepo.init(this)
        Notifier.createChannel(this)
        Reminders.createChannel(this)
        RestTimer.createChannel(this)
        Reminders.ensureDefaults(this)
        Reminders.schedule(this)
        LiveState.init(this)
        LiveSyncService.createChannel(this)
        LiveScheduler.ensureArmed(this)
        if (Settings(this).isConfigured) Scheduler.schedulePeriodic(this)
    }
}
