import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../models/look_models.dart';
import '../models/sync_models.dart';
import '../services/sync_bridge.dart';

/// Snapseed-inspired full-screen gesture editor backed by SpektraFilm native rendering.
///
/// Vertical drag opens/navigates the parameter wheel (`Structure`, `Exposure`,
/// `Contrast`, `DIR Couplers`, `Halation`, `Grain`, etc.). Horizontal drag adjusts
/// the active parameter with live top progress bar feedback. Press-and-hold the
/// top-right compare button previews the ungraded original.
class SnapseedEditorScreen extends StatefulWidget {
  const SnapseedEditorScreen({
    super.key,
    required this.bridge,
    required this.file,
    required this.initialCatalog,
    this.initialPreset,
    this.onCatalogChanged,
  });

  final SyncBridge bridge;
  final SyncedFile file;
  final LookCatalog initialCatalog;
  final LookPreset? initialPreset;
  final ValueChanged<LookCatalog>? onCatalogChanged;

  @override
  State<SnapseedEditorScreen> createState() => _SnapseedEditorScreenState();
}

class _SnapseedEditorScreenState extends State<SnapseedEditorScreen> {
  late LookCatalog _catalog;
  late LookPreset _preset;
  AdjustParam _activeParam = AdjustParam.structure;

  Uint8List? _originalBytes;
  Uint8List? _previewBytes;
  bool _loading = true;
  bool _rendering = false;
  bool _exporting = false;
  bool _comparingOriginal = false;
  bool _showStockStrip = true;

  // Gesture state matching Snapseed's orthogonal lock (horizontal = adjust, vertical = switch tool)
  bool? _verticalAxis;
  double _dragAccumX = 0;
  double _dragAccumY = 0;
  int _dragStartValue = 0;
  int _dragStartParamIdx = 0;
  bool _showParamOverlay = false;

  Timer? _debounce;
  int _renderTicket = 0;

  @override
  void initState() {
    super.initState();
    _catalog = widget.initialCatalog;
    final auto = _catalog.findPreset(_catalog.autoApplyPresetId);
    _preset = (widget.initialPreset ??
            auto ??
            (_catalog.presets.isNotEmpty
                ? _catalog.presets.first
                : const LookPreset(
                    id: 'builtin_portra_400',
                    name: 'Kodak Portra 400',
                  )))
        .copyWith();
    _loadInitialFrames();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  Future<void> _loadInitialFrames() async {
    const neutral = LookPreset(
      id: '__neutral__',
      name: 'Original',
      stockId: 'neutral',
      paperId: 'neutral_scan',
      dirCouplers: 0,
      halation: 0,
      grain: 0,
    );
    final results = await Future.wait([
      widget.bridge.renderPhotoPreview(widget.file.dcfName, neutral),
      widget.bridge.renderPhotoPreview(widget.file.dcfName, _preset),
    ]);
    if (!mounted) return;
    setState(() {
      _originalBytes = results[0];
      _previewBytes = results[1] ?? results[0];
      _loading = false;
    });
  }

  void _schedulePreviewRender() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 45), _renderNow);
  }

  Future<void> _renderNow() async {
    final ticket = ++_renderTicket;
    if (mounted) {
      setState(() => _rendering = true);
    }
    final bytes = await widget.bridge.renderPhotoPreview(
      widget.file.dcfName,
      _preset,
    );
    if (!mounted || ticket != _renderTicket) return;
    setState(() {
      if (bytes != null) _previewBytes = bytes;
      _rendering = false;
    });
  }

  void _onPanStart(DragStartDetails details) {
    _verticalAxis = null;
    _dragAccumX = 0;
    _dragAccumY = 0;
    _dragStartValue = _preset.valueOf(_activeParam);
    _dragStartParamIdx = AdjustParam.values.indexOf(_activeParam);
  }

  void _onPanUpdate(DragUpdateDetails details, double screenWidth) {
    _dragAccumX += details.delta.dx;
    _dragAccumY += details.delta.dy;

    if (_verticalAxis == null) {
      if (_dragAccumX.abs() < 8 && _dragAccumY.abs() < 8) return;
      _verticalAxis = _dragAccumY.abs() > _dragAccumX.abs();
      if (_verticalAxis == true && mounted) {
        setState(() => _showParamOverlay = true);
      }
    }

    if (_verticalAxis == true) {
      const itemStepPx = 42.0;
      final steps = (_dragAccumY / itemStepPx).round();
      final nextIdx = (_dragStartParamIdx + steps)
          .clamp(0, AdjustParam.values.length - 1);
      final nextParam = AdjustParam.values[nextIdx];
      if (nextParam != _activeParam) {
        setState(() {
          _activeParam = nextParam;
        });
      }
      return;
    }

    final span = _activeParam.max - _activeParam.min;
    final sensitivity = span / (screenWidth * 0.68).clamp(220.0, 520.0);
    final nextVal = (_dragStartValue + (_dragAccumX * sensitivity).round())
        .clamp(_activeParam.min, _activeParam.max);
    if (nextVal != _preset.valueOf(_activeParam)) {
      setState(() {
        _preset = _preset.withParam(_activeParam, nextVal);
      });
      _schedulePreviewRender();
    }
  }

  void _onPanEnd(DragEndDetails details) {
    if (_showParamOverlay) {
      setState(() => _showParamOverlay = false);
    }
    _verticalAxis = null;
  }

  void _applyStock(FilmStockInfo stock) {
    setState(() {
      _preset = _preset.copyWith(
        stockId: stock.id,
        dirCouplers: stock.defaultDirCouplers,
        halation: stock.defaultHalation,
        grain: stock.defaultGrain,
      );
    });
    _renderNow();
  }

  void _applyPaper(PrintPaperInfo paper) {
    setState(() {
      _preset = _preset.copyWith(paperId: paper.id);
    });
    _renderNow();
  }

  void _applyPreset(LookPreset p) {
    setState(() {
      _preset = p.copyWith();
    });
    _renderNow();
  }

  Future<void> _exportPhoto() async {
    if (_exporting) return;
    setState(() => _exporting = true);
    try {
      final res = await widget.bridge.exportGradedPhoto(
        widget.file.dcfName,
        _preset,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            res.localPath.isNotEmpty
                ? 'Saved ${res.localPath} (${res.elapsedMs} ms)'
                : 'Graded photo exported.',
          ),
        ),
      );
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _exporting = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Export failed: $e')),
      );
    }
  }

  Future<void> _promptSavePreset() async {
    final nameCtrl = TextEditingController(
      text: _preset.builtIn ? '${_preset.name} Custom' : _preset.name,
    );
    bool setAuto = true;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: const Text('Save SpektraFilm Preset'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: nameCtrl,
                autofocus: true,
                decoration: const InputDecoration(
                  labelText: 'Preset Name',
                  hintText: 'e.g. Warm Street 400',
                ),
              ),
              const SizedBox(height: 12),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Auto-apply to all incoming camera photos'),
                value: setAuto,
                onChanged: (v) => setDialogState(() => setAuto = v ?? true),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: const Text('Save'),
            ),
          ],
        ),
      ),
    );
    if (confirmed != true || !mounted) return;

    final customName =
        nameCtrl.text.trim().isEmpty ? 'Custom Look' : nameCtrl.text.trim();
    final newId = _preset.builtIn
        ? 'custom_${DateTime.now().millisecondsSinceEpoch}'
        : _preset.id;
    final toSave = _preset.copyWith(
      id: newId,
      name: customName,
      builtIn: false,
    );

    var nextCatalog = await widget.bridge.saveLookPreset(toSave);
    if (setAuto) {
      nextCatalog = await widget.bridge.setAutoApplyPreset(toSave.id);
    }
    if (!mounted) return;
    setState(() {
      _catalog = nextCatalog;
      _preset = toSave;
    });
    widget.onCatalogChanged?.call(nextCatalog);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          setAuto
              ? 'Saved "$customName" and enabled Auto-Apply for new photos.'
              : 'Saved preset "$customName".',
        ),
      ),
    );
  }

  Future<void> _openTuneSheet() async {
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: const Color(0xFF171C24),
      isScrollControlled: true,
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      'SpektraFilm Tools & Presets',
                      style: Theme.of(ctx).textTheme.titleMedium,
                    ),
                    TextButton.icon(
                      onPressed: () {
                        Navigator.of(ctx).pop();
                        _promptSavePreset();
                      },
                      icon: const Icon(Icons.bookmark_add_outlined),
                      label: const Text('Save Preset'),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  'Parameters (or drag vertically on photo)',
                  style: Theme.of(ctx).textTheme.labelMedium,
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final p in AdjustParam.values)
                      ChoiceChip(
                        label: Text(
                          '${p.label} ${p.formatValue(_preset.valueOf(p))}',
                        ),
                        selected: p == _activeParam,
                        onSelected: (_) {
                          setState(() => _activeParam = p);
                          Navigator.of(ctx).pop();
                        },
                      ),
                  ],
                ),
                const SizedBox(height: 16),
                Text(
                  'Print Paper (RA-4 / Silver)',
                  style: Theme.of(ctx).textTheme.labelMedium,
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final paper in _catalog.papers)
                      ChoiceChip(
                        label: Text(paper.name),
                        selected: paper.id == _preset.paperId,
                        onSelected: (_) {
                          _applyPaper(paper);
                          Navigator.of(ctx).pop();
                        },
                      ),
                  ],
                ),
                const SizedBox(height: 16),
                Text(
                  'Saved Presets',
                  style: Theme.of(ctx).textTheme.labelMedium,
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final p in _catalog.presets)
                      ActionChip(
                        avatar: p.id == _catalog.autoApplyPresetId
                            ? const Icon(Icons.auto_awesome, size: 16)
                            : null,
                        label: Text(p.name),
                        onPressed: () {
                          _applyPreset(p);
                          Navigator.of(ctx).pop();
                        },
                      ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final screenWidth = MediaQuery.sizeOf(context).width;
    final val = _preset.valueOf(_activeParam);
    final shownBytes = _comparingOriginal ? _originalBytes : _previewBytes;

    return Scaffold(
      backgroundColor: const Color(0xFFEEEEEE),
      body: SafeArea(
        child: Column(
          children: [
            // Snapseed-style top adjustment bar
            _SnapseedTopProgressBar(param: _activeParam, value: val),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
              child: Row(
                children: [
                  GestureDetector(
                    onTap: () =>
                        setState(() => _showStockStrip = !_showStockStrip),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 4,
                      ),
                      decoration: BoxDecoration(
                        color: const Color(0xFFE0E2E6),
                        borderRadius: BorderRadius.circular(14),
                      ),
                      child: Text(
                        _stockDisplayName(_preset.stockId),
                        style: const TextStyle(
                          color: Color(0xFF374151),
                          fontSize: 12,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ),
                  ),
                  Expanded(
                    child: Center(
                      child: Text(
                        '${_activeParam.label} ${_activeParam.formatValue(val)}',
                        style: const TextStyle(
                          color: Color(0xFF2B2D30),
                          fontSize: 15,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ),
                  ),
                  Listener(
                    onPointerDown: (_) =>
                        setState(() => _comparingOriginal = true),
                    onPointerUp: (_) =>
                        setState(() => _comparingOriginal = false),
                    onPointerCancel: (_) =>
                        setState(() => _comparingOriginal = false),
                    child: Tooltip(
                      message: 'Hold for Before / After',
                      child: Container(
                        padding: const EdgeInsets.all(6),
                        decoration: BoxDecoration(
                          color: _comparingOriginal
                              ? const Color(0xFF1A73E8).withValues(alpha: 0.16)
                              : Colors.transparent,
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: const Icon(
                          Icons.compare,
                          color: Color(0xFF5F6368),
                          size: 24,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),

            // Interactive Snapseed-style canvas
            Expanded(
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onPanStart: _onPanStart,
                onPanUpdate: (d) => _onPanUpdate(d, screenWidth),
                onPanEnd: _onPanEnd,
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    Padding(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 6,
                      ),
                      child: Center(
                        child: _loading
                            ? const CircularProgressIndicator()
                            : (shownBytes != null
                                ? Image.memory(
                                    shownBytes,
                                    fit: BoxFit.contain,
                                    gaplessPlayback: true,
                                  )
                                : const Icon(
                                    Icons.image_not_supported_outlined,
                                    color: Color(0xFF6B7280),
                                    size: 64,
                                  )),
                      ),
                    ),
                    if (_comparingOriginal)
                      Positioned(
                        top: 16,
                        left: 0,
                        right: 0,
                        child: Center(
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 12,
                              vertical: 4,
                            ),
                            decoration: BoxDecoration(
                              color: Colors.black.withValues(alpha: 0.65),
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: const Text(
                              'ORIGINAL',
                              style: TextStyle(
                                color: Colors.white,
                                fontSize: 11,
                                fontWeight: FontWeight.w600,
                                letterSpacing: 1.0,
                              ),
                            ),
                          ),
                        ),
                      ),
                    if (_rendering || _exporting)
                      const Positioned(
                        top: 12,
                        right: 22,
                        child: SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                      ),
                    if (_showParamOverlay)
                      Center(
                        child: Container(
                          width: 240,
                          padding: const EdgeInsets.symmetric(vertical: 8),
                          decoration: BoxDecoration(
                            color: Colors.black.withValues(alpha: 0.78),
                            borderRadius: BorderRadius.circular(12),
                          ),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              for (final p in AdjustParam.values)
                                Container(
                                  width: double.infinity,
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 16,
                                    vertical: 6,
                                  ),
                                  color: p == _activeParam
                                      ? const Color(0xFF1A73E8)
                                          .withValues(alpha: 0.85)
                                      : Colors.transparent,
                                  child: Row(
                                    mainAxisAlignment:
                                        MainAxisAlignment.spaceBetween,
                                    children: [
                                      Text(
                                        p.label,
                                        style: const TextStyle(
                                          color: Colors.white,
                                          fontSize: 13,
                                          fontWeight: FontWeight.w500,
                                        ),
                                      ),
                                      Text(
                                        p.formatValue(_preset.valueOf(p)),
                                        style: const TextStyle(
                                          color: Color(0xFFE5E7EB),
                                          fontSize: 13,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),

            // Toggleable compact SpektraFilm stock ribbon
            if (_showStockStrip)
              SizedBox(
                height: 44,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  itemCount: _catalog.stocks.length,
                  separatorBuilder: (_, _) => const SizedBox(width: 8),
                  itemBuilder: (ctx, idx) {
                    final stock = _catalog.stocks[idx];
                    final selected = stock.id == _preset.stockId;
                    return ActionChip(
                      backgroundColor: selected
                          ? const Color(0xFF1A73E8)
                          : const Color(0xFFE2E4E9),
                      side: BorderSide.none,
                      label: Text(
                        stock.name.replaceFirst('Kodak ', ''),
                        style: TextStyle(
                          fontSize: 12,
                          color: selected
                              ? Colors.white
                              : const Color(0xFF202124),
                          fontWeight:
                              selected ? FontWeight.w600 : FontWeight.w400,
                        ),
                      ),
                      onPressed: () => _applyStock(stock),
                    );
                  },
                ),
              ),

            // Snapseed bottom action bar: X | Tune/Presets | ✓
            Container(
              height: 58,
              decoration: const BoxDecoration(
                color: Color(0xFFF6F6F6),
                border: Border(top: BorderSide(color: Color(0xFFDADCE0))),
              ),
              padding: const EdgeInsets.symmetric(horizontal: 20),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  IconButton(
                    tooltip: 'Cancel',
                    onPressed: _exporting ? null : () => Navigator.pop(context),
                    icon: const Icon(Icons.close, color: Color(0xFF3C4043)),
                  ),
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      IconButton(
                        tooltip: 'Parameters, Film & Presets',
                        onPressed: _openTuneSheet,
                        icon: const Icon(Icons.tune, color: Color(0xFF3C4043)),
                      ),
                      IconButton(
                        tooltip: 'Save Preset & Auto-Apply',
                        onPressed: _promptSavePreset,
                        icon: const Icon(
                          Icons.auto_awesome,
                          color: Color(0xFF1A73E8),
                          size: 22,
                        ),
                      ),
                    ],
                  ),
                  IconButton(
                    tooltip: 'Save Graded Photo',
                    onPressed: _exporting ? null : _exportPhoto,
                    icon: const Icon(Icons.check, color: Color(0xFF3C4043)),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _stockDisplayName(String stockId) {
    for (final s in _catalog.stocks) {
      if (s.id == stockId) return s.name;
    }
    return stockId;
  }
}

class _SnapseedTopProgressBar extends StatelessWidget {
  const _SnapseedTopProgressBar({required this.param, required this.value});

  final AdjustParam param;
  final int value;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 5,
      width: double.infinity,
      child: CustomPaint(
        painter: _SnapseedBarPainter(
          min: param.min,
          max: param.max,
          value: value,
          isBipolar: param.isBipolar,
        ),
      ),
    );
  }
}

class _SnapseedBarPainter extends CustomPainter {
  const _SnapseedBarPainter({
    required this.min,
    required this.max,
    required this.value,
    required this.isBipolar,
  });

  final int min;
  final int max;
  final int value;
  final bool isBipolar;

  @override
  void paint(Canvas canvas, Size size) {
    final bgPaint = Paint()..color = const Color(0xFFD5D8DC);
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, size.height), bgPaint);

    final bluePaint = Paint()..color = const Color(0xFF1A73E8);
    if (isBipolar) {
      final mid = size.width * 0.5;
      final frac = value / 100.0;
      if (frac >= 0) {
        canvas.drawRect(
          Rect.fromLTWH(mid, 0, mid * frac, size.height),
          bluePaint,
        );
      } else {
        final w = mid * (-frac);
        canvas.drawRect(
          Rect.fromLTWH(mid - w, 0, w, size.height),
          bluePaint,
        );
      }
      final tickPaint = Paint()..color = const Color(0xFF5F6368);
      canvas.drawRect(Rect.fromLTWH(mid - 0.75, 0, 1.5, size.height), tickPaint);
    } else {
      final frac = (value - min) / (max - min).toDouble();
      canvas.drawRect(
        Rect.fromLTWH(0, 0, size.width * frac.clamp(0.0, 1.0), size.height),
        bluePaint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _SnapseedBarPainter old) {
    return old.value != value || old.isBipolar != isBipolar;
  }
}
