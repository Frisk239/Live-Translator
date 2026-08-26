package com.livetranslator.android.account

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/** 账号缝：壳 ↔ 托管服务的 HTTPS JSON（注册/登录/会话/改密码/退出）。
 *  错误文案对齐 spec：重复邮箱→去登录、错密码→密码不对、试得勤→暂拒。 */

sealed interface AccountResult {
    data class Registered(val email: String, val token: String) : AccountResult
    data class LoggedIn(val email: String, val token: String) : AccountResult
    data class Ok(val email: String, val token: String) : AccountResult
    data class EmailTaken(val message: String) : AccountResult
    data class BadCredentials(val message: String) : AccountResult
    data class Throttled(val message: String) : AccountResult
    data class Unauthorized(val message: String) : AccountResult
    data class NetworkError(val message: String) : AccountResult
}

class AccountRepo(
    private val baseHttp: String,
    private val client: OkHttpClient = OkHttpClient(),
) {
    private val json = "application/json; charset=utf-8".toMediaType()

    suspend fun register(email: String, password: String, rememberMe: Boolean): AccountResult =
        call("/account/register", JSONObject().put("email", email).put("password", password).put("rememberMe", rememberMe)) { body, _ ->
            AccountResult.Registered(body.getString("email"), body.getString("token"))
        }

    suspend fun login(email: String, password: String, rememberMe: Boolean): AccountResult =
        call("/account/login", JSONObject().put("email", email).put("password", password).put("rememberMe", rememberMe)) { body, _ ->
            AccountResult.LoggedIn(body.getString("email"), body.getString("token"))
        }

    suspend fun session(token: String): AccountResult =
        call("/account/session", JSONObject().put("token", token)) { body, _ ->
            AccountResult.Ok(body.getString("email"), token)
        }

    suspend fun changePassword(token: String, oldPassword: String, newPassword: String): AccountResult =
        call(
            "/account/password",
            JSONObject().put("token", token).put("oldPassword", oldPassword).put("newPassword", newPassword),
        ) { body, _ -> AccountResult.Ok(body.getString("email"), body.getString("token")) }

    suspend fun logout(token: String): AccountResult =
        call("/account/logout", JSONObject().put("token", token)) { _, _ ->
            AccountResult.Ok("", token)
        }

    private suspend fun call(
        path: String,
        payload: JSONObject,
        onOk: (JSONObject, Int) -> AccountResult,
    ): AccountResult = withContext(Dispatchers.IO) {
        val req = try {
            Request.Builder().url(baseHttp + path).post(payload.toString().toRequestBody(json)).build()
        } catch (_: IllegalArgumentException) {
            return@withContext AccountResult.NetworkError("服务器地址不可用")
        }
        val resp = try {
            client.newCall(req).execute()
        } catch (e: java.io.IOException) {
            return@withContext AccountResult.NetworkError(e.message ?: "网络不通")
        }
        resp.use {
            val text = it.body?.string() ?: ""
            val body = try {
                JSONObject(text)
            } catch (_: Exception) {
                JSONObject()
            }
            val message = body.optString("error", "")
            when (it.code) {
                200 -> onOk(body, it.code)
                401 -> AccountResult.BadCredentials(message.ifEmpty { "邮箱或密码不对" })
                409 -> AccountResult.EmailTaken(message.ifEmpty { "这个邮箱已经有账号，去登录" })
                429 -> AccountResult.Throttled(message.ifEmpty { "试得太勤了，过几分钟再来。" })
                else -> AccountResult.NetworkError(message.ifEmpty { "服务不可用（${it.code}）" })
            }
        }
    }
}
