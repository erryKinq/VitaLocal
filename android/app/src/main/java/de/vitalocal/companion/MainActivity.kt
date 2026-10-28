package de.vitalocal.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.BackHandler
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import de.vitalocal.companion.ui.AreaTheme
import de.vitalocal.companion.ui.LocalOpenSettings
import de.vitalocal.companion.ui.VIcon
import de.vitalocal.companion.ui.VitaTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Caps.load(this)
        setContent { VitaTheme { AppScreen() } }
    }

    override fun onResume() {
        super.onResume()
        Scheduler.syncHealthConnect(this)
        // Android's background budget for the live sync starts over while the app is in front
        if (LiveState.state.value.paused) {
            LiveState.resume()
            LiveScheduler.arm(this)
        } else {
            LiveScheduler.ensureArmed(this)
        }
    }
}

/** `module`: the area that hides this tab when switched off on the server (null: always shown). */
private data class Tab(val key: String, val label: String, val icon: Int, val module: String?)

private val TABS = listOf(
    Tab("today", tr("Übersicht"), R.drawable.ic_t_home, null), Tab("food", tr("Ernährung"), R.drawable.ic_t_salad, "nutrition"),
    Tab("habits", tr("Gewohnheiten"), R.drawable.ic_t_plant_2, "habits"), Tab("training", tr("Training"), R.drawable.ic_t_barbell, "training"),
    Tab("journal", tr("Journal"), R.drawable.ic_t_notebook, "journal"),
)

@Composable
private fun AppScreen() {
    val ctx = LocalContext.current
    val caps by Caps.state.collectAsState()
    val tabs = TABS.filter { it.module == null || caps.on(it.module) }
    var tabKey by rememberSaveable { mutableStateOf("today") }
    val tab = tabs.firstOrNull { it.key == tabKey } ?: tabs.first()
    // settings (with the sync status) lie over the tabs; back closes them. Unconfigured apps start there.
    var settings by rememberSaveable { mutableStateOf<String?>(if (Settings(ctx).isConfigured) null else "") }
    BackHandler(enabled = settings != null) { settings = null }
    CompositionLocalProvider(LocalOpenSettings provides { section -> settings = section ?: "" }) {
        val open = settings
        if (open != null) {
            Scaffold { padding ->
                Box(Modifier.fillMaxSize().padding(padding)) { SettingsScreen(section = open, onClose = { settings = null }) }
            }
        } else Scaffold(
            bottomBar = {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surfaceContainer) {
                    tabs.forEach { t ->
                        NavigationBarItem(selected = tab == t, onClick = { tabKey = t.key }, icon = { VIcon(t.icon, size = 22.dp) },
                            // five tabs leave too little room for "Gewohnheiten" at the default size
                            label = { Text(t.label, maxLines = 1, style = if (tabs.size > 4) MaterialTheme.typography.labelSmall else LocalTextStyle.current) })
                    }
                }
            },
        ) { padding ->
            Box(Modifier.fillMaxSize().padding(padding)) {
                Column {
                    when (tab.key) {
                        "food" -> AreaTheme({ it.food }) { FoodScreen() }
                        "habits" -> AreaTheme({ it.habit }) { HabitsScreen() }
                        "training" -> AreaTheme({ it.train }) { TrainingScreen() }
                        "journal" -> AreaTheme({ it.mind }) { JournalScreen() }
                        else -> AreaTheme({ it.day }) { TodayScreen() }
                    }
                }
            }
        }
    }
}
