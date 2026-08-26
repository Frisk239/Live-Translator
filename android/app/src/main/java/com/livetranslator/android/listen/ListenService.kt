package com.livetranslator.android.listen

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import com.livetranslator.android.BuildConfig
import com.livetranslator.android.MainActivity
import com.livetranslator.android.R
import com.livetranslator.android.account.SessionTokenStore
import com.livetranslator.android.core.CaptionReducer
import com.livetranslator.android.core.CaptionState
import com.livetranslator.android.core.ListenEvent
import com.livetranslator.android.core.NoticeKind
import com.livetranslator.android.core.Prefs
import com.livetranslator.android.core.PrefsStore
import com.livetranslator.android.core.Seam
import com.livetranslator.android.overlay.CaptionOverlay
import kotlin.math.sqrt

/** 监听前台服务（android-spec：面板开停 / 抓音推流 / 常驻通知 / 悬浮字幕的宿主）。
 *  生命周期边界（ADR 0038）：划掉（onTaskRemoved）= 停听退出；锁屏 = 停听
 *  （Android 15 系统 onStop + 老版本 SCREEN_OFF 兜底）；被顶/满员/登录失效终态即停不重试。 */

class ListenService : Service() {

    private var projection: MediaProjection? = null
    private var record: AudioRecord? = null
    private var thread: Thread? = null
    private var link: HostedLink? = null
    private var overlay: CaptionOverlay? = null
    private var prefs: Prefs = Prefs()
    private var screenOffReceiver: BroadcastReceiver? = null

    /** 主动停听时置位：projection.stop() 会回调自己的 onStop，别再当「锁屏/撤销」处理 */
    @Volatile
    private var stoppedByUs = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopListening(NoticeKind.NO_SPEECH, notify = false)
                ListenBus.reset()
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_BEGIN -> begin(intent)
            else -> stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun begin(intent: Intent) {
        stoppedByUs = false
        val code = intent.getIntExtra(EXTRA_CODE, 0)
        @Suppress("DEPRECATION")
        val data: Intent? = intent.getParcelableExtra(EXTRA_DATA)
        val token = SessionTokenStore(this).load()?.second
        if (code == 0 || data == null || token == null) {
            ListenBus.update { it.copy(phase = ListenPhase.FAILED, failureKind = NoticeKind.AUTH, statusText = "登录已失效，重新登录后再开托管听译。") }
            stopSelf()
            return
        }
        stopListening(NoticeKind.NO_SPEECH, notify = false) // 重开一路前先收干净旧的

        prefs = (application as PrefsOwner).prefsStore.load()
        startInForeground()
        registerScreenOff()

        val sourceLabel = intent.getStringExtra(EXTRA_SOURCE_LABEL) ?: AudioSource.ALL_LABEL
        val sourceUid = intent.getIntExtra(EXTRA_SOURCE_UID, -1)
        val includeSelf = intent.getBooleanExtra(EXTRA_INCLUDE_SELF, false)

        val pm = getSystemService(MediaProjectionManager::class.java)
        val proj = try {
            pm.getMediaProjection(code, data)
        } catch (_: Exception) {
            null
        }
        if (proj == null) {
            fail("授权失效了，重新按开听。")
            return
        }
        projection = proj
        proj.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                // Android 15 锁屏/系统撤销都会走这里（ADR 0038：锁屏即停听）；
                // 自己 stopProjection 触发的回调不算，别覆盖「没在听」
                if (stoppedByUs) {
                    stopSelf()
                    return
                }
                stopListening(NoticeKind.NO_SPEECH, notify = false)
                ListenBus.update { it.copy(phase = ListenPhase.IDLE, statusText = "锁屏或撤销授权，已停听。要看字幕重新按开听。") }
                stopSelf()
            }
        }, null)

        val rec = buildRecorder(proj, sourceUid, includeSelf)
        if (rec == null) {
            fail("抓不到声音（录音初始化失败）。")
            return
        }
        record = rec

        attachOverlay()
        link = HostedLink(
            wsUrl = BuildConfig.HOSTED_WS,
            token = token,
            source = sourceLabel,
            listener = linkListener,
        ).also { it.start() }

        ListenBus.update {
            it.copy(
                phase = ListenPhase.LISTENING,
                statusText = "在听 · $sourceLabel",
                bar = null,
                failureKind = null,
            )
        }
        updateNotification("在听 · $sourceLabel")

        val r = rec
        r.startRecording()
        thread = Thread {
            val buf = ShortArray(1600) // 100ms
            var silentMs = 0L
            var warnedSilence = false
            while (!Thread.currentThread().isInterrupted && record === r) {
                val n = r.read(buf, 0, buf.size)
                if (n <= 0) continue
                link?.sendPcm(buf, n)
                var acc = 0.0
                for (i in 0 until n) {
                    val v = buf[i] / 32768.0
                    acc += v * v
                }
                val rms = sqrt(acc / n)
                if (rms < SILENCE_RMS) {
                    silentMs += 100
                    if (!warnedSilence && silentMs >= QUIET_HINT_MS) {
                        warnedSilence = true
                        ListenBus.update { st -> if (st.phase == ListenPhase.LISTENING) st.copy(statusText = "在听 · $sourceLabel（还没听到声音；若对方不让抓会是静音）") else st }
                    }
                } else {
                    silentMs = 0
                    if (warnedSilence) {
                        warnedSilence = false
                        ListenBus.update { st -> st.copy(statusText = "在听 · $sourceLabel") }
                    }
                }
            }
        }.also { it.start() }
    }

    private val linkListener = object : HostedLink.Listener {
        override fun onEvent(text: String) {
            val ev = Seam.parseEvent(text) ?: return
            if (ev !is ListenEvent.Notice) {
                val next = CaptionReducer.onEvent(CaptionState(ListenBus.state.value.bar), ev, System.currentTimeMillis())
                ListenBus.update { it.copy(bar = next.bar) }
                // 缝回调在 OkHttp 读线程；View 只能主线程动
                overlay?.post { overlay?.render() }
            }
        }

        override fun onConnected() {
            ListenBus.update { it.copy(statusText = it.statusText.replaceFirst("（网不稳，重连中…）", "")) }
        }

        override fun onTerminal(kind: NoticeKind, message: String) {
            stopListening(kind, notify = true)
            ListenBus.update { it.copy(phase = ListenPhase.FAILED, failureKind = kind, statusText = ListenBus.stoppedText(kind)) }
            updateNotification("已停 · ${ListenBus.stoppedText(kind)}")
            stopSelf()
        }

        override fun onExhausted(message: String) {
            stopListening(null, notify = true)
            ListenBus.update { it.copy(phase = ListenPhase.FAILED, failureKind = null, statusText = message) }
            updateNotification("已停 · 网断")
            stopSelf()
        }
    }

    /** 整屏授权下按音源过滤（ADR 0037）：选 App = addMatchingUid（自己天然不在内）；
     *  全部 = excludeUid(自己)；调试项可含自己（E2E 自播用）。 */
    private fun buildRecorder(proj: MediaProjection, sourceUid: Int, includeSelf: Boolean): AudioRecord? {
        val configBuilder = AudioPlaybackCaptureConfiguration.Builder(proj)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
        if (!includeSelf) {
            if (sourceUid > 0) {
                configBuilder.addMatchingUid(sourceUid)
            } else {
                configBuilder.excludeUid(android.os.Process.myUid())
            }
        }
        val fmt = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(16000)
            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
            .build()
        val minBuf = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        return try {
            AudioRecord.Builder()
                .setAudioFormat(fmt)
                .setBufferSizeInBytes(minBuf * 4)
                .setAudioPlaybackCaptureConfig(configBuilder.build())
                .build()
                .takeIf { it.state == AudioRecord.STATE_INITIALIZED }
        } catch (_: Exception) {
            null
        }
    }

    private fun attachOverlay() {
        if (!android.provider.Settings.canDrawOverlays(this)) return // 面板侧已引导，这里兜底不挂
        val owner = application as PrefsOwner
        detachOverlay()
        overlay = CaptionOverlay(
            context = this,
            prefs = prefs,
            onTap = {
                startActivity(
                    Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            },
            onMoved = { x, y ->
                val p = owner.prefsStore.load()
                owner.prefsStore.save(p.copy(overlayX = x, overlayY = y))
            },
        ).also { it.attach() }
    }

    private fun detachOverlay() {
        overlay?.detach()
        overlay = null
    }

    private fun fail(text: String) {
        ListenBus.update { it.copy(phase = ListenPhase.FAILED, statusText = text) }
        updateNotification("已停")
        stopSelf()
    }

    private fun stopListening(kind: NoticeKind?, notify: Boolean) {
        stoppedByUs = true
        thread?.interrupt()
        thread = null
        record?.let {
            try { it.stop(); it.release() } catch (_: Exception) {}
        }
        record = null
        link?.stop()
        link = null
        detachOverlay()
        if (kind == null && notify) {
            // 普通停止：撤条回没在听
            ListenBus.reset()
        }
        projection?.stop()
        projection = null
        unregisterScreenOff()
    }

    // ---------- 常驻通知（ADR 0038：在听状态的锚点，带停止） ----------

    private fun startInForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "在听状态", NotificationManager.IMPORTANCE_LOW))
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, buildNotification("准备在听…"), ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(NOTIF_ID, buildNotification("准备在听…"))
        }
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stop = PendingIntent.getService(
            this, 1,
            Intent(this, ListenService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "停止", stop).build())
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(text: String) {
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification(text))
    }

    // ---------- 生命周期（ADR 0038） ----------

    override fun onTaskRemoved(rootIntent: Intent?) {
        // 划掉最近任务 = 停听退出
        stopListening(NoticeKind.NO_SPEECH, notify = false)
        ListenBus.reset()
        stopSelf()
        super.onTaskRemoved(rootIntent)
    }

    private fun registerScreenOff() {
        if (screenOffReceiver != null) return
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context?, i: Intent?) {
                stopListening(NoticeKind.NO_SPEECH, notify = false)
                ListenBus.update { it.copy(phase = ListenPhase.IDLE, statusText = "锁屏已停听。要看字幕重新按开听。") }
                stopSelf()
            }
        }
        val filter = IntentFilter(Intent.ACTION_SCREEN_OFF)
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(receiver, filter, RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(receiver, filter)
        }
        screenOffReceiver = receiver
    }

    private fun unregisterScreenOff() {
        screenOffReceiver?.let { runCatching { unregisterReceiver(it) } }
        screenOffReceiver = null
    }

    override fun onDestroy() {
        stopListening(null, notify = false)
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "listening"
        private const val NOTIF_ID = 100
        const val ACTION_BEGIN = "BEGIN"
        const val ACTION_STOP = "STOP"
        const val EXTRA_CODE = "code"
        const val EXTRA_DATA = "data"
        const val EXTRA_SOURCE_LABEL = "source_label"
        const val EXTRA_SOURCE_UID = "source_uid"
        const val EXTRA_INCLUDE_SELF = "include_self"
        private const val SILENCE_RMS = 0.004
        private const val QUIET_HINT_MS = 8000L

        fun begin(ctx: Context, code: Int, data: Intent, label: String, uid: Int, includeSelf: Boolean) {
            ctx.startForegroundService(
                Intent(ctx, ListenService::class.java)
                    .setAction(ACTION_BEGIN)
                    .putExtra(EXTRA_CODE, code)
                    .putExtra(EXTRA_DATA, data)
                    .putExtra(EXTRA_SOURCE_LABEL, label)
                    .putExtra(EXTRA_SOURCE_UID, uid)
                    .putExtra(EXTRA_INCLUDE_SELF, includeSelf),
            )
        }

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, ListenService::class.java).setAction(ACTION_STOP))
        }
    }
}

/** Application 实现它，把 PrefsStore 递给服务（服务自己不建第二份）。 */
interface PrefsOwner {
    val prefsStore: PrefsStore
}
