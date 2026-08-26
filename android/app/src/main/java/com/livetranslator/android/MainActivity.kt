package com.livetranslator.android

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlin.math.PI
import kotlin.math.sin

/* 抓音 Spike（扔掉用）：验证「MediaProjection 授权 → AudioPlaybackCapture → PCM 电平」管道。
   自播测试音用来验证抓取方自己 App 的声音会不会进流（官方文档说会，需 excludeUid 才排除）。 */

class MainActivity : ComponentActivity() {

    private lateinit var projectionManager: MediaProjectionManager
    private var toneTrack: AudioTrack? = null

    private val requestPerms =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
            if (granted[Manifest.permission.RECORD_AUDIO] == true) launchProjectionIntent()
            else CaptureService.pushMessage("没有录音权限，开不了。")
        }

    private val projectionLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            if (result.resultCode == RESULT_OK && result.data != null) {
                CaptureService.start(this, result.resultCode, result.data!!)
            } else {
                CaptureService.pushMessage("授权被取消（resultCode=${result.resultCode}）。")
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        projectionManager = getSystemService(MediaProjectionManager::class.java)
        setContent { SpikeApp() }
    }

    private fun launchProjectionIntent() {
        projectionLauncher.launch(projectionManager.createScreenCaptureIntent())
    }

    private fun onStartCaptureClicked() {
        val need = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) need.add(Manifest.permission.POST_NOTIFICATIONS)
        val missing = need.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) launchProjectionIntent()
        else requestPerms.launch(missing.toTypedArray())
    }

    private fun startTone() {
        stopTone()
        val rate = 16000
        val n = rate * 2
        val samples = ShortArray(n) { i -> (sin(2 * PI * 440.0 * i / rate) * 12000).toInt().toShort() }
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(rate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setTransferMode(AudioTrack.MODE_STATIC)
            .setBufferSizeInBytes(n * 2)
            .build()
        track.write(samples, 0, n)
        track.setLoopPoints(0, n, -1)
        track.play()
        toneTrack = track
        CaptureService.pushMessage("自播测试音已开（440Hz · USAGE_MEDIA · 循环）。")
    }

    private fun stopTone() {
        toneTrack?.let {
            try { it.stop(); it.release() } catch (_: Exception) {}
        }
        toneTrack = null
    }

    @Composable
    fun SpikeApp() {
        val st by CaptureService.state.collectAsState()
        var toneOn by remember { mutableStateOf(false) }

        MaterialTheme {
            Surface(modifier = Modifier.fillMaxSize()) {
                Column(
                    modifier = Modifier.fillMaxSize().padding(20.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text("抓音 Spike", style = MaterialTheme.typography.titleLarge)
                    Text(
                        "MediaProjection 授权 → AudioPlaybackCapture（16k 单声道 PCM16）→ 电平。" +
                            "步骤：点①过系统授权，切到别的 App 放声音，回来看电平；勾②验证自己 App 的声音进不进流（预期会进，正式版要 excludeUid 排除）。",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    LinearProgressIndicator(
                        progress = { st.level / 100f },
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text("电平 ${st.level}% · 已采 ${st.totalBytes / 1024} KB · ${if (st.running) "在抓" else "停着"}")
                    Text(st.message, style = MaterialTheme.typography.bodySmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Button(onClick = { onStartCaptureClicked() }, enabled = !st.running) { Text("① 授权并抓音") }
                        Button(onClick = { CaptureService.stop(this@MainActivity) }, enabled = st.running) { Text("停止") }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = toneOn, onCheckedChange = {
                            toneOn = it
                            if (it) startTone() else { stopTone(); CaptureService.pushMessage("自播测试音已关。") }
                        })
                        Text("② 自播测试音（440Hz 循环）")
                    }
                }
            }
        }
    }
}
