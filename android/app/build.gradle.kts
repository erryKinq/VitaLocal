import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Release signing: own key outside the repo (build.sh creates it), otherwise the debug key.
val signingProps = Properties().apply {
    val dir = System.getenv("VITALOCAL_SIGNING_DIR") ?: (System.getProperty("user.home") + "/Library/Application Support/VitaLocal")
    val f = file("$dir/android-signing.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "de.vitalocal.companion"
    compileSdk = 36

    defaultConfig {
        applicationId = "de.vitalocal.companion"
        minSdk = 29
        targetSdk = 36
        versionCode = 39
        versionName = "1.9.2"
        // zxing-cpp ships native code for every ABI; the phone (and the emulator) are arm64
        ndk { abiFilters += listOf("arm64-v8a") }
    }

    signingConfigs {
        if (signingProps.getProperty("storeFile") != null) {
            create("release") {
                storeFile = file(signingProps.getProperty("storeFile"))
                storePassword = signingProps.getProperty("storePassword")
                keyAlias = signingProps.getProperty("keyAlias")
                keyPassword = signingProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    // tests expect the German source texts and day boundaries in Berlin, whatever the machine is set to
    testOptions {
        unitTests.all {
            it.systemProperty("user.language", "de")
            it.systemProperty("user.country", "DE")
            it.systemProperty("user.timezone", "Europe/Berlin")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2026.05.01"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.activity:activity-compose:1.12.4")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.10.0")
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.documentfile:documentfile:1.1.0")
    implementation("androidx.work:work-runtime-ktx:2.12.0")
    // Health Connect as data source for people without a Gadgetbridge watch
    implementation("androidx.health.connect:connect-client:1.1.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // barcode scan: CameraX preview + analysis, zxing-cpp decoder (FOSS, no Google services)
    implementation("androidx.camera:camera-camera2:1.6.2")
    implementation("androidx.camera:camera-lifecycle:1.6.2")
    implementation("androidx.camera:camera-view:1.6.2")
    implementation("io.github.zxing-cpp:android:3.1.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
}
