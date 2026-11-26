package com.bionz.looklab

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.LruCache
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.IOException
import java.io.RandomAccessFile
import java.util.Locale
import kotlin.math.max
import kotlin.math.min

data class ExportResult(
    val localPath: String,
    val elapsedMs: Long,
    val width: Int,
    val height: Int
)

/**
 * Decodes synced Sony .JPG / .ARW files (including fast embedded-JPEG extraction
 * from Sony ARW TIFF IFDs), caches preview bitmaps, renders SpektraFilm looks,
 * and publishes graded JPEGs into DCIM/BionzLookLab/Graded via MediaStore.
 */
object LookLabImageIO {

    private val previewCache = object : LruCache<String, Bitmap>(4) {}
    private val thumbCache = object : LruCache<String, ByteArray>(32) {}

    fun loadThumbnailJpeg(context: Context, dcfName: String, maxDim: Int = 360): ByteArray? {
        val cacheKey = "$dcfName:$maxDim"
        thumbCache.get(cacheKey)?.let { return it }
        val bmp = loadDecodedBitmap(context, dcfName, null, maxDim) ?: return null
        val bytes = compressJpeg(bmp, 84)
        thumbCache.put(cacheKey, bytes)
        return bytes
    }

    fun renderPreviewJpeg(
        context: Context,
        dcfName: String,
        preset: LookPreset,
        maxDim: Int = 1080
    ): ByteArray? {
        val key = "$dcfName:$maxDim"
        val base = previewCache.get(key) ?: loadDecodedBitmap(context, dcfName, null, maxDim)?.also {
            previewCache.put(key, it)
        } ?: return null

        val graded = SpektraFilmEngine.render(base, preset)
        return try {
            compressJpeg(graded, 90)
        } finally {
            if (graded !== base) graded.recycle()
        }
    }

    fun exportGradedToMediaStore(
        context: Context,
        dcfName: String,
        preset: LookPreset,
        sourceFile: File? = null,
        maxDim: Int = 3000
    ): ExportResult {
        val t0 = System.currentTimeMillis()
        val src = loadDecodedBitmap(context, dcfName, sourceFile, maxDim)
            ?: throw IOException("Unable to decode $dcfName for grading")
        val graded = try {
            SpektraFilmEngine.render(src, preset)
        } finally {
            src.recycle()
        }
        val outName = buildGradedFilename(dcfName, preset)
        val outW = graded.width
        val outH = graded.height
        val relPath = try {
            saveBitmapToMediaStore(context, graded, outName)
        } finally {
            graded.recycle()
        }
        val elapsed = (System.currentTimeMillis() - t0).coerceAtLeast(1L)
        return ExportResult(relPath, elapsed, outW, outH)
    }

    private fun buildGradedFilename(dcfName: String, preset: LookPreset): String {
        val dot = dcfName.lastIndexOf('.')
        val stem = if (dot > 0) dcfName.substring(0, dot) else dcfName
        return "${stem}_${preset.slug()}.JPG"
    }

    private fun saveBitmapToMediaStore(context: Context, bmp: Bitmap, outName: String): String {
        val resolver = context.contentResolver
        val relPath = "${Environment.DIRECTORY_DCIM}/BionzLookLab/Graded"
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, outName)
            put(MediaStore.MediaColumns.MIME_TYPE, "image/jpeg")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                put(MediaStore.MediaColumns.RELATIVE_PATH, relPath)
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            }
        }
        val uri = resolver.insert(
            MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY),
            values
        ) ?: throw IOException("MediaStore insert returned null for $outName")

        var ok = false
        try {
            resolver.openOutputStream(uri, "w")?.use { os ->
                if (!bmp.compress(Bitmap.CompressFormat.JPEG, 94, os)) {
                    throw IOException("JPEG compress failed for $outName")
                }
                os.flush()
            } ?: throw IOException("Failed to open OutputStream for $outName")

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val pub = ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }
                resolver.update(uri, pub, null, null)
            }
            ok = true
            return "$relPath/$outName"
        } finally {
            if (!ok) resolver.delete(uri, null, null)
        }
    }

    private fun compressJpeg(bmp: Bitmap, quality: Int): ByteArray {
        val out = ByteArrayOutputStream(256 * 1024)
        bmp.compress(Bitmap.CompressFormat.JPEG, quality, out)
        return out.toByteArray()
    }

    private fun loadDecodedBitmap(
        context: Context,
        dcfName: String,
        directFile: File?,
        maxDim: Int
    ): Bitmap? {
        if (directFile != null && directFile.isFile) {
            decodeFileOrArw(directFile, maxDim)?.let { return it }
        }
        val directPublic = File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DCIM),
            "BionzLookLab/$dcfName"
        )
        if (directPublic.isFile && directPublic.canRead()) {
            decodeFileOrArw(directPublic, maxDim)?.let { return it }
        }
        val uri = findMediaUriByName(context, dcfName) ?: return null
        return decodeUriOrArw(context, uri, dcfName, maxDim)
    }

    private fun findMediaUriByName(context: Context, dcfName: String): Uri? {
        val collections = listOf(
            MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY),
            MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        )
        val projection = arrayOf(MediaStore.MediaColumns._ID)
        val selection = "${MediaStore.MediaColumns.DISPLAY_NAME} = ?"
        val args = arrayOf(dcfName)
        for (col in collections) {
            try {
                context.contentResolver.query(
                    col,
                    projection,
                    selection,
                    args,
                    "${MediaStore.MediaColumns.DATE_MODIFIED} DESC"
                )?.use { cursor ->
                    if (cursor.moveToFirst()) {
                        val id = cursor.getLong(0)
                        return Uri.withAppendedPath(col, id.toString())
                    }
                }
            } catch (_: Throwable) {
            }
        }
        return null
    }

    private fun decodeFileOrArw(file: File, maxDim: Int): Bitmap? {
        val isArw = file.name.lowercase(Locale.US).endsWith(".arw")
        if (isArw) {
            extractEmbeddedJpegFromArwFile(file)?.let { (jpgBytes, orientation) ->
                return decodeByteArrayScaled(jpgBytes, maxDim, orientation)
            }
        }
        val orientation = FileInputStream(file).use { readExifOrientation(it.readNOrHead(65536)) }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val opts = BitmapFactory.Options().apply {
            inSampleSize = computeInSampleSize(bounds.outWidth, bounds.outHeight, maxDim)
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        val rawBmp = BitmapFactory.decodeFile(file.absolutePath, opts) ?: return null
        return finalizeBitmap(rawBmp, maxDim, orientation)
    }

    private fun decodeUriOrArw(context: Context, uri: Uri, dcfName: String, maxDim: Int): Bitmap? {
        val isArw = dcfName.lowercase(Locale.US).endsWith(".arw")
        val tmp = if (isArw) {
            // Stage embedded preview read via ParcelFileDescriptor when possible, else temp copy
            try {
                context.contentResolver.openInputStream(uri)?.use { input ->
                    val prefix = input.readNOrHead(3 * 1024 * 1024)
                    val (jpg, orient) = extractLargestJpegFromBuffer(prefix)
                    if (jpg != null) {
                        return decodeByteArrayScaled(jpg, maxDim, orient)
                    }
                }
            } catch (_: Throwable) {
            }
            null
        } else {
            null
        }
        if (tmp != null) return tmp

        val orientation = try {
            context.contentResolver.openInputStream(uri)?.use { readExifOrientation(it.readNOrHead(65536)) } ?: 1
        } catch (_: Throwable) {
            1
        }

        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        context.contentResolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, bounds)
        }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val opts = BitmapFactory.Options().apply {
            inSampleSize = computeInSampleSize(bounds.outWidth, bounds.outHeight, maxDim)
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        val decoded = context.contentResolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, opts)
        } ?: return null
        return finalizeBitmap(decoded, maxDim, orientation)
    }

    private fun decodeByteArrayScaled(bytes: ByteArray, maxDim: Int, orientation: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val opts = BitmapFactory.Options().apply {
            inSampleSize = computeInSampleSize(bounds.outWidth, bounds.outHeight, maxDim)
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        val rawBmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts) ?: return null
        return finalizeBitmap(rawBmp, maxDim, orientation)
    }

    private fun computeInSampleSize(w: Int, h: Int, maxDim: Int): Int {
        var sample = 1
        val longer = max(w, h)
        while ((longer / (sample * 2)) >= maxDim) {
            sample *= 2
        }
        return sample
    }

    private fun finalizeBitmap(src: Bitmap, maxDim: Int, orientation: Int): Bitmap {
        val rot = when (orientation) {
            3 -> 180f
            6 -> 90f
            8 -> 270f
            else -> 0f
        }
        val w = if (rot == 90f || rot == 270f) src.height else src.width
        val h = if (rot == 90f || rot == 270f) src.width else src.height
        val longer = max(w, h)
        val scale = if (longer > maxDim) maxDim.toFloat() / longer.toFloat() else 1.0f

        if (rot == 0f && scale == 1.0f && src.config == Bitmap.Config.ARGB_8888) {
            return src
        }
        val m = Matrix()
        if (rot != 0f) m.postRotate(rot)
        if (scale != 1.0f) m.postScale(scale, scale)
        val transformed = Bitmap.createBitmap(src, 0, 0, src.width, src.height, m, true)
        if (transformed !== src) src.recycle()
        return transformed
    }

    /**
     * Parses Sony ARW TIFF IFD0/IFD1/SubIFD pointers to extract the largest embedded JPEG preview
     * in-place without reading the entire 25 MB RAW sensor payload into memory.
     */
    private fun extractEmbeddedJpegFromArwFile(file: File): Pair<ByteArray, Int>? {
        return try {
            RandomAccessFile(file, "r").use { raf ->
                val len = raf.length()
                if (len < 16L) return null
                val hdr = ByteArray(min(len, 256 * 1024L).toInt())
                raf.readFully(hdr)
                val orient = parseTiffOrientation(hdr, 0)
                val candidates = ArrayList<Pair<Int, Int>>()
                findTiffJpegOffsets(hdr, candidates)
                val best = candidates.maxByOrNull { it.second }
                if (best != null && best.first > 0 && best.second > 4096 &&
                    best.first.toLong() + best.second.toLong() <= len
                ) {
                    val out = ByteArray(best.second)
                    raf.seek(best.first.toLong())
                    raf.readFully(out)
                    return Pair(out, orient)
                }
                // Fallback: scan first 4 MiB for largest SOI..EOI block
                val scanLen = min(len, 4L * 1024L * 1024L).toInt()
                val buf = ByteArray(scanLen)
                raf.seek(0L)
                raf.readFully(buf)
                val (jpg, bufOrient) = extractLargestJpegFromBuffer(buf)
                if (jpg != null) Pair(jpg, if (orient != 1) orient else bufOrient) else null
            }
        } catch (_: Throwable) {
            null
        }
    }

    private fun extractLargestJpegFromBuffer(buf: ByteArray): Pair<ByteArray?, Int> {
        val orient = parseTiffOrientation(buf, 0)
        var bestStart = -1
        var bestLen = 0
        var i = 0
        while (i + 3 < buf.size) {
            if (buf[i] == 0xFF.toByte() && buf[i + 1] == 0xD8.toByte() && buf[i + 2] == 0xFF.toByte()) {
                var j = i + 3
                var depth = 0
                while (j + 1 < buf.size) {
                    if (buf[j] == 0xFF.toByte() && buf[j + 1] == 0xD8.toByte() && buf[j + 2] == 0xFF.toByte()) {
                        depth++
                    } else if (buf[j] == 0xFF.toByte() && buf[j + 1] == 0xD9.toByte()) {
                        if (depth == 0) {
                            val size = (j + 2) - i
                            if (size > bestLen) {
                                bestStart = i
                                bestLen = size
                            }
                            break
                        } else {
                            depth--
                        }
                    }
                    j++
                }
                i = max(i + 3, j + 2)
            } else {
                i++
            }
        }
        if (bestStart >= 0 && bestLen > 4096) {
            return Pair(buf.copyOfRange(bestStart, bestStart + bestLen), orient)
        }
        return Pair(null, orient)
    }

    private fun findTiffJpegOffsets(buf: ByteArray, out: ArrayList<Pair<Int, Int>>) {
        if (buf.size < 12) return
        val le = buf[0] == 'I'.code.toByte() && buf[1] == 'I'.code.toByte()
        val be = buf[0] == 'M'.code.toByte() && buf[1] == 'M'.code.toByte()
        if (!le && !be) return
        val visited = HashSet<Int>()
        val firstIfd = readU32(buf, 4, le)
        val queue = ArrayDeque<Int>()
        queue.add(firstIfd)
        while (queue.isNotEmpty()) {
            val ifd = queue.removeFirst()
            if (ifd <= 0 || ifd + 2 >= buf.size || !visited.add(ifd)) continue
            val count = readU16(buf, ifd, le)
            var jpgOff = -1
            var jpgLen = -1
            for (k in 0 until count) {
                val entry = ifd + 2 + k * 12
                if (entry + 12 > buf.size) break
                val tag = readU16(buf, entry, le)
                val valOrOff = readU32(buf, entry + 8, le)
                when (tag) {
                    0x0201 -> jpgOff = valOrOff // JPEGInterchangeFormat
                    0x0202 -> jpgLen = valOrOff // JPEGInterchangeFormatLength
                    0x014A -> { // SubIFDs
                        val subCnt = readU32(buf, entry + 4, le)
                        if (subCnt == 1) queue.add(valOrOff)
                        else if (subCnt in 2..8 && valOrOff > 0 && valOrOff + subCnt * 4 <= buf.size) {
                            for (s in 0 until subCnt) queue.add(readU32(buf, valOrOff + s * 4, le))
                        }
                    }
                    0x8769 -> queue.add(valOrOff) // ExifIFD
                }
            }
            if (jpgOff > 0 && jpgLen > 4096) {
                out.add(Pair(jpgOff, jpgLen))
            }
            val nextIfdPos = ifd + 2 + count * 12
            if (nextIfdPos + 4 <= buf.size) {
                val nextIfd = readU32(buf, nextIfdPos, le)
                if (nextIfd > 0) queue.add(nextIfd)
            }
        }
    }

    private fun readExifOrientation(head: ByteArray): Int {
        if (head.size < 16) return 1
        // Direct TIFF (ARW)
        if ((head[0] == 'I'.code.toByte() && head[1] == 'I'.code.toByte()) ||
            (head[0] == 'M'.code.toByte() && head[1] == 'M'.code.toByte())
        ) {
            return parseTiffOrientation(head, 0)
        }
        // JPEG APP1 search
        if (head[0] != 0xFF.toByte() || head[1] != 0xD8.toByte()) return 1
        var p = 2
        while (p + 4 < head.size) {
            if (head[p] != 0xFF.toByte()) break
            val marker = head[p + 1].toInt() and 0xFF
            if (marker == 0xDA || marker == 0xD9) break
            val segLen = ((head[p + 2].toInt() and 0xFF) shl 8) or (head[p + 3].toInt() and 0xFF)
            if (segLen < 2 || p + 2 + segLen > head.size) break
            if (marker == 0xE1 && segLen > 14 &&
                head[p + 4] == 'E'.code.toByte() && head[p + 5] == 'x'.code.toByte() &&
                head[p + 6] == 'i'.code.toByte() && head[p + 7] == 'f'.code.toByte()
            ) {
                return parseTiffOrientation(head, p + 10)
            }
            p += 2 + segLen
        }
        return 1
    }

    private fun parseTiffOrientation(buf: ByteArray, tiffBase: Int): Int {
        if (tiffBase + 8 >= buf.size) return 1
        val le = buf[tiffBase] == 'I'.code.toByte() && buf[tiffBase + 1] == 'I'.code.toByte()
        val be = buf[tiffBase] == 'M'.code.toByte() && buf[tiffBase + 1] == 'M'.code.toByte()
        if (!le && !be) return 1
        val ifd = tiffBase + readU32(buf, tiffBase + 4, le)
        if (ifd <= tiffBase || ifd + 2 >= buf.size) return 1
        val count = readU16(buf, ifd, le)
        for (i in 0 until count) {
            val e = ifd + 2 + i * 12
            if (e + 12 > buf.size) break
            if (readU16(buf, e, le) == 0x0112) {
                return readU16(buf, e + 8, le).coerceIn(1, 8)
            }
        }
        return 1
    }

    private fun readU16(b: ByteArray, off: Int, le: Boolean): Int {
        val b0 = b[off].toInt() and 0xFF
        val b1 = b[off + 1].toInt() and 0xFF
        return if (le) (b0 or (b1 shl 8)) else ((b0 shl 8) or b1)
    }

    private fun readU32(b: ByteArray, off: Int, le: Boolean): Int {
        val b0 = b[off].toInt() and 0xFF
        val b1 = b[off + 1].toInt() and 0xFF
        val b2 = b[off + 2].toInt() and 0xFF
        val b3 = b[off + 3].toInt() and 0xFF
        return if (le) (b0 or (b1 shl 8) or (b2 shl 16) or (b3 shl 24))
        else ((b0 shl 24) or (b1 shl 16) or (b2 shl 8) or b3)
    }

    private fun java.io.InputStream.readNOrHead(maxBytes: Int): ByteArray {
        val out = ByteArrayOutputStream(min(maxBytes, 65536))
        val buf = ByteArray(16384)
        var total = 0
        while (total < maxBytes) {
            val want = min(buf.size, maxBytes - total)
            val n = read(buf, 0, want)
            if (n <= 0) break
            out.write(buf, 0, n)
            total += n
        }
        return out.toByteArray()
    }
}
