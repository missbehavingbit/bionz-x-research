enum AdjustParam {
  structure('Structure', -100, 100),
  exposure('Exposure', -100, 100),
  contrast('Contrast', -100, 100),
  highlights('Highlights', -100, 100),
  shadows('Shadows', -100, 100),
  warmth('Warmth', -100, 100),
  tint('Tint', -100, 100),
  saturation('Saturation', -100, 100),
  dirCouplers('DIR Couplers', 0, 100),
  halation('Halation', 0, 100),
  grain('Grain', 0, 100),
  fade('Fade', 0, 100),
  vignette('Vignette', 0, 100);

  const AdjustParam(this.label, this.min, this.max);

  final String label;
  final int min;
  final int max;

  bool get isBipolar => min < 0;

  String formatValue(int value) {
    if (isBipolar && value > 0) return '+$value';
    return '$value';
  }
}

class FilmStockInfo {
  const FilmStockInfo({
    required this.id,
    required this.name,
    required this.subtitle,
    this.defaultHalation = 20,
    this.defaultGrain = 25,
    this.defaultDirCouplers = 50,
  });

  final String id;
  final String name;
  final String subtitle;
  final int defaultHalation;
  final int defaultGrain;
  final int defaultDirCouplers;

  factory FilmStockInfo.fromMap(Map<dynamic, dynamic> map) {
    return FilmStockInfo(
      id: (map['id'] as String?) ?? 'portra_400',
      name: (map['name'] as String?) ?? 'Film Stock',
      subtitle: (map['subtitle'] as String?) ?? '',
      defaultHalation: (map['defaultHalation'] as num?)?.toInt() ?? 20,
      defaultGrain: (map['defaultGrain'] as num?)?.toInt() ?? 25,
      defaultDirCouplers: (map['defaultDirCouplers'] as num?)?.toInt() ?? 50,
    );
  }
}

class PrintPaperInfo {
  const PrintPaperInfo({
    required this.id,
    required this.name,
    required this.subtitle,
  });

  final String id;
  final String name;
  final String subtitle;

  factory PrintPaperInfo.fromMap(Map<dynamic, dynamic> map) {
    return PrintPaperInfo(
      id: (map['id'] as String?) ?? 'endura',
      name: (map['name'] as String?) ?? 'Print Paper',
      subtitle: (map['subtitle'] as String?) ?? '',
    );
  }
}

class LookPreset {
  const LookPreset({
    required this.id,
    required this.name,
    this.stockId = 'portra_400',
    this.paperId = 'endura',
    this.builtIn = false,
    this.exposure = 0,
    this.contrast = 0,
    this.structure = 0,
    this.highlights = 0,
    this.shadows = 0,
    this.warmth = 0,
    this.tint = 0,
    this.saturation = 0,
    this.dirCouplers = 50,
    this.halation = 20,
    this.grain = 25,
    this.fade = 0,
    this.vignette = 0,
  });

  final String id;
  final String name;
  final String stockId;
  final String paperId;
  final bool builtIn;
  final int exposure;
  final int contrast;
  final int structure;
  final int highlights;
  final int shadows;
  final int warmth;
  final int tint;
  final int saturation;
  final int dirCouplers;
  final int halation;
  final int grain;
  final int fade;
  final int vignette;

  int valueOf(AdjustParam param) {
    switch (param) {
      case AdjustParam.exposure:
        return exposure;
      case AdjustParam.contrast:
        return contrast;
      case AdjustParam.structure:
        return structure;
      case AdjustParam.highlights:
        return highlights;
      case AdjustParam.shadows:
        return shadows;
      case AdjustParam.warmth:
        return warmth;
      case AdjustParam.tint:
        return tint;
      case AdjustParam.saturation:
        return saturation;
      case AdjustParam.dirCouplers:
        return dirCouplers;
      case AdjustParam.halation:
        return halation;
      case AdjustParam.grain:
        return grain;
      case AdjustParam.fade:
        return fade;
      case AdjustParam.vignette:
        return vignette;
    }
  }

  LookPreset withParam(AdjustParam param, int rawValue) {
    final v = rawValue.clamp(param.min, param.max);
    switch (param) {
      case AdjustParam.exposure:
        return copyWith(exposure: v);
      case AdjustParam.contrast:
        return copyWith(contrast: v);
      case AdjustParam.structure:
        return copyWith(structure: v);
      case AdjustParam.highlights:
        return copyWith(highlights: v);
      case AdjustParam.shadows:
        return copyWith(shadows: v);
      case AdjustParam.warmth:
        return copyWith(warmth: v);
      case AdjustParam.tint:
        return copyWith(tint: v);
      case AdjustParam.saturation:
        return copyWith(saturation: v);
      case AdjustParam.dirCouplers:
        return copyWith(dirCouplers: v);
      case AdjustParam.halation:
        return copyWith(halation: v);
      case AdjustParam.grain:
        return copyWith(grain: v);
      case AdjustParam.fade:
        return copyWith(fade: v);
      case AdjustParam.vignette:
        return copyWith(vignette: v);
    }
  }

  LookPreset copyWith({
    String? id,
    String? name,
    String? stockId,
    String? paperId,
    bool? builtIn,
    int? exposure,
    int? contrast,
    int? structure,
    int? highlights,
    int? shadows,
    int? warmth,
    int? tint,
    int? saturation,
    int? dirCouplers,
    int? halation,
    int? grain,
    int? fade,
    int? vignette,
  }) {
    return LookPreset(
      id: id ?? this.id,
      name: name ?? this.name,
      stockId: stockId ?? this.stockId,
      paperId: paperId ?? this.paperId,
      builtIn: builtIn ?? this.builtIn,
      exposure: exposure ?? this.exposure,
      contrast: contrast ?? this.contrast,
      structure: structure ?? this.structure,
      highlights: highlights ?? this.highlights,
      shadows: shadows ?? this.shadows,
      warmth: warmth ?? this.warmth,
      tint: tint ?? this.tint,
      saturation: saturation ?? this.saturation,
      dirCouplers: dirCouplers ?? this.dirCouplers,
      halation: halation ?? this.halation,
      grain: grain ?? this.grain,
      fade: fade ?? this.fade,
      vignette: vignette ?? this.vignette,
    );
  }

  Map<String, dynamic> toMap() => {
        'id': id,
        'name': name,
        'stockId': stockId,
        'paperId': paperId,
        'builtIn': builtIn,
        'exposure': exposure,
        'contrast': contrast,
        'structure': structure,
        'highlights': highlights,
        'shadows': shadows,
        'warmth': warmth,
        'tint': tint,
        'saturation': saturation,
        'dirCouplers': dirCouplers,
        'halation': halation,
        'grain': grain,
        'fade': fade,
        'vignette': vignette,
      };

  factory LookPreset.fromMap(Map<dynamic, dynamic> map) {
    int readInt(String k, int def) => (map[k] as num?)?.toInt() ?? def;
    return LookPreset(
      id: (map['id'] as String?) ?? 'custom',
      name: (map['name'] as String?) ?? 'Custom Film',
      stockId: (map['stockId'] as String?) ?? 'portra_400',
      paperId: (map['paperId'] as String?) ?? 'endura',
      builtIn: (map['builtIn'] as bool?) ?? false,
      exposure: readInt('exposure', 0).clamp(-100, 100),
      contrast: readInt('contrast', 0).clamp(-100, 100),
      structure: readInt('structure', 0).clamp(-100, 100),
      highlights: readInt('highlights', 0).clamp(-100, 100),
      shadows: readInt('shadows', 0).clamp(-100, 100),
      warmth: readInt('warmth', 0).clamp(-100, 100),
      tint: readInt('tint', 0).clamp(-100, 100),
      saturation: readInt('saturation', 0).clamp(-100, 100),
      dirCouplers: readInt('dirCouplers', 50).clamp(0, 100),
      halation: readInt('halation', 20).clamp(0, 100),
      grain: readInt('grain', 25).clamp(0, 100),
      fade: readInt('fade', 0).clamp(0, 100),
      vignette: readInt('vignette', 0).clamp(0, 100),
    );
  }
}

class LookCatalog {
  const LookCatalog({
    required this.stocks,
    required this.papers,
    required this.presets,
    this.autoApplyPresetId,
  });

  final List<FilmStockInfo> stocks;
  final List<PrintPaperInfo> papers;
  final List<LookPreset> presets;
  final String? autoApplyPresetId;

  LookPreset? findPreset(String? id) {
    if (id == null || id.isEmpty) return null;
    for (final p in presets) {
      if (p.id == id) return p;
    }
    return null;
  }

  factory LookCatalog.fromMap(Map<dynamic, dynamic> map) {
    final rawStocks = map['stocks'] as List<dynamic>? ?? const [];
    final rawPapers = map['papers'] as List<dynamic>? ?? const [];
    final rawPresets = map['presets'] as List<dynamic>? ?? const [];
    final autoId = map['autoApplyPresetId'] as String?;
    return LookCatalog(
      stocks: rawStocks
          .whereType<Map<dynamic, dynamic>>()
          .map(FilmStockInfo.fromMap)
          .toList(growable: false),
      papers: rawPapers
          .whereType<Map<dynamic, dynamic>>()
          .map(PrintPaperInfo.fromMap)
          .toList(growable: false),
      presets: rawPresets
          .whereType<Map<dynamic, dynamic>>()
          .map(LookPreset.fromMap)
          .toList(growable: false),
      autoApplyPresetId: (autoId != null && autoId.isNotEmpty) ? autoId : null,
    );
  }

  factory LookCatalog.fallbackDefaults() => const LookCatalog(
        stocks: [
          FilmStockInfo(
            id: 'portra_400',
            name: 'Kodak Portra 400',
            subtitle: 'Natural skin tones, soft highlights',
            defaultHalation: 18,
            defaultGrain: 24,
            defaultDirCouplers: 55,
          ),
          FilmStockInfo(
            id: 'gold_200',
            name: 'Kodak Gold 200',
            subtitle: 'Golden warmth, rich mids & blues',
            defaultHalation: 26,
            defaultGrain: 34,
            defaultDirCouplers: 62,
          ),
          FilmStockInfo(
            id: 'cinestill_800t',
            name: 'CineStill 800T',
            subtitle: 'Remjet-free tungsten halation glow',
            defaultHalation: 72,
            defaultGrain: 42,
            defaultDirCouplers: 52,
          ),
          FilmStockInfo(
            id: 'hp5_400',
            name: 'Ilford HP5 Plus 400',
            subtitle: 'Classic silver-gelatin B&W negative',
            defaultHalation: 12,
            defaultGrain: 48,
            defaultDirCouplers: 0,
          ),
        ],
        papers: [
          PrintPaperInfo(
            id: 'endura',
            name: 'Kodak Endura',
            subtitle: 'Warm RA-4 paper with smooth shadows',
          ),
          PrintPaperInfo(
            id: 'crystal_archive',
            name: 'Fuji Crystal Archive',
            subtitle: 'Crisp whites & cool clean contrast',
          ),
        ],
        presets: [
          LookPreset(
            id: 'builtin_portra_400',
            name: 'Kodak Portra 400',
            stockId: 'portra_400',
            paperId: 'endura',
            builtIn: true,
            contrast: 6,
            structure: 8,
            highlights: -14,
            shadows: 14,
            warmth: 10,
            dirCouplers: 55,
            halation: 18,
            grain: 24,
            fade: 6,
          ),
          LookPreset(
            id: 'builtin_cinestill_800t',
            name: 'CineStill 800T',
            stockId: 'cinestill_800t',
            paperId: 'crystal_archive',
            builtIn: true,
            contrast: 16,
            structure: 6,
            highlights: -18,
            shadows: 12,
            warmth: -16,
            tint: -6,
            dirCouplers: 52,
            halation: 72,
            grain: 42,
            fade: 10,
          ),
        ],
      );
}

class LookExportResult {
  const LookExportResult({
    required this.localPath,
    required this.elapsedMs,
    this.width = 0,
    this.height = 0,
  });

  final String localPath;
  final int elapsedMs;
  final int width;
  final int height;

  factory LookExportResult.fromMap(Map<dynamic, dynamic> map) {
    return LookExportResult(
      localPath: (map['localPath'] as String?) ?? '',
      elapsedMs: (map['elapsedMs'] as num?)?.toInt() ?? 0,
      width: (map['width'] as num?)?.toInt() ?? 0,
      height: (map['height'] as num?)?.toInt() ?? 0,
    );
  }
}
