package com.livetranslator.android

import android.app.Application
import com.livetranslator.android.account.AccountRepo
import com.livetranslator.android.account.SessionTokenStore
import com.livetranslator.android.core.SharedPrefsStore
import com.livetranslator.android.listen.PrefsOwner

class App : Application(), PrefsOwner {
    lateinit var accountRepo: AccountRepo
    lateinit var tokenStore: SessionTokenStore
    override val prefsStore by lazy { SharedPrefsStore(this) }

    override fun onCreate() {
        super.onCreate()
        accountRepo = AccountRepo(BuildConfig.HOSTED_HTTP)
        tokenStore = SessionTokenStore(this)
    }
}
