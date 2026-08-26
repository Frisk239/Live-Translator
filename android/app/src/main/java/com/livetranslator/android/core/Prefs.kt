package com.livetranslator.android.core

import android.content.Context

/** 观众的选择下次打开还在（CONTEXT「控制面板」）：音源、字幕模式、字幕样式。
 *  接口化以便 JVM 单测；实现是 SharedPreferences 的薄封装。 */

enum class CaptionMode(val wire: String) { ORIG("orig"), BILINGUAL("bilingual"), TRANS("trans");
    companion object { val DEFAULT = BILINGUAL; fun fromWire(s: String?): CaptionMode = entries.firstOrNull { it.wire == s } ?: DEFAULT }
}

enum class SubColor { WHITE, YELLOW, CYAN }
enum class SubSize { S, M, L }

data class OverlayStyle(
    val color: SubColor = SubColor.WHITE,
    val size: SubSize = SubSize.M,
    val plateOn: Boolean = false,
)

data class Prefs(
    val mode: CaptionMode = CaptionMode.DEFAULT,
    val style: OverlayStyle = OverlayStyle(),
    val lastSourceLabel: String? = null,
    /** 悬浮字幕位置（像素）；null = 默认（屏幕靠下居中） */
    val overlayX: Int? = null,
    val overlayY: Int? = null,
)

interface PrefsStore {
    fun load(): Prefs
    fun save(p: Prefs)
}

class SharedPrefsStore(context: Context) : PrefsStore {
    private val sp = context.getSharedPreferences("prefs", Context.MODE_PRIVATE)
    override fun load(): Prefs = Prefs(
        mode = CaptionMode.fromWire(sp.getString("mode", null)),
        style = OverlayStyle(
            color = sp.getString("color", null)?.let { c -> SubColor.entries.firstOrNull { it.name == c } } ?: SubColor.WHITE,
            size = sp.getString("size", null)?.let { s -> SubSize.entries.firstOrNull { it.name == s } } ?: SubSize.M,
            plateOn = sp.getBoolean("plate", false),
        ),
        lastSourceLabel = sp.getString("source", null),
        overlayX = if (sp.contains("ox")) sp.getInt("ox", 0) else null,
        overlayY = if (sp.contains("oy")) sp.getInt("oy", 0) else null,
    )

    override fun save(p: Prefs) {
        sp.edit()
            .putString("mode", p.mode.wire)
            .putString("color", p.style.color.name)
            .putString("size", p.style.size.name)
            .putBoolean("plate", p.style.plateOn)
            .putString("source", p.lastSourceLabel)
            .apply {
                if (p.overlayX != null) putInt("ox", p.overlayX) else remove("ox")
                if (p.overlayY != null) putInt("oy", p.overlayY) else remove("oy")
            }
            .apply()
    }
}
