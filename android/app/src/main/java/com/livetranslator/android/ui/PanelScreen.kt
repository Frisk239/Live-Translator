package com.livetranslator.android.ui

import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import android.media.projection.MediaProjectionManager
import com.livetranslator.android.BuildConfig
import com.livetranslator.android.R
import com.livetranslator.android.core.CaptionMode
import com.livetranslator.android.core.Prefs
import com.livetranslator.android.core.PrefsStore
import com.livetranslator.android.core.SubColor
import com.livetranslator.android.core.SubSize
import com.livetranslator.android.listen.ListenBus
import com.livetranslator.android.listen.ListenPhase
import com.livetranslator.android.listen.ListenService
import com.livetranslator.android.listen.Sources

/** 控制面板：选音源、字幕模式与样式、开听/停止；邮箱进个人中心。
 *  开听 = 检查悬浮窗权限 → 系统投屏授权（每会话一次，ADR 0037/0038）→ 落回面板出字幕。 */

@Composable
fun PanelScreen(
    email: String,
    prefsStore: PrefsStore,
    onOpenProfile: () -> Unit,
) {
    val context = LocalContext.current
    val prefs = remember { prefsStore.load() }
    var mode by remember { mutableStateOf(prefs.mode) }
    var color by remember { mutableStateOf(prefs.style.color) }
    var size by remember { mutableStateOf(prefs.style.size) }
    var plateOn by remember { mutableStateOf(prefs.style.plateOn) }
    var sources by remember { mutableStateOf<List<com.livetranslator.android.listen.AudioSource>>(emptyList()) }
    var picked by remember { mutableStateOf(prefs.lastSourceLabel ?: com.livetranslator.android.listen.AudioSource.ALL_LABEL) }
    var speechOn by remember { mutableStateOf(false) }
    val listen by ListenBus.state.collectAsState()

    var player by remember { mutableStateOf<MediaPlayer?>(null) }

    val projection = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val data = result.data
        if (result.resultCode == android.app.Activity.RESULT_OK && data != null) {
            val src = sources.firstOrNull { it.label == picked } ?: com.livetranslator.android.listen.AudioSource.ALL
            ListenService.begin(context, result.resultCode, data, src.label, src.uid ?: -1, includeSelf = src.includeSelfDebug)
        } else {
            ListenBus.update { it.copy(statusText = "授权取消了，没开听。") }
        }
    }

    fun startListening() {
        if (!android.provider.Settings.canDrawOverlays(context)) {
            ListenBus.update { it.copy(statusText = "还差「显示在其他应用上层」权限，去系统设置打开后再开听。") }
            return
        }
        val pm = context.getSystemService(MediaProjectionManager::class.java)
        projection.launch(pm.createScreenCaptureIntent())
    }

    Surface(Modifier.fillMaxSize()) {
        Column(
            Modifier.fillMaxWidth().padding(20.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("直播同传", style = MaterialTheme.typography.headlineSmall)
                TextButton(onClick = onOpenProfile) { Text(email) }
            }

            // 状态行
            Text(
                listen.statusText,
                style = MaterialTheme.typography.bodyMedium,
                color = when (listen.phase) {
                    ListenPhase.LISTENING -> MaterialTheme.colorScheme.primary
                    ListenPhase.FAILED -> MaterialTheme.colorScheme.error
                    ListenPhase.IDLE -> MaterialTheme.colorScheme.onSurface
                },
            )

            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button(
                    onClick = { startListening() },
                    enabled = listen.phase != ListenPhase.LISTENING,
                ) { Text("开听") }
                Button(
                    onClick = { ListenService.stop(context) },
                    enabled = listen.phase == ListenPhase.LISTENING,
                ) { Text("停止") }
            }

            HorizontalDivider()
            Text("音源", style = MaterialTheme.typography.titleSmall)
            LaunchedSourcesOnce { sources = it }

            sources.forEach { src ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(selected = picked == src.label, onClick = {
                        picked = src.label
                        prefsStore.save(prefsStore.load().copy(lastSourceLabel = src.label))
                    })
                    Text(src.label, style = MaterialTheme.typography.bodyMedium)
                }
            }

            HorizontalDivider()
            Text("字幕", style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                CaptionMode.entries.forEach { m ->
                    FilterChip(selected = mode == m, onClick = {
                        mode = m
                        prefsStore.save(prefsStore.load().copy(mode = m))
                    }, label = { Text(modeLabel(m)) })
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SubColor.entries.forEach { c ->
                    FilterChip(selected = color == c, onClick = {
                        color = c
                        prefsStore.save(prefsStore.load().copy(style = prefsStore.load().style.copy(color = c)))
                    }, label = { Text(colorLabel(c)) })
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SubSize.entries.forEach { s ->
                    FilterChip(selected = size == s, onClick = {
                        size = s
                        prefsStore.save(prefsStore.load().copy(style = prefsStore.load().style.copy(size = s)))
                    }, label = { Text(sizeLabel(s)) })
                }
                FilterChip(selected = plateOn, onClick = {
                    plateOn = !plateOn
                    prefsStore.save(prefsStore.load().copy(style = prefsStore.load().style.copy(plateOn = plateOn)))
                }, label = { Text("黑底衬") })
            }

            if (BuildConfig.DEBUG) {
                HorizontalDivider()
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = speechOn, onCheckedChange = { on ->
                        speechOn = on
                        player?.release()
                        player = null
                        if (on) {
                            player = MediaPlayer().apply {
                                setAudioAttributes(
                                    AudioAttributes.Builder()
                                        .setUsage(AudioAttributes.USAGE_MEDIA)
                                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                                        .build(),
                                )
                                val afd = context.resources.openRawResourceFd(R.raw.en_speech)
                                setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
                                afd.close()
                                isLooping = true
                                prepare()
                                start()
                            }
                        }
                    })
                    Text("（调试）自播英语素材", style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

private fun modeLabel(m: CaptionMode) = when (m) {
    CaptionMode.ORIG -> "仅原文"
    CaptionMode.BILINGUAL -> "双语"
    CaptionMode.TRANS -> "仅译文"
}

private fun colorLabel(c: SubColor) = when (c) {
    SubColor.WHITE -> "白"
    SubColor.YELLOW -> "黄"
    SubColor.CYAN -> "青"
}

private fun sizeLabel(s: SubSize) = when (s) {
    SubSize.S -> "小"
    SubSize.M -> "中"
    SubSize.L -> "大"
}

@Composable
private fun LaunchedSourcesOnce(update: (List<com.livetranslator.android.listen.AudioSource>) -> Unit) {
    val context = LocalContext.current
    androidx.compose.runtime.LaunchedEffect(Unit) {
        update(Sources.list(context))
    }
}
