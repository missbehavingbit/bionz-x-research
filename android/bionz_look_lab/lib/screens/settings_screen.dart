import 'package:flutter/material.dart';

import '../services/sync_bridge.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({
    super.key,
    required this.bridge,
    required this.batteryExempt,
    required this.onBatteryRefresh,
  });

  final SyncBridge bridge;
  final bool batteryExempt;
  final VoidCallback onBatteryRefresh;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: Column(
            children: [
              ListTile(
                leading: Icon(
                  batteryExempt ? Icons.check_circle : Icons.warning_amber,
                  color: batteryExempt
                      ? const Color(0xFF34D399)
                      : const Color(0xFFFBBF24),
                ),
                title: const Text('Unrestricted Background Battery'),
                subtitle: Text(
                  batteryExempt
                      ? 'Enabled: OS allows continuous locked-screen transfers.'
                      : 'Disabled: tap to exempt from Doze battery throttling.',
                ),
                trailing: OutlinedButton(
                  onPressed: () async {
                    await bridge.requestBatteryOptimizationExemption();
                    onBatteryRefresh();
                  },
                  child: const Text('Configure'),
                ),
              ),
              const Divider(height: 1),
              ListTile(
                leading: const Icon(Icons.wifi),
                title: const Text('Android Wi-Fi Settings'),
                subtitle: const Text(
                  'Connect once or accept A6400-5G-SYNC suggestion; keep "stay connected without internet" if prompted.',
                ),
                trailing: OutlinedButton(
                  onPressed: bridge.openWifiSettings,
                  child: const Text('Open'),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        const Card(
          child: Column(
            children: [
              ListTile(
                leading: Icon(Icons.folder_outlined),
                title: Text('Destination Storage'),
                subtitle: Text('DCIM/BionzLookLab (atomic MediaStore publish)'),
              ),
              Divider(height: 1),
              ListTile(
                leading: Icon(Icons.verified_user_outlined),
                title: Text('Camera Transfer Protocol'),
                subtitle: Text(
                  'WiFiSync v1: HTTP/1.1 + Range Resume + IEEE CRC-32 + Smart Single-Best (RAW overrides JPG in RAW+JPG)',
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
