package com.livetranslator.android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.IBinder
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlin.math.sqrt

/* 抓音 Spike 的前台服务（扔掉用）。
   targetSdk 34+ 顺序硬约束：用户先过 createScreenCaptureIntent → 本服务以 mediaProjection 类型
   startForeground → 才能 getMediaProjection。抓 USAGE_MEDIA/GAME/UNKNOWN 的混音。 */

class CaptureService : Service() {

    data class SpikeState(
        val running: Boolean = false,
        val level: Int = 0,
        val totalBytes: Long = 0,
        val message: String = "没在抓。点「① 授权并抓音」开始。",
    )

    private var projection: MediaProjection? = null
    private var record: AudioRecord? = null
    private var thread: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopCapture()
            stopSelf()
            return START_NOT_STICKY
        }
        val code = intent?.getIntExtra(EXTRA_CODE, 0) ?: 0
        @Suppress("DEPRECATION")
        val data: Intent? = intent?.getParcelableExtra(EXTRA_DATA)
        if (code == 0 || data == null) {
            pushMessage("启动参数缺失，抓不了。")
            stopSelf()
            return START_NOT_STICKY
        }

        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "抓音 Spike", NotificationManager.IMPORTANCE_LOW)
        )
        val notif = Notification.Builder(this, CHANNEL)
            .setContentTitle("直播同传 Spike")
            .setContentText("正在抓播放音频做验证")
            .setSmallIcon(android.R.drawable.ic_media_play)
            .build()
        startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)

        startCapture(code, data)
        return START_NOT_STICKY
    }

    private fun startCapture(code: Int, data: Intent) {
        val pm = getSystemService(MediaProjectionManager::class.java)
        val proj = try {
            pm.getMediaProjection(code, data)
        } catch (e: Exception) {
            null
        }
        if (proj == null) {
            pushMessage("getMediaProjection 失败或返回空。")
            stopSelf()
            return
        }
        projection = proj
        proj.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                pushMessage("MediaProjection 被撤销（onStop）——正式版要处理重授权。")
                stopCapture()
                stopSelf()
            }
        }, null)

        val config = AudioPlaybackCaptureConfiguration.Builder(proj)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .build()
        val fmt = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(16000)
            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
            .build()
        val minBuf = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = try {
            AudioRecord.Builder()
                .setAudioFormat(fmt)
                .setBufferSizeInBytes(minBuf * 4)
                .setAudioPlaybackCaptureConfig(config)
                .build()
        } catch (e: Exception) {
            pushMessage("AudioRecord 构建失败：${e.message}")
            stopSelf()
            return
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            pushMessage("AudioRecord 没初始化成功（state=${rec.state}）。")
            rec.release()
            stopSelf()
            return
        }
        record = rec
        rec.startRecording()
        state.value = state.value.copy(
            running = true, level = 0, totalBytes = 0,
            message = "在抓（MEDIA/GAME/UNKNOWN · 16k 单声道）。去别的 App 放声音，回来看电平。",
        )
        Log.i(TAG, "capture started, minBuf=$minBuf")

        val r = rec
        thread = Thread {
            val buf = ShortArray(1600) /* 100ms */
            var total = 0L
            var loops = 0
            var accSq = 0.0
            var accN = 0
            while (!Thread.currentThread().isInterrupted && record === r) {
                val n = r.read(buf, 0, buf.size)
                if (n <= 0) {
                    Log.w(TAG, "read=$n")
                    continue
                }
                total += n * 2L
                HostedLink.sendPcm(buf, n) /* 端到端验证：开了推流就把抓到的混音送托管 */
                for (i in 0 until n) {
                    val v = buf[i] / 32768.0
                    accSq += v * v
                }
                accN += n
                loops++
                if (loops >= 5) { /* ~500ms 汇总一次 */
                    val rms = sqrt(accSq / accN)
                    val pct = (rms * 260).toInt().coerceIn(0, 100)
                    state.value = state.value.copy(level = pct, totalBytes = total)
                    Log.i(TAG, "rms=${pct}% bytes=$total")
                    loops = 0; accSq = 0.0; accN = 0
                }
            }
        }.also { it.start() }
    }

    private fun stopCapture() {
        thread?.interrupt()
        thread = null
        record?.let {
            try { it.stop(); it.release() } catch (_: Exception) {}
        }
        record = null
        projection?.stop()
        projection = null
        state.value = state.value.copy(running = false, level = 0)
        Log.i(TAG, "capture stopped")
    }

    override fun onDestroy() {
        stopCapture()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "SpikeCap"
        private const val CHANNEL = "spike"
        private const val NOTIF_ID = 1
        private const val EXTRA_CODE = "code"
        private const val EXTRA_DATA = "data"
        private const val ACTION_STOP = "STOP"

        val state = MutableStateFlow(SpikeState())

        fun pushMessage(msg: String) {
            state.value = state.value.copy(message = msg)
            Log.i(TAG, msg)
        }

        fun start(ctx: Context, resultCode: Int, data: Intent) {
            ctx.startService(
                Intent(ctx, CaptureService::class.java)
                    .putExtra(EXTRA_CODE, resultCode)
                    .putExtra(EXTRA_DATA, data)
            )
        }

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, CaptureService::class.java).setAction(ACTION_STOP))
        }
    }
}
