class SyncConfig {
  const SyncConfig({
    this.ssid = 'A6400-5G-SYNC',
    this.psk = 'a6400-wifisync',
    this.host = '192.168.122.1',
    this.port = 8765,
    this.autoSuggestWifi = true,
  });

  final String ssid;
  final String psk;
  final String host;
  final int port;
  final bool autoSuggestWifi;

  factory SyncConfig.fromMap(Map<dynamic, dynamic> map) {
    return SyncConfig(
      ssid: (map['ssid'] as String?) ?? 'A6400-5G-SYNC',
      psk: (map['psk'] as String?) ?? 'a6400-wifisync',
      host: (map['host'] as String?) ?? '192.168.122.1',
      port: (map['port'] as num?)?.toInt() ?? 8765,
      autoSuggestWifi: (map['autoSuggestWifi'] as bool?) ?? true,
    );
  }

  Map<String, dynamic> toMap() => {
        'ssid': ssid,
        'psk': psk,
        'host': host,
        'port': port,
        'autoSuggestWifi': autoSuggestWifi,
      };
}

class SyncedFile {
  const SyncedFile({
    required this.cameraId,
    required this.dcfName,
    required this.seqId,
    required this.sizeBytes,
    required this.crc32Hex,
    required this.localPath,
    required this.syncedAtMs,
  });

  final String cameraId;
  final String dcfName;
  final int seqId;
  final int sizeBytes;
  final String crc32Hex;
  final String localPath;
  final int syncedAtMs;

  double get sizeMb => sizeBytes / (1024.0 * 1024.0);

  bool get isRaw => dcfName.toUpperCase().endsWith('.ARW');

  factory SyncedFile.fromMap(Map<dynamic, dynamic> map) {
    return SyncedFile(
      cameraId: (map['cameraId'] as String?) ?? '',
      dcfName: (map['dcfName'] as String?) ?? '',
      seqId: (map['seqId'] as num?)?.toInt() ?? 0,
      sizeBytes: (map['sizeBytes'] as num?)?.toInt() ?? 0,
      crc32Hex: (map['crc32'] as String?) ?? '',
      localPath: (map['localPath'] as String?) ?? '',
      syncedAtMs: (map['syncedAtMs'] as num?)?.toInt() ?? 0,
    );
  }
}

class SyncSnapshot {
  const SyncSnapshot({
    this.serviceRunning = false,
    this.connectionState = 'Stopped',
    this.ssid = 'A6400-5G-SYNC',
    this.host = '192.168.122.1',
    this.port = 8765,
    this.cameraId = '',
    this.powerState = 'OFF',
    this.chanspec = '-',
    this.queueLen = 0,
    this.activeFile = '',
    this.progress = 0.0,
    this.speedMbps = 0.0,
    this.syncedCount = 0,
    this.lastError = '',
    this.hookName = 'NoOpPostTransferHook (Transfer-only v1)',
    this.recentFiles = const [],
    this.logs = const [],
  });

  final bool serviceRunning;
  final String connectionState;
  final String ssid;
  final String host;
  final int port;
  final String cameraId;
  final String powerState;
  final String chanspec;
  final int queueLen;
  final String activeFile;
  final double progress;
  final double speedMbps;
  final int syncedCount;
  final String lastError;
  final String hookName;
  final List<SyncedFile> recentFiles;
  final List<String> logs;

  factory SyncSnapshot.fromMap(Map<dynamic, dynamic> map) {
    final rawFiles = map['recentFiles'] as List<dynamic>? ?? const [];
    final rawLogs = map['logs'] as List<dynamic>? ?? const [];
    return SyncSnapshot(
      serviceRunning: (map['serviceRunning'] as bool?) ?? false,
      connectionState: (map['connectionState'] as String?) ?? 'Stopped',
      ssid: (map['ssid'] as String?) ?? 'A6400-5G-SYNC',
      host: (map['host'] as String?) ?? '192.168.122.1',
      port: (map['port'] as num?)?.toInt() ?? 8765,
      cameraId: (map['cameraId'] as String?) ?? '',
      powerState: (map['powerState'] as String?) ?? 'OFF',
      chanspec: (map['chanspec'] as String?) ?? '-',
      queueLen: (map['queueLen'] as num?)?.toInt() ?? 0,
      activeFile: (map['activeFile'] as String?) ?? '',
      progress: (map['progress'] as num?)?.toDouble() ?? 0.0,
      speedMbps: (map['speedMbps'] as num?)?.toDouble() ?? 0.0,
      syncedCount: (map['syncedCount'] as num?)?.toInt() ?? 0,
      lastError: (map['lastError'] as String?) ?? '',
      hookName:
          (map['hookName'] as String?) ?? 'NoOpPostTransferHook (Transfer-only v1)',
      recentFiles: rawFiles
          .whereType<Map<dynamic, dynamic>>()
          .map(SyncedFile.fromMap)
          .toList(growable: false),
      logs: rawLogs.map((e) => e.toString()).toList(growable: false),
    );
  }
}
