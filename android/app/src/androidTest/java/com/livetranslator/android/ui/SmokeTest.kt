package com.livetranslator.android.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.livetranslator.android.account.AccountRepo
import com.livetranslator.android.account.MemoryTokenStore
import com.livetranslator.android.core.SharedPrefsStore
import com.livetranslator.android.listen.ListenBus
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** UI 冒烟：登录屏与面板的核心元素渲染。E2E（注册→开听→字幕浮窗）由模拟器手驱脚本覆盖。 */
@RunWith(AndroidJUnit4::class)
class SmokeTest {
    @get:Rule
    val compose = createComposeRule()

    @Test
    fun loginScreenShowsCoreControls() {
        compose.setContent {
            LoginScreen(
                repo = AccountRepo("http://127.0.0.1:1"), // 不可达也行：不点提交
                tokenStore = MemoryTokenStore(),
                onLoggedIn = {},
            )
        }
        compose.onNodeWithText("注册并登录").assertIsDisplayed()
        compose.onNodeWithText("登录").assertIsDisplayed()
        compose.onNodeWithText("记住我（这台手机保持登录）").assertIsDisplayed()
    }

    @Test
    fun panelShowsSourceModeAndToggles() {
        ListenBus.reset()
        val context = androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().targetContext
        compose.setContent {
            PanelScreen(
                email = "a@b.c",
                prefsStore = SharedPrefsStore(context),
                onOpenProfile = {},
            )
        }
        compose.onNodeWithText("a@b.c").assertIsDisplayed()
        compose.onNodeWithText("开听").assertIsDisplayed()
        compose.onNodeWithText("停止").assertIsDisplayed()
        compose.onNodeWithText("双语").assertIsDisplayed()
        compose.onNodeWithText("音源").assertIsDisplayed()
    }
}
