package com.livetranslator.android.account

import android.content.Context
import java.io.File
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** 登录凭据的存放（ADR 0027 语义）：勾「记住我」token 才跨次留存（AndroidKeyStore 加密落盘）；
 *  不勾只活在内存，进程被杀即掉登录（安卓端即「划掉即登出」，ADR 0038）。
 *  接口化以便 JVM 单测；Keystore 壳很薄，设备端由 E2E 覆盖。 */

interface TokenStore {
    /** 当前登录（email + token）；null = 没登录或没被记住 */
    fun load(): Pair<String, String>?

    /** rememberMe=false 时 token 不落盘，只在本次进程内有效 */
    fun save(email: String, token: String, rememberMe: Boolean)

    fun clear()
}

/** 进程内存 + 可选加密落盘的组合。 */
class SessionTokenStore(context: Context) : TokenStore {
    private val appContext = context.applicationContext
    private var inMemory: Pair<String, String>? = null

    override fun load(): Pair<String, String>? {
        inMemory?.let { return it }
        val persisted = KeystoreFile(appContext).read() ?: return null
        inMemory = persisted
        return persisted
    }

    override fun save(email: String, token: String, rememberMe: Boolean) {
        inMemory = email to token
        if (rememberMe) {
            KeystoreFile(appContext).write(email, token)
        } else {
            KeystoreFile(appContext).delete()
        }
    }

    override fun clear() {
        inMemory = null
        KeystoreFile(appContext).delete()
    }
}

/** AndroidKeyStore AES-GCM 加密的凭据文件。密钥在系统 Keystore 里，
 *  文件被拷走也解不开；Keystore 本身清掉（卸载/恢复出厂）等同登出。 */
private class KeystoreFile(context: Context) {
    private val file = File(context.filesDir, "session.bin")

    fun read(): Pair<String, String>? {
        if (!file.isFile) return null
        return try {
            val blob = file.readBytes()
            val iv = blob.copyOfRange(0, 12)
            val ct = blob.copyOfRange(12, blob.size)
            val cipher = Cipher.getInstance(TRANSFORM)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
            val plain = String(cipher.doFinal(ct), Charsets.UTF_8)
            val sep = plain.indexOf('\n')
            plain.substring(0, sep) to plain.substring(sep + 1)
        } catch (_: Exception) {
            null
        }
    }

    fun write(email: String, token: String) {
        try {
            val cipher = Cipher.getInstance(TRANSFORM)
            cipher.init(Cipher.ENCRYPT_MODE, key())
            val ct = cipher.doFinal("$email\n$token".toByteArray(Charsets.UTF_8))
            val iv = cipher.iv
            file.writeBytes(iv + ct)
        } catch (_: Exception) {
            // Keystore 异常不致命：只是这次记不住，下次重登
        }
    }

    fun delete() {
        file.delete()
    }

    private fun key(): SecretKey {
        val ks = java.security.KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyPropertiesCompat.AES, ANDROID_KEYSTORE)
        gen.init(
            android.security.keystore.KeyGenParameterSpec.Builder(
                ALIAS,
                android.security.keystore.KeyProperties.PURPOSE_ENCRYPT or android.security.keystore.KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(android.security.keystore.KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(android.security.keystore.KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return gen.generateKey()
    }

    private object KeyPropertiesCompat {
        const val AES = "AES"
    }

    companion object {
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val ALIAS = "livetranslator_session"
        private const val TRANSFORM = "AES/GCM/NoPadding"
    }
}

/** 单测用的纯内存实现：disk 模拟加密文件、memory 模拟进程内存。 */
class MemoryTokenStore : TokenStore {
    private var disk: Pair<String, String>? = null
    private var memory: Pair<String, String>? = null

    override fun load(): Pair<String, String>? = memory ?: disk

    override fun save(email: String, token: String, rememberMe: Boolean) {
        memory = email to token
        disk = if (rememberMe) email to token else null
    }

    override fun clear() {
        memory = null
        disk = null
    }

    /** 模拟进程被杀：内存丢失，落盘的凭据按 rememberMe 决定去留。 */
    fun killProcess() {
        memory = null
    }
}
