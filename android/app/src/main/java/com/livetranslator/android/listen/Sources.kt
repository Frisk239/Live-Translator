package com.livetranslator.android.listen

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build

/** 音源发现（ADR 0037）：面板列出「可能正在出声的 App」+「全部可抓的声音」兜底。
 *  公开 API 拿不到「正在播放」的实时 UID 关联，第一版列媒体类 App（包可见性 <queries>），
 *  选择经过 addMatchingUid 在采集配置层过滤；抓不到对方 opt-out 是静音，靠能量提示。 */

data class AudioSource(
    val label: String,
    val packageName: String?,
    val uid: Int?,
    /** 调试源：不过滤（含本 App 自己的声音），E2E 自播素材走它 */
    val includeSelfDebug: Boolean = false,
) {
    companion object {
        const val ALL_LABEL = "全部可抓的声音"
        val ALL = AudioSource(ALL_LABEL, null, null)
        const val DEBUG_ALL_LABEL = "（调试）全部声音含本 App"
        val DEBUG_ALL = AudioSource(DEBUG_ALL_LABEL, null, null, includeSelfDebug = true)
    }
}

object Sources {
    fun list(context: Context): List<AudioSource> {
        val pm = context.packageManager
        val self = context.packageName
        val seen = LinkedHashMap<String, AudioSource>()
        listOf(
            Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_APP_MUSIC),
            Intent("android.media.browse.MediaBrowserService"),
        ).forEach { probe ->
            val infos = if (Build.VERSION.SDK_INT >= 33) {
                pm.queryIntentActivities(probe, PackageManager.ResolveInfoFlags.of(0))
            } else {
                @Suppress("DEPRECATION") pm.queryIntentActivities(probe, 0)
            }
            for (info in infos) {
                val pkg = info.activityInfo?.packageName ?: continue
                if (pkg == self || seen.containsKey(pkg)) continue
                val uid = try {
                    if (Build.VERSION.SDK_INT >= 33) pm.getPackageUid(pkg, PackageManager.PackageInfoFlags.of(0))
                    else @Suppress("DEPRECATION") pm.getPackageUid(pkg, 0)
                } catch (_: Exception) {
                    continue
                }
                seen[pkg] = AudioSource(
                    label = info.loadLabel(pm)?.toString() ?: pkg,
                    packageName = pkg,
                    uid = uid,
                )
            }
        }
        val base = listOf(AudioSource.ALL) + seen.values.sortedBy { it.label }
        return if (com.livetranslator.android.BuildConfig.DEBUG) listOf(AudioSource.DEBUG_ALL) + base else base
    }
}
