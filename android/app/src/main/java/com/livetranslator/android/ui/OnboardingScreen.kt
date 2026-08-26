package com.livetranslator.android.ui

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.compose.ui.unit.dp
import com.livetranslator.android.MainActivity

/** 首启集中申请（android-spec）：一页讲清三件事 → 录音+通知（系统弹窗）→ 悬浮窗（跳设置，
 *  返回自动检测）。授过永不再问；投屏授权每会话一次，留在开听时。 */

@Composable
fun OnboardingScreen(onDone: () -> Unit) {
    val context = LocalContext.current
    var sawNotif by remember { mutableStateOf(false) }
    var sawOverlay by remember { mutableStateOf(Settings.canDrawOverlays(context)) }

    val runtimePerms = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        sawNotif = true
    }

    // 从系统设置返回时自动检测悬浮窗权限
    LifecycleResumeEffect(Unit) {
        sawOverlay = Settings.canDrawOverlays(context)
        onPauseOrDispose { }
    }

    fun requestRuntime() {
        val need = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) need.add(Manifest.permission.POST_NOTIFICATIONS)
        runtimePerms.launch(need.toTypedArray())
    }

    Surface(Modifier.fillMaxSize()) {
        Column(
            Modifier.padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text("直播同传", style = MaterialTheme.typography.headlineMedium)
            Text("看外语直播，中文字幕叠在直播画面上。开始前需要三项授权，只问这一次：", style = MaterialTheme.typography.bodyMedium)
            PermissionRow("① 录音", "用来抓手机里正在播放的声音（不是麦克风）", sawNotif)
            PermissionRow("② 通知", "「正在听」的状态常驻通知栏，可随时停止", sawNotif)
            PermissionRow("③ 显示在其他应用上层", "字幕悬浮在直播画面上", sawOverlay)

            when {
                !sawNotif -> Button(onClick = { requestRuntime() }) { Text("授权录音与通知") }
                !sawOverlay -> Button(onClick = {
                    runCatching {
                        context.startActivity(
                            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${context.packageName}"))
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                        )
                    }
                }) { Text("去设置打开悬浮窗权限") }
                else -> {
                    // onResume 回来自动检测；这里也给手动入口
                    Button(onClick = onDone) { Text("完成，开始使用") }
                }
            }
            if (sawNotif && !sawOverlay) {
                Text("从设置返回后会自动继续。", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun PermissionRow(title: String, why: String, granted: Boolean) {
    Column {
        Text(if (granted) "$title ✅" else title, style = MaterialTheme.typography.titleSmall)
        Text(why, style = MaterialTheme.typography.bodySmall)
    }
}

/** 从设置返回时由 MainActivity.onResume 调：悬浮窗权限到手即视为引导完成。 */
fun overlayGranted(context: android.content.Context): Boolean = Settings.canDrawOverlays(context)
