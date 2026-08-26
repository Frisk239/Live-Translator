package com.livetranslator.android.account

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class AccountRepoTest {
    private lateinit var server: MockWebServer
    private lateinit var repo: AccountRepo

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        repo = AccountRepo(server.url("/").toString().trimEnd('/'))
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `注册成功即登录 带回 token`() = runTest {
        server.enqueue(MockResponse().setBody("""{"email":"a@b.c","token":"t1"}"""))
        val r = repo.register("a@b.c", "secret12", rememberMe = true)
        assertEquals(AccountResult.Registered("a@b.c", "t1"), r)
        val req = server.takeRequest()
        assertEquals("/account/register", req.path)
        assertTrue(req.body.readUtf8().contains(""""email":"a@b.c""""))
    }

    @Test
    fun `重复邮箱 409 给去登录的文案`() = runTest {
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"error":"这个邮箱已经有账号，去登录"}"""))
        val r = repo.register("a@b.c", "x", rememberMe = false)
        assertTrue(r is AccountResult.EmailTaken)
        assertTrue((r as AccountResult.EmailTaken).message.contains("登录"))
    }

    @Test
    fun `错密码 401 与登录失效 401 同路`() = runTest {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"error":"邮箱或密码不对"}"""))
        val login = repo.login("a@b.c", "bad", rememberMe = false)
        assertTrue(login is AccountResult.BadCredentials)

        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"error":"登录已失效，请重新登录"}"""))
        val session = repo.session("stale")
        assertTrue(session is AccountResult.BadCredentials)
    }

    @Test
    fun `试得太勤 429 暂拒不锁账号`() = runTest {
        server.enqueue(MockResponse().setResponseCode(429).setBody("""{"error":"试得太勤了，过几分钟再来。"}"""))
        val r = repo.login("a@b.c", "secret12", rememberMe = false)
        assertTrue(r is AccountResult.Throttled)
    }

    @Test
    fun `改密码带回换发的新 token`() = runTest {
        server.enqueue(MockResponse().setBody("""{"email":"a@b.c","token":"t2"}"""))
        val r = repo.changePassword("t1", "old", "new")
        assertEquals(AccountResult.Ok("a@b.c", "t2"), r)
    }

    @Test
    fun `服务不通归网络错误`() = runTest {
        val dead = MockWebServer()
        dead.start()
        val url = dead.url("/").toString().trimEnd('/')
        dead.shutdown()
        val r = AccountRepo(url).login("a@b.c", "x", rememberMe = false)
        assertTrue(r is AccountResult.NetworkError)
    }
}

class TokenStoreTest {
    @Test
    fun `勾记住我 杀进程后仍登录`() {
        val s = MemoryTokenStore()
        s.save("a@b.c", "t1", rememberMe = true)
        s.killProcess()
        assertEquals("a@b.c" to "t1", s.load())
    }

    @Test
    fun `不勾记住我 杀进程即登出`() {
        val s = MemoryTokenStore()
        s.save("a@b.c", "t1", rememberMe = false)
        s.killProcess()
        assertNull(s.load())
    }

    @Test
    fun `改密码覆盖旧凭据 退出登录清干净`() {
        val s = MemoryTokenStore()
        s.save("a@b.c", "t1", rememberMe = true)
        s.save("a@b.c", "t2", rememberMe = true)
        s.killProcess()
        assertEquals("a@b.c" to "t2", s.load())
        s.clear()
        s.killProcess()
        assertNull(s.load())
    }
}
