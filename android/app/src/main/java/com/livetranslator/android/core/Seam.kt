package com.livetranslator.android.core

import org.json.JSONObject

/** 壳 ↔ 托管听译缝（与桌面同协议：auth/start/stop 文本帧 + f32le·mono·16k PCM 二进制帧，
 *  回 draft/final/notice；ADR 0009 / android-spec）。 */
sealed interface ListenEvent {
    data class Draft(val orig: String, val trans: String, val seq: Int? = null) : ListenEvent
    data class Final(val orig: String, val trans: String, val seq: Int? = null) : ListenEvent
    data class Notice(val kind: NoticeKind) : ListenEvent
}

enum class NoticeKind {
    NO_SPEECH, NOT_LANG, NO_AUDIO, CRASHED, KICKED, FULL, AUTH;

    companion object {
        fun fromWire(s: String): NoticeKind? = entries.firstOrNull { it.name.lowercase() == s }
    }
}

object Seam {
    fun parseEvent(text: String): ListenEvent? {
        val obj = try {
            JSONObject(text)
        } catch (_: Exception) {
            return null
        }
        val seq = if (obj.has("seq")) obj.optInt("seq").takeIf { it > 0 } else null
        return when (obj.optString("type")) {
            "draft" -> ListenEvent.Draft(obj.optString("orig"), obj.optString("trans"), seq)
            "final" -> ListenEvent.Final(obj.optString("orig"), obj.optString("trans"), seq)
            "notice" -> NoticeKind.fromWire(obj.optString("kind"))?.let { ListenEvent.Notice(it) }
            else -> null
        }
    }

    fun authCommand(token: String): String =
        JSONObject().put("type", "auth").put("token", token).toString()

    fun startCommand(source: String): String =
        JSONObject().put("type", "start").put("source", source).put("translate", "ct2").toString()

    fun stopCommand(): String = JSONObject().put("type", "stop").toString()

    /** AudioPlaybackCapture 给 PCM16；缝要 f32le。 */
    fun pcm16ToF32le(buf: ShortArray, n: Int): ByteArray {
        val out = ByteArray(n * 4)
        var i = 0
        while (i < n) {
            val v = buf[i] / 32768f
            val bits = v.toRawBits()
            out[i * 4] = (bits and 0xFF).toByte()
            out[i * 4 + 1] = ((bits ushr 8) and 0xFF).toByte()
            out[i * 4 + 2] = ((bits ushr 16) and 0xFF).toByte()
            out[i * 4 + 3] = ((bits ushr 24) and 0xFF).toByte()
            i++
        }
        return out
    }
}
