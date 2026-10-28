package de.vitalocal.companion.ui

import android.content.Context
import android.graphics.Region
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.asComposePath
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.core.graphics.PathParser
import org.json.JSONObject

/** Body outlines from MuscleMap by Melih Colpan (MIT License, https://github.com/melihcolpan/MuscleMap),
 *  converted by tools/musclemap_to_json.py into assets/body-paths.json. */
class BodyShape(val muscle: String?, val path: Path, val region: Region)
class BodySide(val left: Float, val top: Float, val width: Float, val height: Float, val shapes: List<BodyShape>)

object BodyPaths {
    @Volatile private var figures: Map<String, Pair<BodySide, BodySide>>? = null

    /** "male" or "female" → (front, back); parsed once from the bundled asset. */
    fun get(context: Context, figure: String): Pair<BodySide, BodySide> {
        val all = figures ?: synchronized(this) {
            figures ?: parse(context.assets.open("body-paths.json").bufferedReader().use { it.readText() }).also { figures = it }
        }
        return all[figure] ?: all.getValue("male")
    }

    private fun parse(text: String): Map<String, Pair<BodySide, BodySide>> {
        val figs = JSONObject(text).getJSONObject("figures")
        return figs.keys().asSequence().associateWith { name ->
            val f = figs.getJSONObject(name)
            side(f.getJSONObject("front")) to side(f.getJSONObject("back"))
        }
    }

    private fun side(o: JSONObject): BodySide {
        val box = o.getJSONArray("viewBox")
        val shapes = o.getJSONArray("shapes")
        val clip = Region(-10000, -10000, 10000, 10000)
        return BodySide(
            box.getDouble(0).toFloat(), box.getDouble(1).toFloat(), box.getDouble(2).toFloat(), box.getDouble(3).toFloat(),
            (0 until shapes.length()).map { i ->
                val sh = shapes.getJSONObject(i)
                val ds = sh.getJSONArray("d")
                val path = android.graphics.Path()
                for (j in 0 until ds.length()) path.addPath(PathParser.createPathFromPathData(ds.getString(j)))
                BodyShape(sh.optString("muscle").takeIf { !sh.isNull("muscle") && it.isNotEmpty() }, path.asComposePath(),
                    Region().apply { setPath(path, clip) })
            },
        )
    }
}

/**
 * Front and back side by side; [fill] gives a muscle's colour (null: the plain muscle colour),
 * [onPick] makes muscles tappable, [selected] is outlined.
 */
@Composable
fun BodyMap(
    fill: (String) -> Color?,
    modifier: Modifier = Modifier,
    figure: String = "male",
    selected: String? = null,
    height: Dp = 300.dp,
    onPick: ((String) -> Unit)? = null,
) {
    val (front, back) = BodyPaths.get(LocalContext.current, figure)
    val skin = MaterialTheme.colorScheme.surfaceContainerHighest
    val base = MaterialTheme.colorScheme.outline
    val line = MaterialTheme.colorScheme.surfaceContainer
    val mark = MaterialTheme.colorScheme.onSurface
    Row(modifier.fillMaxWidth().height(height), horizontalArrangement = Arrangement.spacedBy(12.dp, androidx.compose.ui.Alignment.CenterHorizontally)) {
        for (side in listOf(front, back)) {
            Canvas(
                Modifier.weight(1f, fill = false).aspectRatio(side.width / side.height).pointerInput(side, onPick) {
                    if (onPick == null) return@pointerInput
                    detectTapGestures { p ->
                        val s = size.height / side.height
                        val x = (p.x / s + side.left).toInt()
                        val y = (p.y / s + side.top).toInt()
                        // topmost shape first: the shapes drawn last lie on top
                        side.shapes.lastOrNull { it.muscle != null && it.region.contains(x, y) }?.muscle?.let(onPick)
                    }
                },
            ) {
                val s = size.height / side.height
                withTransform({
                    scale(s, s, pivot = Offset.Zero)
                    translate(-side.left, -side.top)
                }) {
                    for (sh in side.shapes) {
                        val color = sh.muscle?.let { fill(it) ?: base } ?: skin
                        drawPath(sh.path, color)
                        drawPath(sh.path, line, style = Stroke(width = 3f))
                    }
                    side.shapes.filter { it.muscle != null && it.muscle == selected }.forEach {
                        drawPath(it.path, mark, style = Stroke(width = 6f))
                    }
                }
            }
        }
    }
}
