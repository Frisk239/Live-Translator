package com.livetranslator.android.listen

import com.livetranslator.android.core.NoticeKind
import com.livetranslator.android.core.Seam
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean

/** 托管听译缝的客户端：auth → start → 持续推 f32le PCM → 收 draft/final/notice。
 *  断线语义对齐桌面壳（ADR 0019/0020）：闪断自动再开一路（5 次退避，重开即重新 auth+start，
 *  同账号重开不占自己的满员名额由服务端保证）；重试尽 → 网断终态，不自动改任何东西。
 *  顶号 / 满员 / 登录失效是终态 notice：立即停、不再重试。 */

class HostedLink(
    private val wsUrl: String,
    private val token: String,
    private val source: String,
    private val listener: Listener,
    private val client: OkHttpClient = OkHttpClient(),
    /** 单测注入：重试退避等待（生产 Thread.sleep） */
    private val sleeper: (Long) -> Unit = { Thread.sleep(it) },
) {
    interface Listener {
        fun onEvent(text: String)
        fun onConnected()
        fun onTerminal(kind: NoticeKind, message: String)
        fun onExhausted(message: String)
    }

    private var ws: WebSocket? = null
    private val feeding = AtomicBoolean(false)
    private var retries = 0
    private var closedByUs = false

    fun start() {
        closedByUs = false
        open()
    }

    fun stop() {
        closedByUs = true
        feeding.set(false)
        ws?.close(1000, "stop")
        ws = null
    }

    /** PCM16 → f32le 推流；未连接成功时直接丢弃（静音期丢帧无害，连接后从头推当前流）。 */
    fun sendPcm(buf: ShortArray, n: Int) {
        if (!feeding.get()) return
        ws?.send(Seam.pcm16ToF32le(buf, n).toByteString())
    }

    private fun open() {
        val req = try {
            Request.Builder().url(wsUrl).build()
        } catch (_: IllegalArgumentException) {
            listener.onExhausted("服务器地址不可用")
            return
        }
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(Seam.authCommand(token))
                webSocket.send(Seam.startCommand(source))
                feeding.set(true)
                retries = 0
                listener.onConnected()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val obj = try {
                    JSONObject(text)
                } catch (_: Exception) {
                    return
                }
                if (obj.optString("type") == "notice") {
                    val kind = NoticeKind.fromWire(obj.optString("kind"))
                    if (kind == NoticeKind.KICKED || kind == NoticeKind.FULL || kind == NoticeKind.AUTH) {
                        closedByUs = true // 终态：不再重试
                        feeding.set(false)
                        listener.onTerminal(kind, "")
                        return
                    }
                }
                listener.onEvent(text)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                feeding.set(false)
                if (closedByUs) return
                retryOrFail()
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                // 服务端主动关闭走这里（OkHttp 不自动回应），回个 close 再按断线处理
                feeding.set(false)
                webSocket.close(1000, null)
                if (!closedByUs) retryOrFail()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                feeding.set(false)
                if (!closedByUs) retryOrFail()
            }
        })
    }

    private fun retryOrFail() {
        if (retries >= MAX_RETRIES) {
            listener.onExhausted("网一直连不上，托管停了。等网好了再按开听。")
            return
        }
        retries++
        val backoffMs = RETRY_BASE_MS shl (retries - 1) // 1s,2s,4s,8s,8s ≈ 桌面 5 次量级
        Thread {
            try {
                sleeper(backoffMs)
            } catch (_: InterruptedException) {
                return@Thread
            }
            if (!closedByUs) open()
        }.apply { isDaemon = true }.start()
    }

    companion object {
        const val MAX_RETRIES = 5
        const val RETRY_BASE_MS = 1000L
    }
}
