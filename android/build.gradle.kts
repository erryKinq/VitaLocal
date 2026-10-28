plugins {
    id("com.android.application") version "9.4.1" apply false
    // AGP 9 compiles Kotlin itself; this also lifts the Kotlin compiler to the plugin's version.
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.20" apply false
}
