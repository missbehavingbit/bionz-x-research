package com.bionz.looklab

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicLong

class MainActivity : FlutterActivity() {

    private val mainHandler = Handler(Looper.getMainLooper())
    private val lookExecutor = Executors.newSingleThreadExecutor()
    private val previewGen = AtomicLong(0L)
    private var eventSink: EventChannel.EventSink? = null
    private val stateListener: (Map<String, Any>) -> Unit = { snap ->
        mainHandler.post {
            eventSink?.success(snap)
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, METHOD_CHANNEL)
            .setMethodCallHandler { call, result ->
                handleMethod(call, result)
            }

        EventChannel(flutterEngine.dartExecutor.binaryMessenger, EVENT_CHANNEL)
            .setStreamHandler(object : EventChannel.StreamHandler {
                override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                    eventSink = events
                    WifiSyncForegroundService.addListener(stateListener)
                    events?.success(WifiSyncForegroundService.getSnapshot(this@MainActivity))
                }

                override fun onCancel(arguments: Any?) {
                    WifiSyncForegroundService.removeListener(stateListener)
                    eventSink = null
                }
            })
    }

    private fun handleMethod(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "startService" -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                    checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                ) {
                    requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
                }
                val intent = Intent(this, WifiSyncForegroundService::class.java).apply {
                    action = WifiSyncForegroundService.ACTION_START
                }
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    startForegroundService(intent)
                } else {
                    startService(intent)
                }
                result.success(true)
            }
            "stopService" -> {
                val intent = Intent(this, WifiSyncForegroundService::class.java).apply {
                    action = WifiSyncForegroundService.ACTION_STOP
                }
                startService(intent)
                result.success(true)
            }
            "syncNow" -> {
                val intent = Intent(this, WifiSyncForegroundService::class.java).apply {
                    action = WifiSyncForegroundService.ACTION_SYNC_NOW
                }
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    startForegroundService(intent)
                } else {
                    startService(intent)
                }
                result.success(true)
            }
            "getConfig" -> {
                result.success(SyncConfig.load(this).toMap())
            }
            "saveConfig" -> {
                val args = call.arguments as? Map<*, *> ?: emptyMap<String, Any>()
                val current = SyncConfig.load(this)
                val updated = SyncConfig(
                    ssid = (args["ssid"] as? String) ?: current.ssid,
                    psk = (args["psk"] as? String) ?: current.psk,
                    host = (args["host"] as? String) ?: current.host,
                    port = (args["port"] as? Number)?.toInt() ?: current.port,
                    autoSuggestWifi = (args["autoSuggestWifi"] as? Boolean) ?: current.autoSuggestWifi
                )
                SyncConfig.save(this, updated)
                result.success(updated.toMap())
            }
            "getSnapshot" -> {
                result.success(WifiSyncForegroundService.getSnapshot(this))
            }
            "isIgnoringBatteryOptimizations" -> {
                val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
                result.success(pm.isIgnoringBatteryOptimizations(packageName))
            }
            "requestBatteryOptimizationExemption" -> {
                val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                    data = Uri.parse("package:$packageName")
                }
                startActivity(intent)
                result.success(true)
            }
            "openWifiSettings" -> {
                startActivity(Intent(Settings.ACTION_WIFI_SETTINGS))
                result.success(true)
            }
            "clearLogs" -> {
                WifiSyncForegroundService.clearLogs()
                result.success(true)
            }
            "getLookCatalog" -> {
                result.success(buildLookCatalogMap())
            }
            "saveLookPreset" -> {
                val args = call.arguments as? Map<*, *> ?: emptyMap<String, Any>()
                val store = LookPresetStore(this)
                store.savePreset(LookPreset.fromMap(args))
                WifiSyncForegroundService.refreshHookLabel(this)
                result.success(buildLookCatalogMap())
            }
            "deleteLookPreset" -> {
                val args = call.arguments as? Map<*, *> ?: emptyMap<String, Any>()
                val id = (args["id"] as? String) ?: ""
                LookPresetStore(this).deletePreset(id)
                WifiSyncForegroundService.refreshHookLabel(this)
                result.success(buildLookCatalogMap())
            }
            "setAutoApplyPreset" -> {
                val args = call.arguments as? Map<*, *> ?: emptyMap<String, Any>()
                val id = args["id"] as? String
                LookPresetStore(this).setAutoApplyPresetId(id)
                WifiSyncForegroundService.refreshHookLabel(this)
                result.success(buildLookCatalogMap())
            }
            "loadPhotoThumbnail" -> {
                val args = call.arguments as? Map<*, *> ?: emptyMap<String, Any>()
                val dcfName = (args["dcfName"] as? String) ?: ""
                lookExecutor.execute {
                    val bytes = try {
                        LookLabImageIO.loadThumbnailJpeg(this@MainActivity, dcfName)
                    } catch (_: Throwable) {
                        null
                    }
                    mainHandler.post { result.success(bytes) }
                }
            }
            "renderPhotoPreview" -> {
                val args = call.arguments as? Map<*, *> ?: emptyMap<String, Any>()
                val dcfName = (args["dcfName"] as? String) ?: ""
                val presetMap = args["preset"] as? Map<*, *> ?: emptyMap<String, Any>()
                val preset = LookPreset.fromMap(presetMap)
                val gen = previewGen.incrementAndGet()
                lookExecutor.execute {
                    if (gen != previewGen.get()) {
                        mainHandler.post { result.success(null) }
                        return@execute
                    }
                    val bytes = try {
                        LookLabImageIO.renderPreviewJpeg(this@MainActivity, dcfName, preset)
                    } catch (_: Throwable) {
                        null
                    }
                    mainHandler.post { result.success(bytes) }
                }
            }
            "exportGradedPhoto" -> {
                val args = call.arguments as? Map<*, *> ?: emptyMap<String, Any>()
                val dcfName = (args["dcfName"] as? String) ?: ""
                val presetMap = args["preset"] as? Map<*, *> ?: emptyMap<String, Any>()
                val preset = LookPreset.fromMap(presetMap)
                lookExecutor.execute {
                    try {
                        val res = LookLabImageIO.exportGradedToMediaStore(this@MainActivity, dcfName, preset)
                        mainHandler.post {
                            result.success(
                                mapOf(
                                    "localPath" to res.localPath,
                                    "elapsedMs" to res.elapsedMs,
                                    "width" to res.width,
                                    "height" to res.height
                                )
                            )
                        }
                    } catch (t: Throwable) {
                        mainHandler.post {
                            result.error("EXPORT_FAILED", t.message ?: "Export failed", null)
                        }
                    }
                }
            }
            else -> result.notImplemented()
        }
    }

    private fun buildLookCatalogMap(): Map<String, Any?> {
        val store = LookPresetStore(this)
        return mapOf(
            "stocks" to SpektraFilmEngine.stockInfos().map { it.toMap() },
            "papers" to SpektraFilmEngine.paperInfos().map { it.toMap() },
            "presets" to store.listPresets().map { it.toMap() },
            "autoApplyPresetId" to store.getAutoApplyPresetId()
        )
    }

    override fun onDestroy() {
        WifiSyncForegroundService.removeListener(stateListener)
        lookExecutor.shutdownNow()
        super.onDestroy()
    }

    companion object {
        private const val METHOD_CHANNEL = "com.bionz.looklab/sync"
        private const val EVENT_CHANNEL = "com.bionz.looklab/events"
    }
}
