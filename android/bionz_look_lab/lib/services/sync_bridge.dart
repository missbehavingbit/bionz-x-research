import 'package:flutter/services.dart';

import '../models/look_models.dart';
import '../models/sync_models.dart';

abstract class SyncBridge {
  Stream<SyncSnapshot> get events;
  Future<SyncSnapshot> getSnapshot();
  Future<SyncConfig> getConfig();
  Future<SyncConfig> saveConfig(SyncConfig config);
  Future<void> startService();
  Future<void> stopService();
  Future<void> syncNow();
  Future<bool> isIgnoringBatteryOptimizations();
  Future<void> requestBatteryOptimizationExemption();
  Future<void> openWifiSettings();
  Future<void> clearLogs();

  Future<LookCatalog> getLookCatalog();
  Future<LookCatalog> saveLookPreset(LookPreset preset);
  Future<LookCatalog> deleteLookPreset(String id);
  Future<LookCatalog> setAutoApplyPreset(String? id);
  Future<Uint8List?> loadPhotoThumbnail(String dcfName);
  Future<Uint8List?> renderPhotoPreview(String dcfName, LookPreset preset);
  Future<LookExportResult> exportGradedPhoto(String dcfName, LookPreset preset);
}

class PlatformSyncBridge implements SyncBridge {
  static const MethodChannel _method = MethodChannel('com.bionz.looklab/sync');
  static const EventChannel _event = EventChannel('com.bionz.looklab/events');

  @override
  Stream<SyncSnapshot> get events {
    return _event.receiveBroadcastStream().map((dynamic raw) {
      if (raw is Map) {
        return SyncSnapshot.fromMap(raw);
      }
      return const SyncSnapshot();
    }).handleError((Object _) {
      // Ignored in headless test environments.
    });
  }

  @override
  Future<SyncSnapshot> getSnapshot() async {
    try {
      final raw = await _method.invokeMethod<dynamic>('getSnapshot');
      if (raw is Map) return SyncSnapshot.fromMap(raw);
    } on MissingPluginException {
      // Host widget tests do not attach the Android service.
    }
    return const SyncSnapshot();
  }

  @override
  Future<SyncConfig> getConfig() async {
    try {
      final raw = await _method.invokeMethod<dynamic>('getConfig');
      if (raw is Map) return SyncConfig.fromMap(raw);
    } on MissingPluginException {
      // Fallback defaults for widget tests.
    }
    return const SyncConfig();
  }

  @override
  Future<SyncConfig> saveConfig(SyncConfig config) async {
    try {
      final raw = await _method.invokeMethod<dynamic>('saveConfig', config.toMap());
      if (raw is Map) return SyncConfig.fromMap(raw);
    } on MissingPluginException {
      // No-op in headless widget tests.
    }
    return config;
  }

  Future<void> _invokeVoid(String method) async {
    try {
      await _method.invokeMethod<void>(method);
    } on MissingPluginException {
      // No-op in headless widget tests without Android host bindings.
    }
  }

  @override
  Future<void> startService() => _invokeVoid('startService');

  @override
  Future<void> stopService() => _invokeVoid('stopService');

  @override
  Future<void> syncNow() => _invokeVoid('syncNow');

  @override
  Future<bool> isIgnoringBatteryOptimizations() async {
    try {
      return await _method.invokeMethod<bool>('isIgnoringBatteryOptimizations') ??
          true;
    } on MissingPluginException {
      return true;
    }
  }

  @override
  Future<void> requestBatteryOptimizationExemption() =>
      _invokeVoid('requestBatteryOptimizationExemption');

  @override
  Future<void> openWifiSettings() => _invokeVoid('openWifiSettings');

  @override
  Future<void> clearLogs() => _invokeVoid('clearLogs');

  @override
  Future<LookCatalog> getLookCatalog() async {
    try {
      final raw = await _method.invokeMethod<dynamic>('getLookCatalog');
      if (raw is Map) return LookCatalog.fromMap(raw);
    } on MissingPluginException {
      // Fallback catalog for headless widget tests.
    }
    return LookCatalog.fallbackDefaults();
  }

  @override
  Future<LookCatalog> saveLookPreset(LookPreset preset) async {
    try {
      final raw = await _method.invokeMethod<dynamic>(
        'saveLookPreset',
        preset.toMap(),
      );
      if (raw is Map) return LookCatalog.fromMap(raw);
    } on MissingPluginException {
      // No-op in headless widget tests.
    }
    return LookCatalog.fallbackDefaults();
  }

  @override
  Future<LookCatalog> deleteLookPreset(String id) async {
    try {
      final raw = await _method.invokeMethod<dynamic>(
        'deleteLookPreset',
        {'id': id},
      );
      if (raw is Map) return LookCatalog.fromMap(raw);
    } on MissingPluginException {
      // No-op in headless widget tests.
    }
    return LookCatalog.fallbackDefaults();
  }

  @override
  Future<LookCatalog> setAutoApplyPreset(String? id) async {
    try {
      final raw = await _method.invokeMethod<dynamic>(
        'setAutoApplyPreset',
        {'id': id},
      );
      if (raw is Map) return LookCatalog.fromMap(raw);
    } on MissingPluginException {
      // No-op in headless widget tests.
    }
    return LookCatalog.fallbackDefaults();
  }

  @override
  Future<Uint8List?> loadPhotoThumbnail(String dcfName) async {
    try {
      return await _method.invokeMethod<Uint8List>(
        'loadPhotoThumbnail',
        {'dcfName': dcfName},
      );
    } on MissingPluginException {
      return null;
    }
  }

  @override
  Future<Uint8List?> renderPhotoPreview(String dcfName, LookPreset preset) async {
    try {
      return await _method.invokeMethod<Uint8List>(
        'renderPhotoPreview',
        {
          'dcfName': dcfName,
          'preset': preset.toMap(),
        },
      );
    } on MissingPluginException {
      return null;
    }
  }

  @override
  Future<LookExportResult> exportGradedPhoto(
    String dcfName,
    LookPreset preset,
  ) async {
    try {
      final raw = await _method.invokeMethod<dynamic>(
        'exportGradedPhoto',
        {
          'dcfName': dcfName,
          'preset': preset.toMap(),
        },
      );
      if (raw is Map) return LookExportResult.fromMap(raw);
    } on MissingPluginException {
      return const LookExportResult(localPath: '', elapsedMs: 0);
    }
    return const LookExportResult(localPath: '', elapsedMs: 0);
  }
}
