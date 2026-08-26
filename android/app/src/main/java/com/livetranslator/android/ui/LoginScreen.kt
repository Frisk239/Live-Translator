package com.livetranslator.android.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.livetranslator.android.account.AccountResult
import com.livetranslator.android.account.AccountRepo
import com.livetranslator.android.account.TokenStore
import kotlinx.coroutines.launch

/** 登录/注册同一屏（spec：手机只做托管，登录才解锁；不出现找回密码）。 */
@Composable
fun LoginScreen(
    repo: AccountRepo,
    tokenStore: TokenStore,
    onLoggedIn: (email: String) -> Unit,
) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var rememberMe by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var notice by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    suspend fun submit(register: Boolean) {
        if (email.isBlank() || password.isBlank()) {
            error = "填邮箱和密码"
            return
        }
        busy = true
        error = null
        notice = null
        val r = if (register) repo.register(email.trim(), password, rememberMe) else repo.login(email.trim(), password, rememberMe)
        busy = false
        when (r) {
            is AccountResult.Registered, is AccountResult.LoggedIn -> {
                val (m, t) = when (r) {
                    is AccountResult.Registered -> r.email to r.token
                    is AccountResult.LoggedIn -> r.email to r.token
                    else -> return
                }
                tokenStore.save(m, t, rememberMe)
                onLoggedIn(m)
            }
            is AccountResult.EmailTaken -> {
                error = r.message
                notice = "已经有账号？直接登录。"
            }
            is AccountResult.BadCredentials -> error = r.message
            is AccountResult.Throttled -> error = r.message
            is AccountResult.NetworkError -> error = r.message
            else -> error = "服务异常，稍后再试"
        }
    }

    Surface(Modifier.fillMaxSize()) {
        Column(
            Modifier.fillMaxWidth().padding(24.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("直播同传", style = MaterialTheme.typography.headlineMedium)
            Text("登录后用托管听译：手机只抓音、出字幕", style = MaterialTheme.typography.bodySmall)
            OutlinedTextField(value = email, onValueChange = { email = it }, label = { Text("邮箱") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("密码") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth(),
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = rememberMe, onCheckedChange = { rememberMe = it })
                Text("记住我（这台手机保持登录）", style = MaterialTheme.typography.bodySmall)
            }
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            notice?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(onClick = { scope.launch { submit(register = true) } }, enabled = !busy) {
                    if (busy) CircularProgressIndicator(Modifier.padding(4.dp)) else Text("注册并登录")
                }
                TextButton(onClick = { scope.launch { submit(register = false) } }, enabled = !busy) { Text("登录") }
            }
        }
    }
}
