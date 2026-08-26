val hostedHttp = (findProperty("LIVE_HOSTED_HTTP") as String?) ?: "http://10.0.2.2:8787"
val hostedWs = (findProperty("LIVE_HOSTED_WS") as String?) ?: "ws://10.0.2.2:8787/listen"

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "com.livetranslator.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.livetranslator.android"
        minSdk = 29
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        // 源写死不可改（ADR 0031）；调试构建连本机模拟器宿主（10.0.2.2 = 宿主 loopback）
        buildConfigField("String", "HOSTED_HTTP", "\"" + hostedHttp + "\"")
        buildConfigField("String", "HOSTED_WS", "\"" + hostedWs + "\"")
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    testOptions {
        unitTests.isIncludeAndroidResources = true
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.okhttp)
    implementation(libs.androidx.lifecycle.runtime.compose)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.org.json)
    testImplementation(libs.robolectric)
    testImplementation(libs.coroutines.test)

    androidTestImplementation(libs.androidx.test.junit)
    androidTestImplementation(libs.espresso.core)
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
