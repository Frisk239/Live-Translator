package com.livetranslator.android

import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlinx.coroutines.flow.MutableStateFlow
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject

/* 端到端验证用：注册账号 → 连托管听译 WS → 推抓到的 PCM → 收字幕事件。
   模拟器里 10.0.2.2 即宿主机 loopback（Android 官方别名），开发服务听 127.0.0.1 即可达。 */

object HostedLink {
    data class LinkState(
        val status: String = "未连",
        val caption: String = "",
        val events: Int = 0,
    )

    val state = MutableStateFlow(LinkState())

    @Volatile private var ws: WebSocket? = null
    @Volatile private var feeding = false
    private val client = OkHttpClient()

    fun start(baseHttp: String) {
        if (ws != null) return
        setState { it.copy(status = "注册中…") }
        val json = "application/json; charset=utf-8".toMediaType()
        val body = """{"email":"spike-emu@t.c","password":"secret12"}""".toRequestBody(json)
        client.newCall(Request.Builder().url("$baseHttp/account/register").post(body).build())
            .enqueue(object : okhttp3.Callback {
                override fun onFailure(call: okhttp3.Call, e: java.io.IOException) {
                    setState { it.copy(status = "注册失败：${e.message}") }
                }

                override fun onResponse(call: okhttp3.Call, response: Response) {
                    val text = response.body?.string() ?: ""
                    response.close()
                    val token = try {
                        JSONObject(text).getString("token")
                    } catch (e: Exception) {
                        null
                    }
                    if (token == null) {
                        setState { it.copy(status = "没拿到 token：$text") }
                        return
                    }
                    openWs(baseHttp.replace("http", "ws") + "/listen", token)
                }
            })
    }

    private fun openWs(url: String, token: String) {
        setState { it.copy(status = "连 WS…") }
        val req = Request.Builder().url(url).build()
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send("""{"type":"auth","token":"$token"}""")
                webSocket.send("""{"type":"start","source":"spike-emu","translate":"ct2"}""")
                feeding = true
                setState { it.copy(status = "在推（等字幕…）") }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val obj = try {
                    JSONObject(text)
                } catch (e: Exception) {
                    return
                }
                when (obj.optString("type")) {
                    "draft", "final" -> {
                        val kind = if (obj.optString("type") == "final") "定稿" else "草稿"
                        val orig = obj.optString("orig")
                        val trans = obj.optString("trans")
                        setState {
                            it.copy(
                                caption = "[$kind] $orig\n$trans",
                                events = it.events + 1,
                            )
                        }
                    }
                    "notice" -> setState { it.copy(status = "提示 ${obj.optString("kind")}") }
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                feeding = false
                setState { it.copy(status = "WS 断：${t.message}") }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                feeding = false
                setState { it.copy(status = "已关") }
            }
        })
    }

    fun stop() {
        feeding = false
        ws?.close(1000, "stop")
        ws = null
        setState { LinkState(status = "未连") }
    }

    /** PCM16 → f32le（缝格式），feeding 未开时直接丢弃。 */
    fun sendPcm(buf: ShortArray, n: Int) {
        if (!feeding) return
        val w = ws ?: return
        val bb = ByteBuffer.allocate(n * 4).order(ByteOrder.LITTLE_ENDIAN)
        for (i in 0 until n) bb.putFloat(buf[i] / 32768f)
        w.send(bb.array().toByteString())
    }

    private fun setState(mut: (LinkState) -> LinkState) {
        state.value = mut(state.value)
    }
}
