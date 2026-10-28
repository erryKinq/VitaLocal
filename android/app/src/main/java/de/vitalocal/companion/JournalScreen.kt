package de.vitalocal.companion

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import de.vitalocal.companion.ui.DayArrows
import de.vitalocal.companion.ui.Muted
import de.vitalocal.companion.ui.ScreenHeader
import de.vitalocal.companion.ui.VCard
import de.vitalocal.companion.ui.Vita
import de.vitalocal.companion.ui.WebLink
import de.vitalocal.companion.ui.dayLabel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Same pause as the WebUI's editor before a text is saved. */
private const val IDLE_MS = 1200L

/**
 * Tab "Journal": the free text of one of the last 7 days, saved like in the WebUI (after a short
 * pause, over the version it started from), and the day's check-ins with symptoms and notes open
 * (the overview has the same card folded). Reviews and search are in the WebUI.
 */
@Composable
fun JournalScreen() {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val state by JournalRepo.state.collectAsState()
    var day by remember { mutableStateOf(DayRepo.today) }
    val d = state.day(day)

    LaunchedEffect(day) { JournalRepo.refresh(ctx, day) }
    LaunchedEffect(day) { DayRepo.refresh(ctx, day) }
    // keyed on Unit: a key on [day] would restart this on every day change and jump back at once
    LifecycleResumeEffect(Unit) {
        if (day != DayRepo.today) day = DayRepo.today
        scope.launch { JournalRepo.refresh(ctx, day); DayRepo.refresh(ctx, day) }
        onPauseOrDispose { }
    }
    // autosave: a pause in typing sends the draft (a conflict waits for the user's choice)
    LaunchedEffect(day, d.draft) {
        if (!d.unsaved || d.conflict) return@LaunchedEffect
        delay(IDLE_MS)
        JournalRepo.save(ctx, day)
    }

    PullToRefreshBox(isRefreshing = state.busy && d.draft == null, onRefresh = { scope.launch { JournalRepo.refresh(ctx, day); DayRepo.refresh(ctx, day) } }) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            ScreenHeader(tr("Journal"), dayLabel(day), R.drawable.ic_t_notebook, Vita.colors.mind) {
                DayArrows(day) { day = it }
            }
            if (d.conflict) ConflictCard(d, onTake = { JournalRepo.discardDraft(ctx, day) },
                onKeep = { scope.launch { JournalRepo.keepDraft(ctx, day) } })
            VCard(tr("Tagebuch"), R.drawable.ic_t_writing, tint = Vita.colors.mind) {
                OutlinedTextField(
                    value = d.shown, onValueChange = { JournalRepo.edit(ctx, day, it.take(20000)) },
                    modifier = Modifier.fillMaxWidth(), minLines = 10,
                    placeholder = { Text(tr("Wie war dein Tag? Was hat dich beschäftigt?")) },
                )
                Muted(when {
                    !d.loaded && d.draft == null -> if (state.busy) tr("Lädt …") else ""
                    d.conflict -> tr("Nicht gespeichert – in der WebUI geändert")
                    d.unsaved -> if (state.busy) tr("Speichert …") else tr("Entwurf, noch nicht gespeichert")
                    d.text.isEmpty() -> tr("Noch kein Eintrag für diesen Tag")
                    else -> tr("Gespeichert")
                })
                state.error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
                WebLink(tr("Rückblicke und Suche in der WebUI"), "#/journal/reviews")
            }
            CheckinCard(day, expanded = true)
        }
    }
}

/** The text was changed elsewhere since the draft started: take that text or keep the draft. */
@Composable
private fun ConflictCard(d: JournalDay, onTake: () -> Unit, onKeep: () -> Unit) {
    VCard(tr("In der WebUI geändert"), R.drawable.ic_t_alert_triangle, tint = Vita.colors.warn) {
        Muted(tr("Während du geschrieben hast, wurde der Text woanders gespeichert. Dort steht jetzt:"))
        Text(d.text.ifEmpty { tr("(leer)") }, style = MaterialTheme.typography.bodyMedium, maxLines = 8)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = onTake) { Text(tr("Diesen übernehmen")) }
            FilledTonalButton(onClick = onKeep) { Text(tr("Meinen behalten")) }
        }
    }
}
