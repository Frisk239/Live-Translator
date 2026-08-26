package com.livetranslator.android.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class SeamTest {
    @Test
    fun `parse draft and final with optional seq`() {
        val d = Seam.parseEvent("""{"type":"draft","orig":"so we","trans":"我们"}""")
        assertEquals(ListenEvent.Draft("so we", "我们", null), d)
        val f = Seam.parseEvent("""{"type":"final","orig":"so we're gonna","trans":"我们打算","seq":7}""")
        assertEquals(ListenEvent.Final("so we're gonna", "我们打算", 7), f)
    }

    @Test
    fun `parse notice kinds case-insensitively`() {
        assertEquals(ListenEvent.Notice(NoticeKind.KICKED), Seam.parseEvent("""{"type":"notice","kind":"kicked"}"""))
        assertEquals(ListenEvent.Notice(NoticeKind.FULL), Seam.parseEvent("""{"type":"notice","kind":"full"}"""))
    }

    @Test
    fun `bad json or unknown kind yields null`() {
        assertNull(Seam.parseEvent("not json"))
        assertNull(Seam.parseEvent("""{"type":"notice","kind":"whatever"}"""))
        assertNull(Seam.parseEvent("""{"type":"mystery"}"""))
    }

    @Test
    fun `commands carry the seam shape`() {
        assertTrue(Seam.authCommand("tok").contains(""""token":"tok""""))
        assertTrue(Seam.startCommand("sys").contains(""""translate":"ct2""""))
        assertTrue(Seam.stopCommand().contains(""""type":"stop""""))
    }

    @Test
    fun `pcm16 converts to little-endian f32`() {
        val bytes = Seam.pcm16ToF32le(shortArrayOf(0x4000, -0x4000), 2)
        val floats = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer()
        assertEquals(0.5f, floats.get(0), 0f)
        assertEquals(-0.5f, floats.get(1), 0f)
    }
}
