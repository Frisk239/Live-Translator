package com.livetranslator.android.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** 与桌面 tests/shell_reducer.test.ts 同语义对齐——两端字幕行为必须一字不差。 */
class CaptionStateTest {

    private fun draft(orig: String, trans: String) = ListenEvent.Draft(orig, trans)
    private fun final(orig: String, trans: String) = ListenEvent.Final(orig, trans)

    @Test
    fun `草稿往外长 定稿冻住 静默约两秒后撤条`() {
        var s = CaptionState()
        s = CaptionReducer.onEvent(s, draft("so we", ""), 0)
        assertEquals("so we", s.bar?.orig)
        assertFalse(s.bar!!.isFinal)
        s = CaptionReducer.onEvent(s, draft("so we're gonna", "我们打算"), 100)
        assertEquals("我们打算", s.bar?.trans)
        s = CaptionReducer.onEvent(s, final("so we're gonna", "我们打算试试"), 200)
        assertTrue(s.bar!!.isFinal)

        s = CaptionReducer.onTick(s, 200 + SILENT_WITHDRAW_MS - 1)
        assertNotNull("差 1ms 不撤", s.bar)
        s = CaptionReducer.onTick(s, 200 + SILENT_WITHDRAW_MS)
        assertNull("满两秒直接拿掉，不淡出", s.bar)
    }

    @Test
    fun `下一条草稿立刻挤掉上一条定稿 不等撤条计时`() {
        var s = CaptionState()
        s = CaptionReducer.onEvent(s, final("a", "甲"), 0)
        s = CaptionReducer.onEvent(s, draft("b", "乙"), 300)
        assertEquals("b", s.bar?.orig)
        assertFalse(s.bar!!.isFinal)
        s = CaptionReducer.onEvent(s, final("b", "乙"), 400)
        s = CaptionReducer.onTick(s, 400 + SILENT_WITHDRAW_MS)
        assertNull(s.bar)
    }

    @Test
    fun `空译文定稿后译文落地 只换译文 定稿时刻不动 撤条从落地重算`() {
        var s = CaptionState()
        s = CaptionReducer.onEvent(s, final("boss fight", ""), 0)
        val finalAt = s.bar!!.finalAt
        s = CaptionReducer.onEvent(s, final("boss fight", "Boss 战"), 1400)
        assertEquals("Boss 战", s.bar?.trans)
        assertEquals("定稿时刻不重置", finalAt, s.bar?.finalAt)
        s = CaptionReducer.onTick(s, 1400 + SILENT_WITHDRAW_MS - 1)
        assertNotNull("译文刚落地不满两秒不撤", s.bar)
        s = CaptionReducer.onTick(s, 1400 + SILENT_WITHDRAW_MS)
        assertNull(s.bar)
    }

    @Test
    fun `流式生长 前缀延长 不算换版 不顺延撤条`() {
        var s = CaptionState()
        s = CaptionReducer.onEvent(s, final("a", "老大"), 0)
        val from = s.bar!!.withdrawFrom
        s = CaptionReducer.onEvent(s, final("a", "老大争斗"), 500)
        assertEquals("前缀延长不重置", from, s.bar?.withdrawFrom)
        assertEquals(0, s.bar?.withdrawResets)
        s = CaptionReducer.onTick(s, 0 + SILENT_WITHDRAW_MS)
        assertNull("从首条定稿起算撤", s.bar)
    }

    @Test
    fun `换版顺延每条最多两次 第三次不再钉住条`() {
        var s = CaptionState()
        s = CaptionReducer.onEvent(s, final("a", "一版"), 0)
        s = CaptionReducer.onEvent(s, final("a", "二版完全不同"), 1800)
        assertEquals(1, s.bar?.withdrawResets)
        s = CaptionReducer.onEvent(s, final("a", "三版又不同"), 3600)
        assertEquals(2, s.bar?.withdrawResets)
        s = CaptionReducer.onEvent(s, final("a", "四版还在变"), 5400)
        assertEquals("第三次起不再顺延", 2, s.bar?.withdrawResets)
        val frozen = s.bar!!.withdrawFrom
        s = CaptionReducer.onEvent(s, final("a", "五版"), 6000)
        assertEquals(frozen, s.bar?.withdrawFrom)
    }

    @Test
    fun `定稿后同原文的草稿只更新译文 不降级回草稿`() {
        var s = CaptionState()
        s = CaptionReducer.onEvent(s, final("a", ""), 0)
        s = CaptionReducer.onEvent(s, draft("a", "流式译文"), 100)
        assertTrue(s.bar!!.isFinal)
        assertEquals("流式译文", s.bar?.trans)
    }

    @Test
    fun `新一条定稿照常重建 定稿时刻从头算`() {
        var s = CaptionState()
        s = CaptionReducer.onEvent(s, final("a", "甲"), 0)
        s = CaptionReducer.onEvent(s, final("b", "乙"), 300)
        assertEquals(0, s.bar?.withdrawResets)
        assertEquals(300L, s.bar?.finalAt)
        assertEquals(300L, s.bar?.withdrawFrom)
    }

    @Test
    fun `notice 不在状态机里处理 由监听层停听撤条`() {
        val s = CaptionReducer.onEvent(CaptionState(), ListenEvent.Notice(NoticeKind.KICKED), 0)
        assertNull(s.bar)
    }
}
