# Bangun APK Monitoring Stok memakai JDK/SDK yang sama dengan aplikasi Comic.
param([switch]$ModeDebug)

$ErrorActionPreference = 'Stop'
$mobileRoot = Split-Path -Parent $PSScriptRoot
Set-Location $mobileRoot

$jdk = 'D:\Android\jdk-21'
$sdk = 'D:\Android\sdk'
$gradleHome = 'D:\Android\gradle-home'
if (-not (Test-Path $jdk)) { throw "JDK tidak ditemukan di $jdk" }
if (-not (Test-Path $sdk)) { throw "Android SDK tidak ditemukan di $sdk" }

$version = (Get-Content (Join-Path $mobileRoot 'package.json') -Raw | ConvertFrom-Json).version
if ($version -notmatch '^\d{1,6}\.\d{1,2}\.\d{1,2}$') {
    throw "Versi $version harus major.minor.patch dengan minor/patch di bawah 100"
}

if (-not $ModeDebug) {
    $keyProperties = if ($env:MONITORING_STOK_KEY_PROPERTIES) {
        $env:MONITORING_STOK_KEY_PROPERTIES
    } else {
        'D:\CODE\key\monitoring-stok\key.properties'
    }
    if (-not (Test-Path $keyProperties)) {
        throw "Kunci rilis khusus Monitoring Stok belum tersedia: $keyProperties. Bangun -ModeDebug untuk uji HP."
    }
    $env:MONITORING_STOK_KEY_PROPERTIES = [System.IO.Path]::GetFullPath($keyProperties)
}

$env:JAVA_HOME = $jdk
$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk
$env:GRADLE_USER_HOME = $gradleHome
$env:PATH = "$jdk\bin;$env:PATH"
$sdkProps = $sdk.Replace('\', '\\')
Set-Content -Path (Join-Path $mobileRoot 'android\local.properties') -Value "sdk.dir=$sdkProps" -Encoding ascii

npm run android:sync
if ($LASTEXITCODE -ne 0) { throw 'Bundel web atau sinkron Capacitor gagal' }

$task = if ($ModeDebug) { 'assembleDebug' } else { 'assembleRelease' }
$source = if ($ModeDebug) {
    Join-Path $mobileRoot 'android\app\build\outputs\apk\debug\app-debug.apk'
} else {
    Join-Path $mobileRoot 'android\app\build\outputs\apk\release\app-release.apk'
}

Push-Location (Join-Path $mobileRoot 'android')
try {
    $env:GRADLE_OPTS = '-Xmx1g -Dfile.encoding=UTF-8'
    .\gradlew.bat $task "-PstokVersion=$version" --no-daemon --max-workers=2 '-Dorg.gradle.jvmargs=-Xmx1g'
    if ($LASTEXITCODE -ne 0) { throw 'Build Gradle gagal' }
} finally {
    Pop-Location
}

if (-not (Test-Path $source)) { throw "APK hasil build tidak ditemukan: $source" }
$dist = Join-Path $mobileRoot 'dist-apk'
New-Item -ItemType Directory -Force $dist | Out-Null
$suffix = if ($ModeDebug) { '-debug' } else { '' }
$target = Join-Path $dist "MonitoringStok-$version$suffix.apk"
Copy-Item $source $target -Force
Write-Host "APK selesai: $target"
