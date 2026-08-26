package com.livetranslator.android.core

/** 字幕条状态机——语义与桌面 reducer（desktop/src/core/reducer.ts）逐条对齐：
 *  草稿可长可换；同条定稿二次落地只换译文、定稿时刻不重置；换版顺延静默撤条、
 *  前缀延长不算换版、每条最多两次；定稿后同原文的草稿不降级。纯逻辑，不碰线程与时钟。 */

const val SILENT_WITHDRAW_MS = 2000L

data class SubtitleBar(
    val orig: String,
    val trans: String,
    val isFinal: Boolean,
    /** 首条定稿时刻（语义时刻，红线计时口径）；同条二次落地不重置 */
    val finalAt: Long? = null,
    /** 静默撤条计时起点；换版落地顺延，观众读到的最后一版至少亮满两秒 */
    val withdrawFrom: Long = 0L,
    /** 换版顺延次数，每条最多 2 次（防长流把条钉死屏上） */
    val withdrawResets: Int = 0,
)

data class CaptionState(val bar: SubtitleBar? = null)

object CaptionReducer {

    fun onEvent(state: CaptionState, ev: ListenEvent, now: Long): CaptionState = when (ev) {
        is ListenEvent.Draft -> onDraft(state, ev, now)
        is ListenEvent.Final -> onFinal(state, ev, now)
        is ListenEvent.Notice -> state // 终态（顶号/满员/登录失效）由监听层停听撤条，不在状态机里
    }

    fun onTick(state: CaptionState, now: Long): CaptionState {
        val bar = state.bar ?: return state
        if (bar.isFinal && now - bar.withdrawFrom >= SILENT_WITHDRAW_MS) return CaptionState(null)
        return state
    }

    private fun onDraft(state: CaptionState, ev: ListenEvent.Draft, now: Long): CaptionState {
        val prev = state.bar
        if (prev != null && prev.isFinal && prev.orig == ev.orig) {
            // 定稿后同原文的草稿（流式译文写回）只更新译文，不把条降级回草稿
            return CaptionState(prev.copy(trans = ev.trans))
        }
        return CaptionState(
            SubtitleBar(orig = ev.orig, trans = ev.trans, isFinal = false, finalAt = null, withdrawFrom = now),
        )
    }

    private fun onFinal(state: CaptionState, ev: ListenEvent.Final, now: Long): CaptionState {
        val prev = state.bar
        if (prev != null && prev.isFinal && prev.orig == ev.orig) {
            // 同一条定稿的二次落地（译文后到）：只换译文；prev 为空串时恒算换版（引擎同款坑）
            val grew = prev.trans.isNotEmpty() && ev.trans.startsWith(prev.trans)
            val changed = ev.trans != prev.trans
            val reset = changed && !grew && prev.withdrawResets < 2
            return CaptionState(
                prev.copy(
                    trans = ev.trans,
                    withdrawFrom = if (reset) now else prev.withdrawFrom,
                    withdrawResets = prev.withdrawResets + if (reset) 1 else 0,
                ),
            )
        }
        return CaptionState(
            SubtitleBar(
                orig = ev.orig, trans = ev.trans, isFinal = true,
                finalAt = now, withdrawFrom = now, withdrawResets = 0,
            ),
        )
    }
}
