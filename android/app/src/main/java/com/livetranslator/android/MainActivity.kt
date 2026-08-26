package com.livetranslator.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.livetranslator.android.ui.LoginScreen
import com.livetranslator.android.ui.OnboardingScreen
import com.livetranslator.android.ui.PanelScreen
import com.livetranslator.android.ui.ProfileScreen
import com.livetranslator.android.ui.overlayGranted

/** 单 Activity：首启引导 → 登录 → 面板/个人中心。返回键就是系统返回（面板退后台=藏，ADR 0038）。 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = application as App
        setContent {
            var screen by remember { mutableStateOf(Screen.ONBOARD) }
            var email by remember { mutableStateOf<String?>(null) }
            var onboarded by remember {
                mutableStateOf(getSharedPreferences("boot", MODE_PRIVATE).getBoolean("onboarded", false))
            }
            if (!onboarded) {
                OnboardingScreen(onDone = {
                    getSharedPreferences("boot", MODE_PRIVATE).edit().putBoolean("onboarded", true).apply()
                    onboarded = true
                })
            } else {
                val saved = email ?: app.tokenStore.load()?.first
                when {
                    screen == Screen.PROFILE && saved != null -> ProfileScreen(
                        email = saved,
                        repo = app.accountRepo,
                        tokenStore = app.tokenStore,
                        onBack = { screen = Screen.PANEL },
                        onLoggedOut = {
                            email = null
                            screen = Screen.PANEL
                        },
                    )
                    saved == null -> LoginScreen(
                        repo = app.accountRepo,
                        tokenStore = app.tokenStore,
                        onLoggedIn = { m -> email = m },
                    )
                    else -> PanelScreen(
                        email = saved,
                        prefsStore = app.prefsStore,
                        onOpenProfile = { screen = Screen.PROFILE },
                    )
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // 从悬浮窗设置页返回：权限到手即引导完成（OnboardingScreen 内展示 ✅）
    }

    enum class Screen { ONBOARD, PANEL, PROFILE }
}
