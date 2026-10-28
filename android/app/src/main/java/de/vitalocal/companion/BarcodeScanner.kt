package de.vitalocal.companion

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import zxingcpp.BarcodeReader
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Product barcodes on food packaging. */
private val FORMATS = setOf(
    BarcodeReader.Format.EAN_13, BarcodeReader.Format.EAN_8, BarcodeReader.Format.UPC_A, BarcodeReader.Format.UPC_E,
)

/**
 * Camera preview that reports the first EAN/UPC code it reads (CameraX + zxing-cpp, on the device,
 * no Google services). Without the camera permission, or for a damaged code, the number can be typed.
 */
@Composable
fun BarcodeScanner(onCode: (String) -> Unit, onCancel: () -> Unit) {
    val ctx = LocalContext.current
    var granted by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(ctx, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    var asked by remember { mutableStateOf(false) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted = it; asked = true }
    LaunchedEffect(Unit) { if (!granted) launcher.launch(Manifest.permission.CAMERA) }
    var typed by remember { mutableStateOf("") }

    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(tr("Barcode scannen"), style = MaterialTheme.typography.titleMedium)
        if (granted) {
            Box(Modifier.fillMaxWidth().aspectRatio(4f / 3f).clipToBounds().background(Color.Black), contentAlignment = Alignment.Center) {
                CameraPreview(onCode)
                Box(Modifier.fillMaxWidth(0.8f).aspectRatio(3f).background(Color.White.copy(alpha = 0.12f)))
            }
            Text(tr("Strichcode in den hellen Bereich halten."), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else if (asked) {
            Text(tr("Ohne Kamera-Berechtigung kann die App nicht scannen. Die Nummer lässt sich eintippen."),
                color = MaterialTheme.colorScheme.error)
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = typed, onValueChange = { typed = it.filter(Char::isDigit).take(14) },
                label = { Text(tr("Nummer eintippen")) }, singleLine = true, modifier = Modifier.weight(1f),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            )
            Button(enabled = typed.length >= 6, onClick = { onCode(typed) }) { Text(tr("Suchen")) }
        }
        TextButton(onClick = onCancel) { Text(tr("Abbrechen")) }
    }
}

@Composable
private fun CameraPreview(onCode: (String) -> Unit) {
    val ctx = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val executor = remember { Executors.newSingleThreadExecutor() }
    val done = remember { AtomicBoolean(false) }
    val reader = remember { BarcodeReader(BarcodeReader.Options(formats = FORMATS, tryHarder = true)) }
    val view = remember { PreviewView(ctx).apply {
            scaleType = PreviewView.ScaleType.FILL_CENTER
            // a TextureView stays inside the (clipped) frame in a bottom sheet; a SurfaceView does not
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        } }

    DisposableEffect(owner) {
        val future = ProcessCameraProvider.getInstance(ctx)
        var provider: ProcessCameraProvider? = null
        future.addListener({
            val p = future.get().also { provider = it }
            val preview = Preview.Builder().build().also { it.surfaceProvider = view.surfaceProvider }
            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
            analysis.setAnalyzer(executor) { image ->
                val code = runCatching { image.use { reader.read(it) } }.getOrNull()
                    ?.firstOrNull { !it.text.isNullOrBlank() && it.text!!.all(Char::isDigit) }?.text
                if (code != null && done.compareAndSet(false, true)) {
                    ContextCompat.getMainExecutor(ctx).execute { onCode(code) }
                }
            }
            runCatching {
                p.unbindAll()
                p.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            }.onFailure { Store.log(Level.WARN, null, tr("Kamera nicht verfügbar: {0}", it.describe())) }
        }, ContextCompat.getMainExecutor(ctx))
        onDispose {
            provider?.unbindAll()
            executor.shutdown()
        }
    }
    AndroidView(factory = { view }, modifier = Modifier.fillMaxSize())
}
