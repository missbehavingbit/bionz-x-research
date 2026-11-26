package com.bionz.looklab

import android.content.Context
import java.io.File

data class SyncConfig(
    val ssid: String = DEFAULT_SSID,
    val psk: String = DEFAULT_PSK,
    val host: String = DEFAULT_HOST,
    val port: Int = DEFAULT_PORT,
    val autoSuggestWifi: Boolean = true
) {
    fun toMap(): Map<String, Any> = mapOf(
        "ssid" to ssid,
        "psk" to psk,
        "host" to host,
        "port" to port,
        "autoSuggestWifi" to autoSuggestWifi
    )

    companion object {
        const val DEFAULT_SSID = "A6400-5G-SYNC"
        const val DEFAULT_PSK = "a6400-wifisync"
        const val DEFAULT_HOST = "192.168.122.1"
        const val DEFAULT_PORT = 8765
        private const val PREFS_NAME = "bionz_wifisync_prefs"

        fun load(context: Context): SyncConfig {
            val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            return SyncConfig(
                ssid = prefs.getString("ssid", DEFAULT_SSID) ?: DEFAULT_SSID,
                psk = prefs.getString("psk", DEFAULT_PSK) ?: DEFAULT_PSK,
                host = prefs.getString("host", DEFAULT_HOST) ?: DEFAULT_HOST,
                port = prefs.getInt("port", DEFAULT_PORT).coerceIn(1, 65535),
                autoSuggestWifi = prefs.getBoolean("autoSuggestWifi", true)
            )
        }

        fun save(context: Context, cfg: SyncConfig) {
            context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit()
                .putString("ssid", cfg.ssid.trim().ifEmpty { DEFAULT_SSID })
                .putString("psk", cfg.psk)
                .putString("host", cfg.host.trim().ifEmpty { DEFAULT_HOST })
                .putInt("port", cfg.port.coerceIn(1, 65535))
                .putBoolean("autoSuggestWifi", cfg.autoSuggestWifi)
                .apply()
        }
    }
}

data class CameraStatus(
    val cameraId: String,
    val fw: String,
    val queueHead: Int,
    val queueLen: Int,
    val ack: Int,
    val powerState: Int,
    val apChanspec: Int,
    val apWidth: Int,
    val uptimeMs: Long
) {
    fun powerStateLabel(): String = when (powerState) {
        0 -> "OFF"
        1 -> "STANDBY"
        2 -> "ACTIVE"
        else -> "UNKNOWN($powerState)"
    }
}

data class ManifestEntry(
    val seqId: Int,
    val name: String,
    val ext: String,
    val sizeBytes: Long,
    val mtime: Long,
    val crc32Hex: String
)

data class SyncedFileRecord(
    val cameraId: String,
    val dcfName: String,
    val seqId: Int,
    val sizeBytes: Long,
    val crc32Hex: String,
    val localPath: String,
    val syncedAtMs: Long
) {
    fun toMap(): Map<String, Any> = mapOf(
        "cameraId" to cameraId,
        "dcfName" to dcfName,
        "seqId" to seqId,
        "sizeBytes" to sizeBytes,
        "crc32" to crc32Hex,
        "localPath" to localPath,
        "syncedAtMs" to syncedAtMs
    )
}

/**
 * Extension point for automatic mobile BIONZ Look Lab processing once a verified
 * .JPG or .ARW photo is saved locally.
 */
interface PostTransferHook {
    val name: String
    fun onFileVerified(record: SyncedFileRecord, verifiedFile: File? = null)
}

class NoOpPostTransferHook : PostTransferHook {
    override val name: String = "NoOpPostTransferHook (Transfer-only v1)"
    override fun onFileVerified(record: SyncedFileRecord, verifiedFile: File?) {
        // Transfer-only fallback hook.
    }
}
