package com.bionz.looklab

import android.graphics.Bitmap
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sqrt

/**
 * Physically-inspired SpektraFilm color negative / print paper pipeline.
 *
 * Stages mirror andreavolpato/spektrafilm:
 *   1. Linearize sRGB input + EV exposure + linear WB warmth/tint
 *   2. Pre-emulsion red-layer highlight halation scatter
 *   3. Spectral emulsion crosstalk matrix (3x3)
 *   4. Per-channel H&D characteristic curve mapping + DIR inter-layer couplers
 *   5. Virtual RA-4 / Silver print paper tone curve, highlights/shadows & fade
 *   6. Snapseed-style local luminance Structure (-100..+100)
 *   7. Density-dependent silver-halide grain + vignette + sRGB OETF encoding
 */
object SpektraFilmEngine {

    data class EmulsionSpec(
        val info: FilmStockInfo,
        val isMonochrome: Boolean = false,
        val matrix: FloatArray,
        val gammaR: Float,
        val gammaG: Float,
        val gammaB: Float,
        val toe: Float,
        val shoulder: Float,
        val dirMatrix: FloatArray,
        val chromaGain: Float,
        val grainSize: Float
    )

    data class PaperSpec(
        val info: PrintPaperInfo,
        val contrast: Float,
        val dMinR: Float,
        val dMinG: Float,
        val dMinB: Float,
        val gainR: Float,
        val gainG: Float,
        val gainB: Float
    )

    val STOCKS: List<EmulsionSpec> = listOf(
        EmulsionSpec(
            info = FilmStockInfo("neutral", "Neutral Digital", "Bypass emulsion curves", 0, 0, 0),
            matrix = floatArrayOf(1f, 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f),
            gammaR = 1.0f, gammaG = 1.0f, gammaB = 1.0f, toe = 0.0f, shoulder = 0.0f,
            dirMatrix = floatArrayOf(0f, 0f, 0f, 0f, 0f, 0f, 0f, 0f, 0f),
            chromaGain = 1.0f, grainSize = 0.0f
        ),
        EmulsionSpec(
            info = FilmStockInfo("portra_400", "Kodak Portra 400", "Natural skin tones, soft highlights", 18, 24, 55),
            matrix = floatArrayOf(
                0.96f, 0.06f, -0.02f,
                -0.02f, 0.99f, 0.03f,
                0.01f, 0.05f, 0.94f
            ),
            gammaR = 0.94f, gammaG = 0.96f, gammaB = 0.99f, toe = 0.14f, shoulder = 0.36f,
            dirMatrix = floatArrayOf(
                0.00f, 0.24f, 0.10f,
                0.22f, 0.00f, 0.18f,
                0.08f, 0.20f, 0.00f
            ),
            chromaGain = 0.96f, grainSize = 0.85f
        ),
        EmulsionSpec(
            info = FilmStockInfo("portra_160", "Kodak Portra 160", "Fine grain pastel portrait negative", 14, 15, 50),
            matrix = floatArrayOf(
                0.95f, 0.06f, -0.01f,
                -0.01f, 0.98f, 0.03f,
                0.01f, 0.04f, 0.95f
            ),
            gammaR = 0.90f, gammaG = 0.92f, gammaB = 0.95f, toe = 0.16f, shoulder = 0.40f,
            dirMatrix = floatArrayOf(
                0.00f, 0.20f, 0.08f,
                0.18f, 0.00f, 0.15f,
                0.06f, 0.16f, 0.00f
            ),
            chromaGain = 0.91f, grainSize = 0.58f
        ),
        EmulsionSpec(
            info = FilmStockInfo("gold_200", "Kodak Gold 200", "Golden warmth, rich mids & blues", 26, 34, 62),
            matrix = floatArrayOf(
                1.03f, 0.02f, -0.05f,
                -0.03f, 0.97f, 0.06f,
                0.02f, 0.03f, 0.95f
            ),
            gammaR = 1.05f, gammaG = 1.02f, gammaB = 0.98f, toe = 0.12f, shoulder = 0.28f,
            dirMatrix = floatArrayOf(
                0.00f, 0.28f, 0.14f,
                0.24f, 0.00f, 0.20f,
                0.10f, 0.22f, 0.00f
            ),
            chromaGain = 1.12f, grainSize = 1.02f
        ),
        EmulsionSpec(
            info = FilmStockInfo("ektar_100", "Kodak Ektar 100", "Ultra-vivid landscapes & deep reds", 20, 14, 78),
            matrix = floatArrayOf(
                1.06f, -0.03f, -0.03f,
                -0.04f, 1.04f, 0.00f,
                0.00f, -0.04f, 1.04f
            ),
            gammaR = 1.14f, gammaG = 1.12f, gammaB = 1.15f, toe = 0.08f, shoulder = 0.20f,
            dirMatrix = floatArrayOf(
                0.00f, 0.34f, 0.18f,
                0.30f, 0.00f, 0.26f,
                0.16f, 0.28f, 0.00f
            ),
            chromaGain = 1.24f, grainSize = 0.50f
        ),
        EmulsionSpec(
            info = FilmStockInfo("vision3_250d", "Kodak Vision3 250D", "Cinema ECN-2 wide dynamic range", 24, 28, 52),
            matrix = floatArrayOf(
                0.97f, 0.04f, -0.01f,
                -0.01f, 0.97f, 0.04f,
                0.02f, 0.04f, 0.94f
            ),
            gammaR = 0.88f, gammaG = 0.90f, gammaB = 0.92f, toe = 0.20f, shoulder = 0.44f,
            dirMatrix = floatArrayOf(
                0.00f, 0.22f, 0.10f,
                0.20f, 0.00f, 0.18f,
                0.10f, 0.18f, 0.00f
            ),
            chromaGain = 0.98f, grainSize = 0.92f
        ),
        EmulsionSpec(
            info = FilmStockInfo("cinestill_800t", "CineStill 800T", "Remjet-free tungsten halation glow", 72, 42, 52),
            matrix = floatArrayOf(
                0.95f, 0.02f, 0.03f,
                0.01f, 0.96f, 0.03f,
                0.03f, 0.06f, 0.91f
            ),
            gammaR = 1.02f, gammaG = 1.00f, gammaB = 1.08f, toe = 0.15f, shoulder = 0.30f,
            dirMatrix = floatArrayOf(
                0.00f, 0.22f, 0.14f,
                0.18f, 0.00f, 0.20f,
                0.12f, 0.22f, 0.00f
            ),
            chromaGain = 1.04f, grainSize = 1.25f
        ),
        EmulsionSpec(
            info = FilmStockInfo("pro_400h", "Fuji Pro 400H", "Airy mint-cyan shadows, pastel mids", 14, 22, 48),
            matrix = floatArrayOf(
                0.92f, 0.07f, 0.01f,
                -0.02f, 1.01f, 0.01f,
                -0.01f, 0.05f, 0.96f
            ),
            gammaR = 0.91f, gammaG = 0.95f, gammaB = 0.94f, toe = 0.18f, shoulder = 0.38f,
            dirMatrix = floatArrayOf(
                0.00f, 0.22f, 0.10f,
                0.18f, 0.00f, 0.16f,
                0.10f, 0.18f, 0.00f
            ),
            chromaGain = 0.93f, grainSize = 0.80f
        ),
        EmulsionSpec(
            info = FilmStockInfo("velvia_50", "Fujichrome Velvia 50", "Punchy slide reversal contrast", 10, 12, 72),
            matrix = floatArrayOf(
                1.08f, -0.05f, -0.03f,
                -0.04f, 1.05f, -0.01f,
                -0.02f, -0.03f, 1.05f
            ),
            gammaR = 1.24f, gammaG = 1.20f, gammaB = 1.25f, toe = 0.06f, shoulder = 0.16f,
            dirMatrix = floatArrayOf(
                0.00f, 0.32f, 0.18f,
                0.28f, 0.00f, 0.24f,
                0.18f, 0.26f, 0.00f
            ),
            chromaGain = 1.28f, grainSize = 0.45f
        ),
        EmulsionSpec(
            info = FilmStockInfo("hp5_400", "Ilford HP5 Plus 400", "Classic silver-gelatin B&W negative", 12, 48, 0),
            isMonochrome = true,
            matrix = floatArrayOf(
                0.27f, 0.61f, 0.12f,
                0.27f, 0.61f, 0.12f,
                0.27f, 0.61f, 0.12f
            ),
            gammaR = 1.08f, gammaG = 1.08f, gammaB = 1.08f, toe = 0.14f, shoulder = 0.30f,
            dirMatrix = floatArrayOf(0f, 0f, 0f, 0f, 0f, 0f, 0f, 0f, 0f),
            chromaGain = 0.0f, grainSize = 1.35f
        )
    )

    val PAPERS: List<PaperSpec> = listOf(
        PaperSpec(
            info = PrintPaperInfo("endura", "Kodak Endura", "Warm RA-4 paper with smooth shadows"),
            contrast = 1.06f, dMinR = 0.012f, dMinG = 0.010f, dMinB = 0.006f,
            gainR = 1.02f, gainG = 1.00f, gainB = 0.97f
        ),
        PaperSpec(
            info = PrintPaperInfo("crystal_archive", "Fuji Crystal Archive", "Crisp whites & cool clean contrast"),
            contrast = 1.10f, dMinR = 0.006f, dMinG = 0.010f, dMinB = 0.013f,
            gainR = 0.98f, gainG = 1.01f, gainB = 1.03f
        ),
        PaperSpec(
            info = PrintPaperInfo("neutral_scan", "Neutral Scan", "Flat scanner balance without tint"),
            contrast = 1.00f, dMinR = 0.0f, dMinG = 0.0f, dMinB = 0.0f,
            gainR = 1.00f, gainG = 1.00f, gainB = 1.00f
        ),
        PaperSpec(
            info = PrintPaperInfo("warm_silver", "Warm Silver Gelatin", "Rich silver blacks & ivory highlights"),
            contrast = 1.12f, dMinR = 0.014f, dMinG = 0.011f, dMinB = 0.004f,
            gainR = 1.03f, gainG = 1.00f, gainB = 0.93f
        )
    )

    private val stockMap: Map<String, EmulsionSpec> = STOCKS.associateBy { it.info.id }
    private val paperMap: Map<String, PaperSpec> = PAPERS.associateBy { it.info.id }

    private val srgbToLinearLut = FloatArray(256) { i ->
        val s = i / 255.0f
        if (s <= 0.04045f) s / 12.92f else ((s + 0.055f) / 1.055f).toDouble().pow(2.4).toFloat()
    }

    private const val OETF_SIZE = 4096
    private val linearToSrgbLut = IntArray(OETF_SIZE + 1) { i ->
        val l = i.toFloat() / OETF_SIZE.toFloat()
        val s = if (l <= 0.0031308f) {
            12.92f * l
        } else {
            1.055f * l.toDouble().pow(1.0 / 2.4).toFloat() - 0.055f
        }
        (s * 255.0f + 0.5f).toInt().coerceIn(0, 255)
    }

    fun stockInfos(): List<FilmStockInfo> = STOCKS.map { it.info }
    fun paperInfos(): List<PrintPaperInfo> = PAPERS.map { it.info }

    fun render(src: Bitmap, preset: LookPreset): Bitmap {
        val w = src.width
        val h = src.height
        val n = w * h
        val pixels = IntArray(n)
        src.getPixels(pixels, 0, w, 0, 0, w, h)

        val stock = stockMap[preset.stockId] ?: STOCKS[1]
        val paper = paperMap[preset.paperId] ?: PAPERS[0]

        val evGain = 2.0f.pow(preset.exposure * 2.0f / 100.0f)
        val warmthF = preset.warmth / 100.0f
        val tintF = preset.tint / 100.0f
        val wbR = evGain * (1.0f + warmthF * 0.28f + tintF * 0.08f).coerceIn(0.45f, 2.2f)
        val wbG = evGain * (1.0f - abs(warmthF) * 0.03f - tintF * 0.16f).coerceIn(0.45f, 2.2f)
        val wbB = evGain * (1.0f - warmthF * 0.28f + tintF * 0.08f).coerceIn(0.45f, 2.2f)

        val linR = FloatArray(n)
        val linG = FloatArray(n)
        val linB = FloatArray(n)

        for (i in 0 until n) {
            val px = pixels[i]
            linR[i] = srgbToLinearLut[(px ushr 16) and 0xFF] * wbR
            linG[i] = srgbToLinearLut[(px ushr 8) and 0xFF] * wbG
            linB[i] = srgbToLinearLut[px and 0xFF] * wbB
        }

        // Stage 2: Red-layer optical halation (quarter-resolution scatter + red bleed)
        val halationStrength = (preset.halation / 100.0f) * 1.15f
        if (halationStrength > 0.01f && w >= 16 && h >= 16) {
            applyHalation(linR, linG, linB, w, h, halationStrength)
        }

        // Stage 3 & 4: Spectral matrix + H&D curves + DIR couplers + Print Paper + Tone
        val bypassEmulsion = stock.info.id == "neutral"
        val m = stock.matrix
        val dm = stock.dirMatrix
        val dirK = (preset.dirCouplers / 100.0f) * 0.65f
        val paperContrast = paper.contrast * (1.0f + preset.contrast * 0.45f / 100.0f)
        val hiShift = preset.highlights / 100.0f
        val shShift = preset.shadows / 100.0f
        val fadeLift = (preset.fade / 100.0f) * 0.085f
        val satMul = if (stock.isMonochrome) {
            0.0f
        } else {
            (stock.chromaGain * (1.0f + preset.saturation * 0.70f / 100.0f)).coerceIn(0.0f, 2.2f)
        }

        for (i in 0 until n) {
            var r = linR[i]
            var g = linG[i]
            var b = linB[i]

            if (!bypassEmulsion) {
                val sr = max(0f, m[0] * r + m[1] * g + m[2] * b)
                val sg = max(0f, m[3] * r + m[4] * g + m[5] * b)
                val sb = max(0f, m[6] * r + m[7] * g + m[8] * b)

                var dr = hdCurve(sr, stock.gammaR, stock.toe, stock.shoulder)
                var dg = hdCurve(sg, stock.gammaG, stock.toe, stock.shoulder)
                var db = hdCurve(sb, stock.gammaB, stock.toe, stock.shoulder)

                if (dirK > 0.001f && !stock.isMonochrome) {
                    val inhR = dm[0] * dr + dm[1] * dg + dm[2] * db
                    val inhG = dm[3] * dr + dm[4] * dg + dm[5] * db
                    val inhB = dm[6] * dr + dm[7] * dg + dm[8] * db
                    val corrR = (dr - dirK * (inhR - dr * 0.25f)).coerceIn(0f, 1.35f)
                    val corrG = (dg - dirK * (inhG - dg * 0.25f)).coerceIn(0f, 1.35f)
                    val corrB = (db - dirK * (inhB - db * 0.25f)).coerceIn(0f, 1.35f)
                    dr = corrR
                    dg = corrG
                    db = corrB
                }

                r = applyPrintPaper(dr, paperContrast, paper.gainR, paper.dMinR)
                g = applyPrintPaper(dg, paperContrast, paper.gainG, paper.dMinG)
                b = applyPrintPaper(db, paperContrast, paper.gainB, paper.dMinB)
            } else {
                r = applyPrintPaper(r.coerceIn(0f, 1.35f), 1.0f + preset.contrast * 0.45f / 100.0f, 1f, 0f)
                g = applyPrintPaper(g.coerceIn(0f, 1.35f), 1.0f + preset.contrast * 0.45f / 100.0f, 1f, 0f)
                b = applyPrintPaper(b.coerceIn(0f, 1.35f), 1.0f + preset.contrast * 0.45f / 100.0f, 1f, 0f)
            }

            // Highlights / Shadows recovery in linear tone domain
            val lum = (0.2126f * r + 0.7152f * g + 0.0722f * b).coerceIn(0f, 1.5f)
            if (lum > 0.0005f && (abs(hiShift) > 0.005f || abs(shShift) > 0.005f)) {
                val t = lum.coerceIn(0f, 1f)
                val shMask = (1f - t) * (1f - t)
                val hiMask = t * t
                val targetLum = (lum + shShift * 0.24f * shMask + hiShift * 0.22f * hiMask).coerceAtLeast(0f)
                val scale = (targetLum / lum).coerceIn(0.35f, 2.5f)
                r *= scale
                g *= scale
                b *= scale
            }

            // Fade black-point lift + Saturation
            val postLum = 0.2126f * r + 0.7152f * g + 0.0722f * b
            if (satMul != 1.0f) {
                r = postLum + (r - postLum) * satMul
                g = postLum + (g - postLum) * satMul
                b = postLum + (b - postLum) * satMul
            }

            if (fadeLift > 0f) {
                val comp = 1f - fadeLift
                r = r * comp + fadeLift * (1f + paper.dMinR * 4f)
                g = g * comp + fadeLift * (1f + paper.dMinG * 4f)
                b = b * comp + fadeLift * (1f + paper.dMinB * 4f)
            }

            linR[i] = r.coerceIn(0f, 1f)
            linG[i] = g.coerceIn(0f, 1f)
            linB[i] = b.coerceIn(0f, 1f)
        }

        // Stage 6: Snapseed-style local luminance Structure (-100..+100)
        if (preset.structure != 0 && w >= 12 && h >= 12) {
            applySnapseedStructure(linR, linG, linB, w, h, preset.structure / 100.0f)
        }

        // Stage 7: Density-dependent silver-halide grain + vignette + OETF encoding
        val grainStrength = (preset.grain / 100.0f) * max(0.35f, stock.grainSize) * 0.055f
        val vigStrength = (preset.vignette / 100.0f) * 0.58f
        val halfW = w * 0.5f
        val halfH = h * 0.5f
        val invHalfW = if (halfW > 0f) 1f / halfW else 1f
        val invHalfH = if (halfH > 0f) 1f / halfH else 1f
        var rng = 0x9E3779B9u

        var idx = 0
        for (y in 0 until h) {
            val ny = (y - halfH) * invHalfH
            val ny2 = ny * ny
            for (x in 0 until w) {
                var r = linR[idx]
                var g = linG[idx]
                var b = linB[idx]

                if (grainStrength > 0.0005f) {
                    val lum = (0.299f * r + 0.587f * g + 0.114f * b).coerceIn(0f, 1f)
                    val densityWeight = 4.0f * lum * (1.0f - lum) + 0.12f
                    // Xorshift32 triangular-ish noise for smoother organic crystal distribution
                    rng = rng xor (rng shl 13)
                    rng = rng xor (rng shr 17)
                    rng = rng xor (rng shl 5)
                    val u1 = ((rng and 0xFFFFu).toInt() / 32767.5f) - 1.0f
                    val u2 = (((rng shr 16) and 0xFFFFu).toInt() / 32767.5f) - 1.0f
                    val nVal = (u1 + u2) * 0.5f * grainStrength * densityWeight
                    if (stock.isMonochrome) {
                        r += nVal; g += nVal; b += nVal
                    } else {
                        val cVar = u2 * grainStrength * densityWeight * 0.25f
                        r += nVal + cVar
                        g += nVal
                        b += nVal - cVar
                    }
                }

                if (vigStrength > 0.001f) {
                    val nx = (x - halfW) * invHalfW
                    val r2 = nx * nx + ny2
                    val att = (1.0f - vigStrength * r2 * 0.65f).coerceIn(0.28f, 1.0f)
                    r *= att
                    g *= att
                    b *= att
                }

                val ir = linearToSrgbLut[(r.coerceIn(0f, 1f) * OETF_SIZE).toInt()]
                val ig = linearToSrgbLut[(g.coerceIn(0f, 1f) * OETF_SIZE).toInt()]
                val ib = linearToSrgbLut[(b.coerceIn(0f, 1f) * OETF_SIZE).toInt()]
                pixels[idx] = (0xFF shl 24) or (ir shl 16) or (ig shl 8) or ib
                idx++
            }
        }

        val out = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        out.setPixels(pixels, 0, w, 0, 0, w, h)
        return out
    }

    private fun hdCurve(x: Float, gamma: Float, toe: Float, shoulder: Float): Float {
        if (x <= 0.0001f) return 0f
        // Map linear light [0..1.4] through film-like log sigmoid roll-off
        val boosted = x * (1.0f + shoulder * 0.6f)
        val compressed = boosted / (boosted + 0.42f + shoulder * 0.55f)
        val mid = (compressed * (1.42f + shoulder * 0.55f)).coerceIn(0f, 1.35f)
        val curved = if (abs(gamma - 1.0f) > 0.005f) {
            mid.toDouble().pow(gamma.toDouble()).toFloat()
        } else {
            mid
        }
        return (curved + toe * 0.025f * (1f - min(1f, curved))).coerceIn(0f, 1.35f)
    }

    private fun applyPrintPaper(v: Float, contrast: Float, gain: Float, dMin: Float): Float {
        // Soft S-curve around 0.18 middle gray + RA-4 paper base density lift
        val c = contrast.coerceIn(0.55f, 1.75f)
        val pivot = 0.22f
        val x = if (v > 0.0001f) {
            (pivot * (v / pivot).toDouble().pow(c.toDouble()).toFloat())
        } else {
            0f
        }
        val soft = x / (x + 0.85f) * 1.85f
        return (soft * gain + dMin).coerceIn(0f, 1.25f)
    }

    private fun applyHalation(
        linR: FloatArray,
        linG: FloatArray,
        linB: FloatArray,
        w: Int,
        h: Int,
        strength: Float
    ) {
        val sw = max(4, w / 4)
        val sh = max(4, h / 4)
        val glow = FloatArray(sw * sh)
        for (sy in 0 until sh) {
            val yStart = sy * 4
            val yEnd = min(h, yStart + 4)
            for (sx in 0 until sw) {
                val xStart = sx * 4
                val xEnd = min(w, xStart + 4)
                var sum = 0f
                var cnt = 0
                for (yy in yStart until yEnd) {
                    val row = yy * w
                    for (xx in xStart until xEnd) {
                        val idx = row + xx
                        val l = 0.35f * linR[idx] + 0.55f * linG[idx] + 0.10f * linB[idx]
                        if (l > 0.62f) {
                            sum += (l - 0.62f)
                        }
                        cnt++
                    }
                }
                glow[sy * sw + sx] = if (cnt > 0) sum / cnt else 0f
            }
        }
        val radius = max(2, min(sw, sh) / 36)
        boxBlur2Pass(glow, sw, sh, radius)

        for (y in 0 until h) {
            val sy = min(sh - 1, y / 4)
            val sRow = sy * sw
            val row = y * w
            for (x in 0 until w) {
                val sx = min(sw - 1, x / 4)
                val gVal = glow[sRow + sx] * strength
                if (gVal > 0.0002f) {
                    val idx = row + x
                    linR[idx] += gVal * 1.45f
                    linG[idx] += gVal * 0.28f
                    linB[idx] += gVal * 0.04f
                }
            }
        }
    }

    private fun applySnapseedStructure(
        linR: FloatArray,
        linG: FloatArray,
        linB: FloatArray,
        w: Int,
        h: Int,
        amount: Float
    ) {
        val n = w * h
        val lum = FloatArray(n)
        val blur = FloatArray(n)
        for (i in 0 until n) {
            val l = (0.2126f * linR[i] + 0.7152f * linG[i] + 0.0722f * linB[i]).coerceIn(0f, 1f)
            lum[i] = l
            blur[i] = l
        }
        val radius = max(3, min(w, h) / 72)
        boxBlur2Pass(blur, w, h, radius)

        val posGain = if (amount > 0f) amount * 1.55f else amount * 0.92f
        for (i in 0 until n) {
            val l = lum[i]
            if (l <= 0.0005f) continue
            val detail = l - blur[i]
            // Weight strongest in midtones to prevent clipping/haloing in extremes
            val midWeight = if (amount > 0f) {
                0.35f + 0.65f * (1f - abs(2f * l - 1f))
            } else {
                1.0f
            }
            val targetL = (l + detail * posGain * midWeight).coerceIn(0f, 1f)
            val ratio = (targetL / l).coerceIn(0.4f, 2.2f)
            linR[i] = (linR[i] * ratio).coerceIn(0f, 1f)
            linG[i] = (linG[i] * ratio).coerceIn(0f, 1f)
            linB[i] = (linB[i] * ratio).coerceIn(0f, 1f)
        }
    }

    private fun boxBlur2Pass(data: FloatArray, w: Int, h: Int, radius: Int) {
        val tmp = FloatArray(w * h)
        boxBlurPass(data, tmp, w, h, radius)
        boxBlurPass(tmp, data, w, h, max(1, radius / 2))
    }

    private fun boxBlurPass(src: FloatArray, dst: FloatArray, w: Int, h: Int, radius: Int) {
        val horiz = FloatArray(w * h)
        val win = 2 * radius + 1
        // Horizontal sliding window
        for (y in 0 until h) {
            val row = y * w
            var acc = 0f
            for (k in -radius..radius) {
                acc += src[row + k.coerceIn(0, w - 1)]
            }
            for (x in 0 until w) {
                horiz[row + x] = acc / win
                val removeX = (x - radius).coerceIn(0, w - 1)
                val addX = (x + radius + 1).coerceIn(0, w - 1)
                acc += src[row + addX] - src[row + removeX]
            }
        }
        // Vertical sliding window
        for (x in 0 until w) {
            var acc = 0f
            for (k in -radius..radius) {
                acc += horiz[k.coerceIn(0, h - 1) * w + x]
            }
            for (y in 0 until h) {
                dst[y * w + x] = acc / win
                val removeY = (y - radius).coerceIn(0, h - 1)
                val addY = (y + radius + 1).coerceIn(0, h - 1)
                acc += horiz[addY * w + x] - horiz[removeY * w + x]
            }
        }
    }
}
