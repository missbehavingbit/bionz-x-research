import 'package:flutter/material.dart';

import '../models/sync_models.dart';
import '../services/sync_bridge.dart';
import 'snapseed_editor_screen.dart';

class SyncScreen extends StatefulWidget {
  const SyncScreen({
    super.key,
    required this.bridge,
    required this.snapshot,
    required this.config,
    required this.batteryExempt,
    required this.onConfigSaved,
    required this.onBatteryRefresh,
  });

  final SyncBridge bridge;
  final SyncSnapshot snapshot;
  final SyncConfig config;
  final bool batteryExempt;
  final ValueChanged<SyncConfig> onConfigSaved;
  final VoidCallback onBatteryRefresh;

  @override
  State<SyncScreen> createState() => _SyncScreenState();
}

class _SyncScreenState extends State<SyncScreen> {
  late TextEditingController _ssidCtrl;
  late TextEditingController _pskCtrl;
  late TextEditingController _hostCtrl;
  late TextEditingController _portCtrl;
  bool _autoSuggest = true;
  bool _configExpanded = false;

  @override
  void initState() {
    super.initState();
    _ssidCtrl = TextEditingController(text: widget.config.ssid);
    _pskCtrl = TextEditingController(text: widget.config.psk);
    _hostCtrl = TextEditingController(text: widget.config.host);
    _portCtrl = TextEditingController(text: widget.config.port.toString());
    _autoSuggest = widget.config.autoSuggestWifi;
  }

  @override
  void didUpdateWidget(covariant SyncScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.config.ssid != widget.config.ssid && !_configExpanded) {
      _ssidCtrl.text = widget.config.ssid;
      _pskCtrl.text = widget.config.psk;
      _hostCtrl.text = widget.config.host;
      _portCtrl.text = widget.config.port.toString();
      _autoSuggest = widget.config.autoSuggestWifi;
    }
  }

  @override
  void dispose() {
    _ssidCtrl.dispose();
    _pskCtrl.dispose();
    _hostCtrl.dispose();
    _portCtrl.dispose();
    super.dispose();
  }

  Color _statusColor(String state) {
    if (state == 'Connected') return const Color(0xFF34D399);
    if (state.startsWith('Waiting') || state == 'Connecting') {
      return const Color(0xFFFBBF24);
    }
    return const Color(0xFF94A3B8);
  }

  Future<void> _saveApConfig() async {
    final port = int.tryParse(_portCtrl.text.trim()) ?? 8765;
    final next = SyncConfig(
      ssid: _ssidCtrl.text.trim().isEmpty
          ? 'A6400-5G-SYNC'
          : _ssidCtrl.text.trim(),
      psk: _pskCtrl.text,
      host: _hostCtrl.text.trim().isEmpty
          ? '192.168.122.1'
          : _hostCtrl.text.trim(),
      port: port.clamp(1, 65535),
      autoSuggestWifi: _autoSuggest,
    );
    final saved = await widget.bridge.saveConfig(next);
    widget.onConfigSaved(saved);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Camera AP configuration saved.')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final snap = widget.snapshot;
    final statusColor = _statusColor(snap.connectionState);
    final theme = Theme.of(context);

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (!widget.batteryExempt)
          Card(
            color: const Color(0xFF422006),
            margin: const EdgeInsets.only(bottom: 12),
            child: ListTile(
              leading: const Icon(Icons.battery_alert, color: Color(0xFFFBBF24)),
              title: const Text('Battery Optimization Active'),
              subtitle: const Text(
                'Exempt this app so locked-screen 5 GHz sync is never paused by Android.',
              ),
              trailing: TextButton(
                onPressed: () async {
                  await widget.bridge.requestBatteryOptimizationExemption();
                  widget.onBatteryRefresh();
                },
                child: const Text('Allow'),
              ),
            ),
          ),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Container(
                      width: 10,
                      height: 10,
                      decoration: BoxDecoration(
                        color: statusColor,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        snap.connectionState,
                        style: theme.textTheme.titleMedium?.copyWith(
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    Chip(
                      label: Text('${snap.syncedCount} synced'),
                      visualDensity: VisualDensity.compact,
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 16,
                  runSpacing: 8,
                  children: [
                    _MetricTile(label: 'SSID', value: snap.ssid),
                    _MetricTile(label: '5 GHz Band', value: snap.chanspec),
                    _MetricTile(label: 'Camera Power', value: snap.powerState),
                    _MetricTile(label: 'Queue Pending', value: '${snap.queueLen}'),
                    _MetricTile(
                      label: 'Throughput',
                      value: '${snap.speedMbps.toStringAsFixed(2)} MB/s',
                    ),
                    _MetricTile(
                      label: 'Camera ID',
                      value: snap.cameraId.isEmpty ? '-' : snap.cameraId,
                    ),
                  ],
                ),
                if (snap.activeFile.isNotEmpty) ...[
                  const SizedBox(height: 14),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('Transferring ${snap.activeFile}'),
                      Text('${(snap.progress * 100).round()}%'),
                    ],
                  ),
                  const SizedBox(height: 6),
                  LinearProgressIndicator(value: snap.progress),
                ],
                if (snap.lastError.isNotEmpty) ...[
                  const SizedBox(height: 10),
                  Text(
                    snap.lastError,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: const Color(0xFFFCA5A5),
                    ),
                  ),
                ],
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: snap.serviceRunning
                            ? widget.bridge.stopService
                            : widget.bridge.startService,
                        icon: Icon(
                          snap.serviceRunning ? Icons.stop : Icons.play_arrow,
                        ),
                        label: Text(
                          snap.serviceRunning
                              ? 'Stop Background Sync'
                              : 'Start Background Sync',
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    OutlinedButton.icon(
                      onPressed: widget.bridge.syncNow,
                      icon: const Icon(Icons.refresh),
                      label: const Text('Sync'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        Card(
          child: ExpansionTile(
            title: const Text('5 GHz AP Connection'),
            subtitle: Text('${widget.config.ssid} • ${widget.config.host}:${widget.config.port}'),
            initiallyExpanded: _configExpanded,
            onExpansionChanged: (v) => setState(() => _configExpanded = v),
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
                child: Column(
                  children: [
                    TextField(
                      controller: _ssidCtrl,
                      decoration: const InputDecoration(labelText: 'SSID'),
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: _pskCtrl,
                      obscureText: true,
                      decoration: const InputDecoration(
                        labelText: 'WPA2 Passphrase',
                      ),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Expanded(
                          flex: 2,
                          child: TextField(
                            controller: _hostCtrl,
                            decoration: const InputDecoration(
                              labelText: 'Camera IP',
                            ),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: TextField(
                            controller: _portCtrl,
                            keyboardType: TextInputType.number,
                            decoration: const InputDecoration(labelText: 'Port'),
                          ),
                        ),
                      ],
                    ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('Auto-Suggest Camera Wi-Fi'),
                      value: _autoSuggest,
                      onChanged: (v) => setState(() => _autoSuggest = v),
                    ),
                    Align(
                      alignment: Alignment.centerRight,
                      child: FilledButton(
                        onPressed: _saveApConfig,
                        child: const Text('Save Settings'),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Recent Verified Photos',
                  style: theme.textTheme.titleMedium,
                ),
                const SizedBox(height: 8),
                if (snap.recentFiles.isEmpty)
                  const Text(
                    'No photos synced yet. Start Background Sync and shoot in Photo mode.',
                  )
                else
                  for (final f in snap.recentFiles.take(15))
                    ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(
                        f.isRaw ? Icons.raw_on : Icons.photo_outlined,
                      ),
                      title: Text(f.dcfName),
                      subtitle: Text(
                        '${f.sizeMb.toStringAsFixed(1)} MB • CRC ${f.crc32Hex} • ${f.localPath}',
                      ),
                      trailing: const Icon(Icons.auto_fix_high, size: 18),
                      onTap: () async {
                        final catalog = await widget.bridge.getLookCatalog();
                        if (!context.mounted) return;
                        await Navigator.of(context).push<bool>(
                          MaterialPageRoute(
                            builder: (_) => SnapseedEditorScreen(
                              bridge: widget.bridge,
                              file: f,
                              initialCatalog: catalog,
                            ),
                          ),
                        );
                      },
                    ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text('Diagnostic Log', style: theme.textTheme.titleMedium),
                    TextButton(
                      onPressed: widget.bridge.clearLogs,
                      child: const Text('Clear'),
                    ),
                  ],
                ),
                if (snap.logs.isEmpty)
                  const Text('No events logged yet.')
                else
                  for (final line in snap.logs.take(15))
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Text(
                        line,
                        style: theme.textTheme.bodySmall?.copyWith(
                          fontFamily: 'monospace',
                        ),
                      ),
                    ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _MetricTile extends StatelessWidget {
  const _MetricTile({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SizedBox(
      width: 140,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: theme.textTheme.labelSmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          Text(
            value,
            style: theme.textTheme.bodyMedium?.copyWith(
              fontWeight: FontWeight.w600,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}
