package com.livetranslator.android.listen

import com.livetranslator.android.core.NoticeKind
import com.livetranslator.android.core.SubtitleBar
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** 监听状态总线：前台服务是唯一写方，控制面板与悬浮字幕窗都是读方。
 *  phase 语义与桌面壳一致：在听 / 没在听 / 失败（失败带终态种类与出路文案）。 */

enum class ListenPhase { IDLE, LISTENING, FAILED }

data class ListenState(
    val phase: ListenPhase = ListenPhase.IDLE,
    val statusText: String = "没在听。选一个音源，按开听。",
    val bar: SubtitleBar? = null,
    val failureKind: NoticeKind? = null,
)

object ListenBus {
    private val _state = MutableStateFlow(ListenState())
    val state: StateFlow<ListenState> = _state

    fun update(mut: (ListenState) -> ListenState) {
        _state.value = mut(_state.value)
    }

    fun reset() {
        _state.value = ListenState()
    }

    fun stoppedText(kind: NoticeKind?): String = when (kind) {
        NoticeKind.KICKED -> "已在别处开听，这里停了。要在这台继续，重新按开听。"
        NoticeKind.FULL -> "现在满了，稍后再试。已开的听译不受影响。"
        NoticeKind.AUTH -> "登录已失效，重新登录后再开托管听译。"
        else -> "没在听。选一个音源，按开听。"
    }
}
