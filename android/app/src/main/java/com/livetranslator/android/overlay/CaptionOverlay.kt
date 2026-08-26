package com.livetranslator.android.overlay

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import com.livetranslator.android.core.CaptionMode
import com.livetranslator.android.core.CaptionReducer
import com.livetranslator.android.core.Prefs
import com.livetranslator.android.core.SubSize
import com.livetranslator.android.core.SubColor
import com.livetranslator.android.listen.ListenBus
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs

/** 悬浮字幕窗（ADR 0035：View + WindowManager，Compose 与 SYSTEM_ALERT_WINDOW 配合不佳）。
 *  一条字幕条两行（原文/译文），字穿透不挡直播交互（FLAG_NOT_FOCUSABLE + 不吃点击），
 *  点按（非拖动）回控制面板；拖到哪记到哪；撤条计时自己跑（250ms tick 喂 CaptionReducer）。 */

class CaptionOverlay(
    context: Context,
    private val prefs: Prefs,
    private val onTap: () -> Unit,
    private val onMoved: (Int, Int) -> Unit,
) : LinearLayout(context) {

    private val wm = context.getSystemService(WindowManager::class.java)
    private val origView = TextView(context)
    private val transView = TextView(context)
    private val params = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
            or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
            or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
    }

    private val ticking = AtomicBoolean(false)

    init {
        orientation = VERTICAL
        val pad = dp(8f)
        setPadding(dp(14f), pad, dp(14f), pad)
        if (prefs.style.plateOn) {
            background = createPlate()
        }
        configureText(origView, bold = false)
        configureText(transView, bold = true)
        addView(origView)
        addView(transView)
        applyPrefs()
        ListenBus.state.value // 初次渲染在 attach 后
    }

    private fun configureText(v: TextView, bold: Boolean) {
        v.typeface = Typeface.create(Typeface.SANS_SERIF, if (bold) Typeface.BOLD else Typeface.NORMAL)
        v.setShadowLayer(4f, 0f, 2f, Color.argb(200, 0, 0, 0))
        v.includeFontPadding = false
    }

    private fun createPlate() = android.graphics.drawable.GradientDrawable().apply {
        setColor(Color.argb(178, 0, 0, 0))
        cornerRadius = dp(6f).toFloat()
    }

    fun applyPrefs() {
        val color = when (prefs.style.color) {
            SubColor.WHITE -> Color.WHITE
            SubColor.YELLOW -> Color.rgb(255, 221, 0)
            SubColor.CYAN -> Color.rgb(64, 224, 208)
        }
        val sizeSp = when (prefs.style.size) {
            SubSize.S -> 14f; SubSize.M -> 17f; SubSize.L -> 21f
        }
        listOf(origView, transView).forEach {
            it.textSize = sizeSp
            it.setTextColor(color)
        }
        render(ListenBus.state.value.bar)
    }

    fun attach() {
        if (windowToken == null && parent == null) {
            positionFromPrefs()
            wm.addView(this, params)
        }
        if (ticking.compareAndSet(false, true)) scheduleTick()
    }

    fun detach() {
        ticking.set(false)
        removeCallbacks(tickRunnable)
        if (windowToken != null) wm.removeView(this)
    }

    /** Bus 状态变化时由服务调用（单一写方）。 */
    fun render() {
        render(ListenBus.state.value.bar)
    }

    private fun render(bar: com.livetranslator.android.core.SubtitleBar?) {
        val mode = prefs.mode
        origView.visibility = if (mode == CaptionMode.TRANS || bar == null) GONE else VISIBLE
        transView.visibility = if (mode == CaptionMode.ORIG || bar == null) GONE else VISIBLE
        if (bar == null) {
            visibility = GONE
            return
        }
        visibility = VISIBLE
        origView.text = bar.orig
        transView.text = bar.trans
        // 空译文那一行不占高（直译等待期只亮原文）
        if (bar.trans.isEmpty()) transView.visibility = GONE
    }

    private fun positionFromPrefs() {
        val display = wm.maximumWindowMetrics.bounds
        params.x = prefs.overlayX ?: (display.centerX() - measuredWidth / 2)
        params.y = prefs.overlayY ?: (display.bottom - (display.height() / 6))
    }

    private val tickRunnable = object : Runnable {
        override fun run() {
            if (!ticking.get()) return
            val s = CaptionReducer.onTick(ListenBus.state.value.let { st ->
                com.livetranslator.android.core.CaptionState(st.bar)
            }, System.currentTimeMillis())
            if (s.bar != ListenBus.state.value.bar) {
                ListenBus.update { it.copy(bar = s.bar) }
                render()
            }
            postDelayed(this, TICK_MS)
        }
    }

    private fun scheduleTick() {
        removeCallbacks(tickRunnable)
        if (ticking.get()) postDelayed(tickRunnable, TICK_MS)
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onInterceptTouchEvent(ev: MotionEvent): Boolean {
        // 交给 onTouch 处理拖动/点按（子 TextView 不消费）
        return true
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.rawX
                downY = event.rawY
                paramX = params.x
                paramY = params.y
                moved = false
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                val dx = event.rawX - downX
                val dy = event.rawY - downY
                if (moved || abs(dx) > dp(4f) || abs(dy) > dp(4f)) {
                    moved = true
                    params.x = paramX + dx.toInt()
                    params.y = paramY + dy.toInt()
                    wm.updateViewLayout(this, params)
                }
                return true
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (!moved && event.actionMasked == MotionEvent.ACTION_UP) onTap()
                else if (moved) onMoved(params.x, params.y)
                return true
            }
        }
        return super.onTouchEvent(event)
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        // 最宽半屏，长句折行
        val half = (wm.maximumWindowMetrics.bounds.width() * 0.62f).toInt()
        super.onMeasure(
            MeasureSpec.makeMeasureSpec(half, MeasureSpec.AT_MOST),
            heightMeasureSpec,
        )
    }

    private var downX = 0f
    private var downY = 0f
    private var paramX = 0
    private var paramY = 0
    private var moved = false

    private fun dp(v: Float): Int = (v * resources.displayMetrics.density + 0.5f).toInt()

    companion object {
        private const val TICK_MS = 250L
    }
}
