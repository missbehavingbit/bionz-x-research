import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../models/look_models.dart';
import '../models/sync_models.dart';
import '../services/sync_bridge.dart';
import 'snapseed_editor_screen.dart';

class LookLabScreen extends StatefulWidget {
  const LookLabScreen({
    super.key,
    required this.bridge,
    required this.hookName,
    required this.recentFiles,
  });

  final SyncBridge bridge;
  final String hookName;
  final List<SyncedFile> recentFiles;

  @override
  State<LookLabScreen> createState() => _LookLabScreenState();
}

class _LookLabScreenState extends State<LookLabScreen> {
  LookCatalog _catalog = LookCatalog.fallbackDefaults();
  bool _loadingCatalog = true;
  final Map<String, Future<Uint8List?>> _thumbFutures = {};

  @override
  void initState() {
    super.initState();
    _loadCatalog();
  }

  Future<void> _loadCatalog() async {
    final catalog = await widget.bridge.getLookCatalog();
    if (!mounted) return;
    setState(() {
      _catalog = catalog;
      _loadingCatalog = false;
    });
  }

  Future<Uint8List?> _thumbFor(String dcfName) {
    return _thumbFutures.putIfAbsent(
      dcfName,
      () => widget.bridge.loadPhotoThumbnail(dcfName),
    );
  }

  Future<void> _selectAutoPreset(String? id) async {
    final next = await widget.bridge.setAutoApplyPreset(id);
    if (!mounted) return;
    setState(() => _catalog = next);
  }

  Future<void> _deleteCustomPreset(LookPreset preset) async {
    final next = await widget.bridge.deleteLookPreset(preset.id);
    if (!mounted) return;
    setState(() => _catalog = next);
  }

  Future<void> _openEditor(SyncedFile file) async {
    await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => SnapseedEditorScreen(
          bridge: widget.bridge,
          file: file,
          initialCatalog: _catalog,
          onCatalogChanged: (next) {
            if (mounted) setState(() => _catalog = next);
          },
        ),
      ),
    );
    if (mounted) _loadCatalog();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final autoPreset = _catalog.findPreset(_catalog.autoApplyPresetId);

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          color: autoPreset != null
              ? const Color(0xFF172536)
              : theme.colorScheme.surfaceContainerHighest,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      Icons.auto_awesome,
                      color: autoPreset != null
                          ? const Color(0xFF60A5FA)
                          : theme.colorScheme.onSurfaceVariant,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'SpektraFilm Auto-Apply Preset',
                        style: theme.textTheme.titleMedium?.copyWith(
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    if (_loadingCatalog)
                      const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  autoPreset != null
                      ? 'Active: ${autoPreset.name} (${autoPreset.stockId} • DIR ${autoPreset.dirCouplers}% • Halation ${autoPreset.halation}% • Grain ${autoPreset.grain}%)\nEvery incoming photo saves raw + auto-graded JPEG in DCIM/BionzLookLab/Graded.'
                      : 'Active: OFF (Raw Transfer Only)\nPick a preset below or save one in the editor to auto-grade incoming photos.',
                  style: theme.textTheme.bodySmall,
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    ChoiceChip(
                      label: const Text('OFF (Raw only)'),
                      selected: _catalog.autoApplyPresetId == null,
                      onSelected: (_) => _selectAutoPreset(null),
                    ),
                    for (final preset in _catalog.presets)
                      InputChip(
                        label: Text(preset.name),
                        selected: preset.id == _catalog.autoApplyPresetId,
                        onSelected: (_) => _selectAutoPreset(preset.id),
                        onDeleted: preset.builtIn
                            ? null
                            : () => _deleteCustomPreset(preset),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        Card(
          child: ListTile(
            leading: const Icon(Icons.camera_roll_outlined),
            title: Text(
              'SpektraFilm Engine • ${_catalog.stocks.length} Stocks • ${_catalog.papers.length} Print Papers',
            ),
            subtitle: const Text(
              'Linear exposure • Red halation • Spectral matrix & H&D curves • DIR couplers • Snapseed Structure • Density grain.',
            ),
          ),
        ),
        const SizedBox(height: 12),
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Text(
            'Synced Photos (${widget.recentFiles.length})',
            style: theme.textTheme.titleMedium,
          ),
        ),
        const SizedBox(height: 8),
        if (widget.recentFiles.isEmpty)
          const Card(
            child: Padding(
              padding: EdgeInsets.all(16),
              child: Text(
                'No synced photos yet. Sync photos over 5 GHz Wi-Fi and tap any shot to open the Snapseed-style SpektraFilm editor.',
              ),
            ),
          )
        else
          GridView.builder(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 2,
              mainAxisSpacing: 10,
              crossAxisSpacing: 10,
              childAspectRatio: 0.92,
            ),
            itemCount: widget.recentFiles.length,
            itemBuilder: (ctx, idx) {
              final f = widget.recentFiles[idx];
              return Card(
                clipBehavior: Clip.antiAlias,
                child: InkWell(
                  onTap: () => _openEditor(f),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Expanded(
                        child: FutureBuilder<Uint8List?>(
                          future: _thumbFor(f.dcfName),
                          builder: (ctx, snap) {
                            final bytes = snap.data;
                            if (bytes != null && bytes.isNotEmpty) {
                              return Image.memory(
                                bytes,
                                fit: BoxFit.cover,
                                gaplessPlayback: true,
                              );
                            }
                            return Container(
                              color: const Color(0xFF1F2631),
                              child: Icon(
                                f.isRaw ? Icons.raw_on : Icons.photo_outlined,
                                size: 36,
                                color: Colors.white54,
                              ),
                            );
                          },
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              f.dcfName,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: theme.textTheme.bodyMedium?.copyWith(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            Text(
                              '${f.sizeMb.toStringAsFixed(1)} MB • Tap to Edit',
                              style: theme.textTheme.labelSmall,
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              );
            },
          ),
      ],
    );
  }
}
