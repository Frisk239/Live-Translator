package com.livetranslator.android.listen

import com.livetranslator.android.core.NoticeKind
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** 缝客户端行为：连上即发 auth/start、事件透传、顶号终态即停不重试、断线自动再开。 */
class HostedLinkTest {

    private lateinit var server: MockWebServer
    private lateinit var client: okhttp3.OkHttpClient
    private val events = LinkedBlockingQueue<String>()
    private val serverGot = LinkedBlockingQueue<String>()
    private val connects = AtomicInteger(0)

    private inner class Recorder : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: okhttp3.Response) {
            connects.incrementAndGet()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            serverGot += text
        }
    }

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = okhttp3.OkHttpClient()
    }

    @After
    fun tearDown() {
        client.dispatcher.executorService.shutdown()
        client.connectionPool.evictAll()
        // mock 端不回应 close 帧时 shutdown 会超时——连接由进程退出兜底，吞掉即可
        runCatching { server.shutdown() }
    }

    private fun listener(
        onTerminal: (NoticeKind) -> Unit = {},
        onExhausted: () -> Unit = {},
        onConnected: () -> Unit = {},
    ) = object : HostedLink.Listener {
        override fun onEvent(text: String) { events += text }
        override fun onConnected() = onConnected()
        override fun onTerminal(kind: NoticeKind, message: String) = onTerminal(kind)
        override fun onExhausted(message: String) = onExhausted()
    }

    @Test
    fun `连上即发 auth 与 start 事件原样透传`() {
        server.enqueue(
            MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: okhttp3.Response) {
                    connects.incrementAndGet()
                    webSocket.send("""{"type":"draft","orig":"so we","trans":"我们"}""")
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    serverGot += text
                }
            }),
        )
        val link = HostedLink(server.url("/listen").toString(), "tok", "全部可抓的声音", listener(), client)
        link.start()

        val auth = serverGot.poll(5, TimeUnit.SECONDS)
        assertEquals("""{"type":"auth","token":"tok"}""", auth)
        val start = serverGot.poll(5, TimeUnit.SECONDS)
        assertTrue(start!!.contains(""""type":"start"""") && start.contains(""""source":"全部可抓的声音""""))
        assertEquals("""{"type":"draft","orig":"so we","trans":"我们"}""", events.poll(5, TimeUnit.SECONDS))
        link.stop()
    }

    @Test
    fun `顶号是终态 停止且不再重连`() {
        val terminal = CountDownLatch(1)
        server.enqueue(
            MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: okhttp3.Response) {
                    connects.incrementAndGet()
                    webSocket.send("""{"type":"notice","kind":"kicked"}""")
                }
            }),
        )
        val link = HostedLink(
            server.url("/listen").toString(), "tok", "s",
            listener(onTerminal = { terminal.countDown() }),
            client,
            sleeper = { /* 快进退避 */ },
        )
        link.start()
        assertTrue(terminal.await(5, TimeUnit.SECONDS))
        Thread.sleep(300) // 若误重试会再发起请求
        assertEquals("终态后不再发连接", 1, server.requestCount)
        link.stop()
    }

    @Test
    fun `断线自动再开一路 重新 auth start`() {
        // 第一路：服务端主动关 → 触发重试；第二路：正常升级（用于数到 2 次连接）
        server.enqueue(
            MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: okhttp3.Response) {
                    connects.incrementAndGet()
                    webSocket.close(1000, "flash cut")
                }
            }),
        )
        server.enqueue(MockResponse().withWebSocketUpgrade(Recorder()))
        val link = HostedLink(
            server.url("/listen").toString(), "tok", "s",
            listener(),
            client,
            sleeper = { /* 快进退避 */ },
        )
        link.start()
        val deadline = System.currentTimeMillis() + 5000
        while (connects.get() < 2 && System.currentTimeMillis() < deadline) Thread.sleep(50)
        assertEquals("闪断后自动再开一路", 2, connects.get())
        // 第二路也重新走了 auth（首条服务端收包）
        assertTrue(serverGot.poll(5, TimeUnit.SECONDS)!!.contains("auth"))
        link.stop()
    }
}
