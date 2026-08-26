package com.livetranslator.android.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.livetranslator.android.account.AccountRepo
import com.livetranslator.android.account.AccountResult
import com.livetranslator.android.account.TokenStore
import kotlinx.coroutines.launch

/** 个人中心（spec：第一版只有改密码 + 退出登录；退出会停掉本机开的听）。 */
@Composable
fun ProfileScreen(
    email: String,
    repo: AccountRepo,
    tokenStore: TokenStore,
    onBack: () -> Unit,
    onLoggedOut: () -> Unit,
) {
    var old by remember { mutableStateOf("") }
    var new by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    Surface(Modifier.fillMaxSize()) {
        Column(
            Modifier.fillMaxWidth().padding(24.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            TextButton(onClick = onBack) { Text("← 返回") }
            Text("个人中心", style = MaterialTheme.typography.headlineSmall)
            Text(email, style = MaterialTheme.typography.bodyMedium)
            OutlinedTextField(value = old, onValueChange = { old = it }, label = { Text("旧密码") }, singleLine = true, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            OutlinedTextField(value = new, onValueChange = { new = it }, label = { Text("新密码") }, singleLine = true, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            msg?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
            Button(onClick = {
                scope.launch {
                    val token = tokenStore.load()?.second ?: return@launch onLoggedOut()
                    busy = true
                    when (val r = repo.changePassword(token, old, new)) {
                        is AccountResult.Ok -> {
                            tokenStore.save(r.email, r.token, true)
                            msg = "密码已改，其它电脑的登录已作废。"
                        }
                        is AccountResult.BadCredentials -> msg = r.message
                        is AccountResult.Throttled -> msg = r.message
                        is AccountResult.NetworkError -> msg = r.message
                        else -> msg = "服务异常，稍后再试"
                    }
                    busy = false
                }
            }, enabled = !busy) { Text("改密码") }

            TextButton(onClick = {
                scope.launch {
                    tokenStore.load()?.second?.let { repo.logout(it) }
                    tokenStore.clear()
                    onLoggedOut()
                }
            }) { Text("退出登录") }
        }
    }
}
