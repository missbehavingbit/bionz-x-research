package com.bionz.looklab

import android.content.ContentValues
import android.content.Context
import android.net.Network
import android.os.Environment
import android.provider.MediaStore
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.charset.StandardCharsets
import java.util.Locale
import java.util.zip.CRC32

class CameraHttpException(message: String, val httpCode: Int = 0) : IOException(message)

data class DownloadResult(
    val sizeBytes: Long,
    val crc32Hex: String,
    val resumedFromBytes: Long
)

class CameraHttpClient(
    private val host: String,
    private val port: Int,
    private val networkProvider: () -> Network?
) {
    private data class HttpResponse(
        val statusCode: Int,
        val headers: Map<String, String>,
        val bodyBytes: ByteArray
    )

    private fun openBoundSocket(readTimeoutMs: Int): Socket {
        val net = networkProvider()
        val sock = net?.socketFactory?.createSocket() ?: Socket()
        sock.soTimeout = readTimeoutMs
        sock.tcpNoDelay = true
        sock.receiveBufferSize = 1024 * 1024
        sock.connect(InetSocketAddress(host, port), CONNECT_TIMEOUT_MS)
        return sock
    }

    private fun readHeaderBlock(input: InputStream): Pair<Int, Map<String, String>> {
        val headerBuf = ByteArrayOutputStream(512)
        var state = 0
        while (headerBuf.size() < MAX_HEADER_BYTES) {
            val b = input.read()
            if (b < 0) throw CameraHttpException("Unexpected EOF while reading headers")
            headerBuf.write(b)
            state = when {
                state == 0 && b == '\r'.code -> 1
                state == 1 && b == '\n'.code -> 2
                state == 2 && b == '\r'.code -> 3
                state == 3 && b == '\n'.code -> 4
                b == '\r'.code -> 1
                else -> 0
            }
            if (state == 4) break
        }
        if (state != 4) throw CameraHttpException("HTTP response header too large")
        val text = headerBuf.toString(StandardCharsets.US_ASCII.name())
        val lines = text.split("\r\n")
        if (lines.isEmpty()) throw CameraHttpException("Empty HTTP status line")
        val parts = lines[0].split(" ", limit = 3)
        if (parts.size < 2) throw CameraHttpException("Malformed HTTP status line: ${lines[0]}")
        val code = parts[1].toIntOrNull()
            ?: throw CameraHttpException("Invalid HTTP status code: ${lines[0]}")
        val map = LinkedHashMap<String, String>()
        for (i in 1 until lines.size) {
            val line = lines[i]
            if (line.isEmpty()) continue
            val colon = line.indexOf(':')
            if (colon > 0) {
                val key = line.substring(0, colon).trim().lowercase(Locale.US)
                val value = line.substring(colon + 1).trim()
                map[key] = value
            }
        }
        return Pair(code, map)
    }

    private fun readFixedBody(input: InputStream, contentLength: Long?): ByteArray {
        val out = ByteArrayOutputStream(contentLength?.toInt()?.coerceIn(64, 65536) ?: 1024)
        val buf = ByteArray(8192)
        var total = 0L
        while (contentLength == null || total < contentLength) {
            val want = if (contentLength == null) buf.size else minOf(buf.size.toLong(), contentLength - total).toInt()
            val n = input.read(buf, 0, want)
            if (n <= 0) break
            out.write(buf, 0, n)
            total += n
        }
        return out.toByteArray()
    }

    private fun requestText(
        method: String,
        target: String,
        body: ByteArray? = null,
        readTimeoutMs: Int = DEFAULT_READ_TIMEOUT_MS
    ): HttpResponse {
        openBoundSocket(readTimeoutMs).use { sock ->
            val req = StringBuilder()
            req.append(method).append(' ').append(target).append(" HTTP/1.1\r\n")
            req.append("Host: ").append(host).append(':').append(port).append("\r\n")
            req.append("Connection: close\r\n")
            if (body != null) {
                req.append("Content-Type: application/json\r\n")
                req.append("Content-Length: ").append(body.size).append("\r\n")
            }
            req.append("\r\n")
            val os = sock.getOutputStream()
            os.write(req.toString().toByteArray(StandardCharsets.US_ASCII))
            if (body != null && body.isNotEmpty()) {
                os.write(body)
            }
            os.flush()

            val input = BufferedInputStream(sock.getInputStream(), 16384)
            val (code, headers) = readHeaderBlock(input)
            val len = headers["content-length"]?.toLongOrNull()
            val bytes = readFixedBody(input, len)
            return HttpResponse(code, headers, bytes)
        }
    }

    fun fetchStatus(): CameraStatus {
        val resp = requestText("GET", "/status")
        if (resp.statusCode != 200) {
            throw CameraHttpException("/status failed with HTTP ${resp.statusCode}", resp.statusCode)
        }
        val j = JSONObject(String(resp.bodyBytes, StandardCharsets.UTF_8))
        return CameraStatus(
            cameraId = j.optString("camera_id", "unknown"),
            fw = j.optString("fw", ""),
            queueHead = j.optInt("queue_head", 0),
            queueLen = j.optInt("queue_len", 0),
            ack = j.optInt("ack", 0),
            powerState = j.optInt("power_state", 0),
            apChanspec = j.optInt("ap_chanspec", 0),
            apWidth = j.optInt("ap_width", 0),
            uptimeMs = j.optLong("uptime_ms", 0L)
        )
    }

    fun fetchManifestAll(since: Int): List<ManifestEntry> {
        val entries = ArrayList<ManifestEntry>()
        var cur = since
        for (pageIdx in 0 until 64) {
            val resp = requestText("GET", "/manifest?since=$cur")
            if (resp.statusCode != 200) {
                throw CameraHttpException("/manifest failed with HTTP ${resp.statusCode}", resp.statusCode)
            }
            val j = JSONObject(String(resp.bodyBytes, StandardCharsets.UTF_8))
            val files = j.optJSONArray("files") ?: break
            if (files.length() == 0) break
            var lastSeq = cur
            for (i in 0 until files.length()) {
                val item = files.getJSONObject(i)
                val seq = item.getInt("seq_id")
                entries.add(
                    ManifestEntry(
                        seqId = seq,
                        name = item.getString("name"),
                        ext = item.optString("ext", ""),
                        sizeBytes = item.optLong("size", 0L),
                        mtime = item.optLong("mtime", 0L),
                        crc32Hex = item.optString("crc32", "").lowercase(Locale.US)
                    )
                )
                lastSeq = seq
            }
            if (lastSeq <= cur) break
            cur = lastSeq
        }
        return entries
    }

    fun waitEvent(since: Int): Boolean {
        val resp = requestText("GET", "/events?since=$since", readTimeoutMs = EVENT_READ_TIMEOUT_MS)
        if (resp.statusCode == 503) {
            throw CameraHttpException("Camera sync shutting down", 503)
        }
        if (resp.statusCode != 200) {
            throw CameraHttpException("/events failed with HTTP ${resp.statusCode}", resp.statusCode)
        }
        val j = JSONObject(String(resp.bodyBytes, StandardCharsets.UTF_8))
        return j.optBoolean("woke", false)
    }

    fun downloadWithResume(
        entry: ManifestEntry,
        partFile: File,
        onProgress: (bytesReceived: Long, totalBytes: Long) -> Unit
    ): DownloadResult {
        partFile.parentFile?.mkdirs()
        var offset = if (partFile.isFile) partFile.length() else 0L
        openBoundSocket(STREAM_READ_TIMEOUT_MS).use { sock ->
            val req = StringBuilder()
            req.append("GET /file/").append(entry.seqId).append(" HTTP/1.1\r\n")
            req.append("Host: ").append(host).append(':').append(port).append("\r\n")
            if (offset > 0L) {
                req.append("Range: bytes=").append(offset).append("-\r\n")
            }
            req.append("Connection: close\r\n\r\n")
            val os = sock.getOutputStream()
            os.write(req.toString().toByteArray(StandardCharsets.US_ASCII))
            os.flush()

            val input = BufferedInputStream(sock.getInputStream(), IO_CHUNK)
            val (code, headers) = readHeaderBlock(input)
            if (code == 416) {
                partFile.delete()
                throw CameraHttpException("Range not satisfiable; cleared .part", 416)
            }
            if (code != 200 && code != 206) {
                throw CameraHttpException("/file/${entry.seqId} HTTP $code", code)
            }
            if (code == 200 && offset > 0L) {
                offset = 0L
            }
            val serverSize = headers["x-file-size"]?.toLongOrNull() ?: entry.sizeBytes
            val serverCrc = (headers["x-crc32"] ?: entry.crc32Hex).lowercase(Locale.US)
            val contentLength = headers["content-length"]?.toLongOrNull()
                ?: throw CameraHttpException("Missing Content-Length on /file/${entry.seqId}")

            var written = 0L
            val buf = ByteArray(IO_CHUNK)
            val inlineCrc = if (offset == 0L) CRC32() else null
            FileOutputStream(partFile, offset > 0L).use { fos ->
                val bos = BufferedOutputStream(fos, IO_CHUNK)
                while (written < contentLength) {
                    val want = minOf(buf.size.toLong(), contentLength - written).toInt()
                    val n = input.read(buf, 0, want)
                    if (n <= 0) break
                    inlineCrc?.update(buf, 0, n)
                    bos.write(buf, 0, n)
                    written += n
                    onProgress(offset + written, if (serverSize > 0L) serverSize else offset + contentLength)
                }
                bos.flush()
                fos.fd.sync()
            }

            if (written != contentLength) {
                throw CameraHttpException("Short transfer: got $written of $contentLength bytes")
            }
            val total = partFile.length()
            if (serverSize > 0L && total != serverSize) {
                throw CameraHttpException("Size mismatch: local=$total server=$serverSize")
            }
            val localCrc = if (inlineCrc != null) {
                String.format(Locale.US, "%08x", inlineCrc.value and 0xFFFFFFFFL)
            } else {
                computeFileCrc32Hex(partFile)
            }
            if (serverCrc.isNotEmpty() && serverCrc != "pending" && localCrc != serverCrc) {
                partFile.delete()
                throw CameraHttpException("CRC-32 mismatch: local=$localCrc server=$serverCrc")
            }
            return DownloadResult(
                sizeBytes = total,
                crc32Hex = localCrc,
                resumedFromBytes = offset
            )
        }
    }

    fun postAck(seqId: Int, sizeBytes: Long, crc32Hex: String): Int {
        val payload = JSONObject().apply {
            put("size", sizeBytes)
            if (crc32Hex.isNotEmpty()) put("crc32", crc32Hex)
        }.toString().toByteArray(StandardCharsets.UTF_8)
        val resp = requestText("POST", "/ack/$seqId", body = payload)
        if (resp.statusCode != 200) {
            throw CameraHttpException("/ack/$seqId failed with HTTP ${resp.statusCode}", resp.statusCode)
        }
        val j = JSONObject(String(resp.bodyBytes, StandardCharsets.UTF_8))
        return j.optInt("ack", seqId)
    }

    companion object {
        private const val IO_CHUNK = 256 * 1024
        private const val CONNECT_TIMEOUT_MS = 4000
        private const val DEFAULT_READ_TIMEOUT_MS = 10000
        private const val STREAM_READ_TIMEOUT_MS = 15000
        private const val EVENT_READ_TIMEOUT_MS = 28000
        private const val MAX_HEADER_BYTES = 8192

        fun computeFileCrc32Hex(file: File): String {
            val crc = CRC32()
            val buf = ByteArray(IO_CHUNK)
            FileInputStream(file).use { fis ->
                while (true) {
                    val n = fis.read(buf)
                    if (n <= 0) break
                    crc.update(buf, 0, n)
                }
            }
            return String.format(Locale.US, "%08x", crc.value and 0xFFFFFFFFL)
        }

        fun publishToMediaStore(context: Context, verifiedFile: File, dcfName: String): String {
            val lower = dcfName.lowercase(Locale.US)
            val isJpg = lower.endsWith(".jpg") || lower.endsWith(".jpeg")
            val mimeType = if (isJpg) "image/jpeg" else "image/x-sony-arw"
            val resolver = context.contentResolver
            var relPath = "${Environment.DIRECTORY_DCIM}/BionzLookLab"
            val uri = try {
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, dcfName)
                    put(MediaStore.MediaColumns.MIME_TYPE, mimeType)
                    put(MediaStore.MediaColumns.RELATIVE_PATH, relPath)
                    put(MediaStore.MediaColumns.IS_PENDING, 1)
                }
                resolver.insert(
                    MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY),
                    values
                )
            } catch (e: IllegalArgumentException) {
                if (isJpg || android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.Q) {
                    throw e
                }
                relPath = "${Environment.DIRECTORY_DOWNLOADS}/BionzLookLab"
                val dlValues = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, dcfName)
                    put(MediaStore.MediaColumns.MIME_TYPE, mimeType)
                    put(MediaStore.MediaColumns.RELATIVE_PATH, relPath)
                    put(MediaStore.MediaColumns.IS_PENDING, 1)
                }
                resolver.insert(
                    MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY),
                    dlValues
                )
            } ?: throw IOException("MediaStore insert returned null for $dcfName")

            var ok = false
            try {
                resolver.openOutputStream(uri, "w")?.use { out ->
                    val buf = ByteArray(IO_CHUNK)
                    FileInputStream(verifiedFile).use { fis ->
                        while (true) {
                            val n = fis.read(buf)
                            if (n <= 0) break
                            out.write(buf, 0, n)
                        }
                    }
                    out.flush()
                } ?: throw IOException("Failed to open MediaStore OutputStream for $dcfName")

                val publishValues = ContentValues().apply {
                    put(MediaStore.MediaColumns.IS_PENDING, 0)
                }
                resolver.update(uri, publishValues, null, null)
                ok = true
                return "$relPath/$dcfName"
            } finally {
                if (!ok) {
                    resolver.delete(uri, null, null)
                }
            }
        }
    }
}
