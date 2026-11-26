import 'dart:async';

import 'package:flutter/material.dart';

import 'models/sync_models.dart';
import 'screens/look_lab_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/sync_screen.dart';
import 'services/sync_bridge.dart';

void main() {
  runApp(BionzLookLabApp(bridge: PlatformSyncBridge()));
}

class BionzLookLabApp extends StatelessWidget {
  const BionzLookLabApp({super.key, required this.bridge});

  final SyncBridge bridge;

  @override
  Widget build(BuildContext context) {
    final scheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFFFF6D00),
      brightness: Brightness.dark,
    );
    return MaterialApp(
      title: 'BIONZ Look Lab',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: scheme,
        scaffoldBackgroundColor: const Color(0xFF0E1116),
        cardTheme: const CardThemeData(
          color: Color(0xFF171C24),
          elevation: 0,
        ),
      ),
      home: _HomeShell(bridge: bridge),
    );
  }
}

class _HomeShell extends StatefulWidget {
  const _HomeShell({required this.bridge});

  final SyncBridge bridge;

  @override
  State<_HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<_HomeShell> {
  int _tab = 0;
  SyncSnapshot _snapshot = const SyncSnapshot();
  SyncConfig _config = const SyncConfig();
  bool _batteryExempt = true;
  StreamSubscription<SyncSnapshot>? _sub;

  @override
  void initState() {
    super.initState();
    _loadInitial();
    _sub = widget.bridge.events.listen((snap) {
      if (!mounted) return;
      setState(() => _snapshot = snap);
    });
  }

  Future<void> _loadInitial() async {
    final snap = await widget.bridge.getSnapshot();
    final cfg = await widget.bridge.getConfig();
    final exempt = await widget.bridge.isIgnoringBatteryOptimizations();
    if (!mounted) return;
    setState(() {
      _snapshot = snap;
      _config = cfg;
      _batteryExempt = exempt;
    });
  }

  Future<void> _refreshBattery() async {
    final exempt = await widget.bridge.isIgnoringBatteryOptimizations();
    if (!mounted) return;
    setState(() => _batteryExempt = exempt);
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final pages = [
      SyncScreen(
        bridge: widget.bridge,
        snapshot: _snapshot,
        config: _config,
        batteryExempt: _batteryExempt,
        onConfigSaved: (cfg) => setState(() => _config = cfg),
        onBatteryRefresh: _refreshBattery,
      ),
      LookLabScreen(
        bridge: widget.bridge,
        hookName: _snapshot.hookName,
        recentFiles: _snapshot.recentFiles,
      ),
      SettingsScreen(
        bridge: widget.bridge,
        batteryExempt: _batteryExempt,
        onBatteryRefresh: _refreshBattery,
      ),
    ];

    return Scaffold(
      appBar: AppBar(
        title: const Text('BIONZ Look Lab'),
        backgroundColor: const Color(0xFF141820),
      ),
      body: pages[_tab],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (idx) => setState(() => _tab = idx),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.wifi_tethering),
            label: 'Sync',
          ),
          NavigationDestination(
            icon: Icon(Icons.auto_fix_high),
            label: 'Look Lab',
          ),
          NavigationDestination(
            icon: Icon(Icons.settings),
            label: 'Settings',
          ),
        ],
      ),
    );
  }
}
