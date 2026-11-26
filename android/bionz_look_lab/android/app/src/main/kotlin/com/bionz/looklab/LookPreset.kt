package com.bionz.looklab

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

data class FilmStockInfo(
    val id: String,
    val name: String,
    val subtitle: String,
    val defaultHalation: Int,
    val defaultGrain: Int,
    val defaultDirCouplers: Int
) {
    fun toMap(): Map<String, Any> = mapOf(
        "id" to id,
        "name" to name,
        "subtitle" to subtitle,
        "defaultHalation" to defaultHalation,
        "defaultGrain" to defaultGrain,
        "defaultDirCouplers" to defaultDirCouplers
    )
}

data class PrintPaperInfo(
    val id: String,
    val name: String,
    val subtitle: String
) {
    fun toMap(): Map<String, Any> = mapOf(
        "id" to id,
        "name" to name,
        "subtitle" to subtitle
    )
}

data class LookPreset(
    val id: String,
    val name: String,
    val stockId: String = "portra_400",
    val paperId: String = "endura",
    val builtIn: Boolean = false,
    val exposure: Int = 0,
    val contrast: Int = 0,
    val structure: Int = 0,
    val highlights: Int = 0,
    val shadows: Int = 0,
    val warmth: Int = 0,
    val tint: Int = 0,
    val saturation: Int = 0,
    val dirCouplers: Int = 50,
    val halation: Int = 20,
    val grain: Int = 25,
    val fade: Int = 0,
    val vignette: Int = 0
) {
    fun slug(): String {
        val clean = name.lowercase().replace(Regex("[^a-z0-9]+"), "_").trim('_')
        return if (clean.isNotEmpty()) clean.take(28) else "spektrafilm"
    }

    fun toMap(): Map<String, Any> = mapOf(
        "id" to id,
        "name" to name,
        "stockId" to stockId,
        "paperId" to paperId,
        "builtIn" to builtIn,
        "exposure" to exposure,
        "contrast" to contrast,
        "structure" to structure,
        "highlights" to highlights,
        "shadows" to shadows,
        "warmth" to warmth,
        "tint" to tint,
        "saturation" to saturation,
        "dirCouplers" to dirCouplers,
        "halation" to halation,
        "grain" to grain,
        "fade" to fade,
        "vignette" to vignette
    )

    fun toJson(): JSONObject = JSONObject().apply {
        put("id", id)
        put("name", name)
        put("stockId", stockId)
        put("paperId", paperId)
        put("builtIn", builtIn)
        put("exposure", exposure)
        put("contrast", contrast)
        put("structure", structure)
        put("highlights", highlights)
        put("shadows", shadows)
        put("warmth", warmth)
        put("tint", tint)
        put("saturation", saturation)
        put("dirCouplers", dirCouplers)
        put("halation", halation)
        put("grain", grain)
        put("fade", fade)
        put("vignette", vignette)
    }

    companion object {
        private fun clampBi(v: Int): Int = v.coerceIn(-100, 100)
        private fun clampUni(v: Int): Int = v.coerceIn(0, 100)

        fun fromMap(map: Map<*, *>): LookPreset {
            fun intVal(key: String, def: Int): Int =
                (map[key] as? Number)?.toInt() ?: def

            return LookPreset(
                id = (map["id"] as? String)?.takeIf { it.isNotBlank() }
                    ?: "custom_${System.currentTimeMillis()}",
                name = (map["name"] as? String)?.takeIf { it.isNotBlank() }
                    ?: "Custom Film",
                stockId = (map["stockId"] as? String) ?: "portra_400",
                paperId = (map["paperId"] as? String) ?: "endura",
                builtIn = (map["builtIn"] as? Boolean) ?: false,
                exposure = clampBi(intVal("exposure", 0)),
                contrast = clampBi(intVal("contrast", 0)),
                structure = clampBi(intVal("structure", 0)),
                highlights = clampBi(intVal("highlights", 0)),
                shadows = clampBi(intVal("shadows", 0)),
                warmth = clampBi(intVal("warmth", 0)),
                tint = clampBi(intVal("tint", 0)),
                saturation = clampBi(intVal("saturation", 0)),
                dirCouplers = clampUni(intVal("dirCouplers", 50)),
                halation = clampUni(intVal("halation", 20)),
                grain = clampUni(intVal("grain", 25)),
                fade = clampUni(intVal("fade", 0)),
                vignette = clampUni(intVal("vignette", 0))
            )
        }

        fun fromJson(obj: JSONObject): LookPreset = LookPreset(
            id = obj.optString("id", "custom"),
            name = obj.optString("name", "Custom Film"),
            stockId = obj.optString("stockId", "portra_400"),
            paperId = obj.optString("paperId", "endura"),
            builtIn = obj.optBoolean("builtIn", false),
            exposure = clampBi(obj.optInt("exposure", 0)),
            contrast = clampBi(obj.optInt("contrast", 0)),
            structure = clampBi(obj.optInt("structure", 0)),
            highlights = clampBi(obj.optInt("highlights", 0)),
            shadows = clampBi(obj.optInt("shadows", 0)),
            warmth = clampBi(obj.optInt("warmth", 0)),
            tint = clampBi(obj.optInt("tint", 0)),
            saturation = clampBi(obj.optInt("saturation", 0)),
            dirCouplers = clampUni(obj.optInt("dirCouplers", 50)),
            halation = clampUni(obj.optInt("halation", 20)),
            grain = clampUni(obj.optInt("grain", 25)),
            fade = clampUni(obj.optInt("fade", 0)),
            vignette = clampUni(obj.optInt("vignette", 0))
        )
    }
}

class LookPresetStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(
        PREFS_NAME,
        Context.MODE_PRIVATE
    )

    @Synchronized
    fun listPresets(): List<LookPreset> {
        val custom = loadCustomPresets()
        val byId = LinkedHashMap<String, LookPreset>()
        for (p in BUILTIN_PRESETS) byId[p.id] = p
        for (p in custom) byId[p.id] = p
        return byId.values.toList()
    }

    @Synchronized
    fun getPresetById(id: String?): LookPreset? {
        if (id.isNullOrBlank()) return null
        return listPresets().firstOrNull { it.id == id }
    }

    @Synchronized
    fun savePreset(raw: LookPreset): LookPreset {
        val cleanId = raw.id.ifBlank { "custom_${System.currentTimeMillis()}" }
        val toStore = raw.copy(id = cleanId, builtIn = false)
        val existing = loadCustomPresets().filterNot { it.id == cleanId }.toMutableList()
        existing.add(0, toStore)
        persistCustomPresets(existing)
        return toStore
    }

    @Synchronized
    fun deletePreset(id: String): Boolean {
        if (BUILTIN_PRESETS.any { it.id == id }) return false
        val current = loadCustomPresets()
        val next = current.filterNot { it.id == id }
        if (next.size == current.size) return false
        persistCustomPresets(next)
        if (getAutoApplyPresetId() == id) {
            setAutoApplyPresetId(null)
        }
        return true
    }

    @Synchronized
    fun getAutoApplyPresetId(): String? {
        return prefs.getString(KEY_AUTO_PRESET_ID, null)?.takeIf { it.isNotBlank() }
    }

    @Synchronized
    fun setAutoApplyPresetId(id: String?): String? {
        val validId = if (id.isNullOrBlank() || getPresetById(id) == null) null else id
        prefs.edit().putString(KEY_AUTO_PRESET_ID, validId).apply()
        return validId
    }

    @Synchronized
    fun getAutoApplyPreset(): LookPreset? = getPresetById(getAutoApplyPresetId())

    private fun loadCustomPresets(): List<LookPreset> {
        val raw = prefs.getString(KEY_CUSTOM_PRESETS, null) ?: return emptyList()
        return try {
            val arr = JSONArray(raw)
            val list = ArrayList<LookPreset>(arr.length())
            for (i in 0 until arr.length()) {
                val obj = arr.optJSONObject(i) ?: continue
                list.add(LookPreset.fromJson(obj))
            }
            list
        } catch (_: Throwable) {
            emptyList()
        }
    }

    private fun persistCustomPresets(list: List<LookPreset>) {
        val arr = JSONArray()
        for (p in list) {
            arr.put(p.toJson())
        }
        prefs.edit().putString(KEY_CUSTOM_PRESETS, arr.toString()).apply()
    }

    companion object {
        private const val PREFS_NAME = "bionz_looklab_presets"
        private const val KEY_CUSTOM_PRESETS = "custom_presets_json"
        private const val KEY_AUTO_PRESET_ID = "auto_apply_preset_id"

        val BUILTIN_PRESETS: List<LookPreset> = listOf(
            LookPreset(
                id = "builtin_portra_400",
                name = "Kodak Portra 400",
                stockId = "portra_400",
                paperId = "endura",
                builtIn = true,
                contrast = 6,
                structure = 8,
                highlights = -14,
                shadows = 14,
                warmth = 10,
                dirCouplers = 55,
                halation = 18,
                grain = 24,
                fade = 6
            ),
            LookPreset(
                id = "builtin_gold_200",
                name = "Kodak Gold 200",
                stockId = "gold_200",
                paperId = "endura",
                builtIn = true,
                contrast = 14,
                structure = 12,
                highlights = -10,
                shadows = 6,
                warmth = 18,
                saturation = 12,
                dirCouplers = 62,
                halation = 26,
                grain = 34,
                fade = 4
            ),
            LookPreset(
                id = "builtin_cinestill_800t",
                name = "CineStill 800T",
                stockId = "cinestill_800t",
                paperId = "crystal_archive",
                builtIn = true,
                contrast = 16,
                structure = 6,
                highlights = -18,
                shadows = 12,
                warmth = -16,
                tint = -6,
                dirCouplers = 52,
                halation = 72,
                grain = 42,
                fade = 10
            ),
            LookPreset(
                id = "builtin_pro_400h",
                name = "Fuji Pro 400H",
                stockId = "pro_400h",
                paperId = "crystal_archive",
                builtIn = true,
                contrast = 4,
                structure = 4,
                highlights = -18,
                shadows = 20,
                warmth = -6,
                tint = -10,
                dirCouplers = 48,
                halation = 14,
                grain = 22,
                fade = 12
            ),
            LookPreset(
                id = "builtin_hp5_400",
                name = "Ilford HP5 Plus",
                stockId = "hp5_400",
                paperId = "warm_silver",
                builtIn = true,
                contrast = 26,
                structure = 28,
                highlights = -14,
                shadows = -8,
                dirCouplers = 0,
                halation = 12,
                grain = 48,
                fade = 14,
                vignette = 16
            )
        )
    }
}
