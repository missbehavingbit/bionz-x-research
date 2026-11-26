package com.bionz.looklab

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper

class SyncStore(context: Context) :
    SQLiteOpenHelper(context.applicationContext, DB_NAME, null, DB_VERSION) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS synced_files (
                camera_id TEXT NOT NULL,
                dcf_name TEXT NOT NULL,
                seq_id INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL,
                crc32 TEXT NOT NULL,
                local_path TEXT NOT NULL,
                synced_at_ms INTEGER NOT NULL,
                PRIMARY KEY (camera_id, dcf_name)
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS watermarks (
                camera_id TEXT PRIMARY KEY,
                ack_seq INTEGER NOT NULL
            )
            """.trimIndent()
        )
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        onCreate(db)
    }

    @Synchronized
    fun isSynced(cameraId: String, dcfName: String): Boolean {
        readableDatabase.rawQuery(
            "SELECT 1 FROM synced_files WHERE camera_id = ? AND dcf_name = ? LIMIT 1",
            arrayOf(cameraId, dcfName)
        ).use { cursor ->
            return cursor.moveToFirst()
        }
    }

    @Synchronized
    fun getWatermark(cameraId: String): Int {
        readableDatabase.rawQuery(
            "SELECT ack_seq FROM watermarks WHERE camera_id = ? LIMIT 1",
            arrayOf(cameraId)
        ).use { cursor ->
            return if (cursor.moveToFirst()) cursor.getInt(0) else 0
        }
    }

    @Synchronized
    fun updateWatermark(cameraId: String, ackSeq: Int) {
        val current = getWatermark(cameraId)
        val merged = maxOf(current, ackSeq)
        val cv = ContentValues().apply {
            put("camera_id", cameraId)
            put("ack_seq", merged)
        }
        writableDatabase.insertWithOnConflict(
            "watermarks",
            null,
            cv,
            SQLiteDatabase.CONFLICT_REPLACE
        )
    }

    @Synchronized
    fun resetWatermark(cameraId: String, ackSeq: Int = 0) {
        val cv = ContentValues().apply {
            put("camera_id", cameraId)
            put("ack_seq", ackSeq)
        }
        writableDatabase.insertWithOnConflict(
            "watermarks",
            null,
            cv,
            SQLiteDatabase.CONFLICT_REPLACE
        )
    }

    @Synchronized
    fun recordSynced(record: SyncedFileRecord) {
        val cv = ContentValues().apply {
            put("camera_id", record.cameraId)
            put("dcf_name", record.dcfName)
            put("seq_id", record.seqId)
            put("size_bytes", record.sizeBytes)
            put("crc32", record.crc32Hex)
            put("local_path", record.localPath)
            put("synced_at_ms", record.syncedAtMs)
        }
        writableDatabase.insertWithOnConflict(
            "synced_files",
            null,
            cv,
            SQLiteDatabase.CONFLICT_REPLACE
        )
    }

    @Synchronized
    fun totalCount(): Int {
        readableDatabase.rawQuery("SELECT COUNT(*) FROM synced_files", null).use { cursor ->
            return if (cursor.moveToFirst()) cursor.getInt(0) else 0
        }
    }

    @Synchronized
    fun recentFiles(limit: Int = 50): List<SyncedFileRecord> {
        val out = ArrayList<SyncedFileRecord>()
        readableDatabase.rawQuery(
            """
            SELECT camera_id, dcf_name, seq_id, size_bytes, crc32, local_path, synced_at_ms
            FROM synced_files
            ORDER BY synced_at_ms DESC
            LIMIT ?
            """.trimIndent(),
            arrayOf(limit.toString())
        ).use { cursor ->
            while (cursor.moveToNext()) {
                out.add(
                    SyncedFileRecord(
                        cameraId = cursor.getString(0),
                        dcfName = cursor.getString(1),
                        seqId = cursor.getInt(2),
                        sizeBytes = cursor.getLong(3),
                        crc32Hex = cursor.getString(4),
                        localPath = cursor.getString(5),
                        syncedAtMs = cursor.getLong(6)
                    )
                )
            }
        }
        return out
    }

    companion object {
        private const val DB_NAME = "bionz_wifisync.db"
        private const val DB_VERSION = 1
    }
}
