# Buat satu kunci rilis permanen untuk flowStok. Jalankan hanya sekali.
param([string]$KeyDirectory = 'D:\CODE\key\monitoring-stok')

$ErrorActionPreference = 'Stop'
$keytool = 'D:\Android\jdk-21\bin\keytool.exe'
$keystore = Join-Path $KeyDirectory 'monitoring-stok-release.jks'
$properties = Join-Path $KeyDirectory 'key.properties'

if (-not (Test-Path $keytool)) { throw "keytool tidak ditemukan di $keytool" }
if ((Test-Path $keystore) -or (Test-Path $properties)) {
    if ((Test-Path $keystore) -and (Test-Path $properties)) {
        Write-Host "Kunci rilis sudah tersedia di $KeyDirectory"
        exit 0
    }
    throw "Kunci rilis tidak lengkap. Jangan timpa berkas yang ada; pulihkan pasangan keystore dan key.properties."
}

New-Item -ItemType Directory -Force -Path $KeyDirectory | Out-Null
$secretBytes = New-Object byte[] 36
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($secretBytes) } finally { $rng.Dispose() }
$password = [Convert]::ToBase64String($secretBytes).TrimEnd('=')
$alias = 'monitoring-stok'

& $keytool -genkeypair -v -keystore $keystore -storepass $password -keypass $password `
    -alias $alias -keyalg RSA -keysize 4096 -validity 10000 `
    -dname 'CN=flowStok, OU=Gudang, O=Citra Mahkota, C=ID'
if ($LASTEXITCODE -ne 0) { throw 'Pembuatan keystore gagal' }

$storePath = ([IO.Path]::GetFullPath($keystore) -replace '\\', '/')
[IO.File]::WriteAllLines($properties, @(
    "storeFile=$storePath"
    "storePassword=$password"
    "keyAlias=$alias"
    "keyPassword=$password"
), [Text.UTF8Encoding]::new($false))

# Batasi rahasia pada akun Windows yang menjalankan script.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
foreach ($path in @($keystore, $properties)) {
    $acl = Get-Acl -LiteralPath $path
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
        $identity, 'FullControl', 'Allow'
    )))
    Set-Acl -LiteralPath $path -AclObject $acl
}

Write-Host "Kunci rilis permanen dibuat di $KeyDirectory"
Write-Host 'Simpan backup folder tersebut di media aman. Kehilangan kunci membuat pembaruan APK tidak dapat dipasang.'
