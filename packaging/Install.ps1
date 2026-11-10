param([switch]$NoStart)
$ErrorActionPreference = 'Stop'
$sourceFolder = $PSScriptRoot
$certificatePath = Join-Path $sourceFolder 'ROGStatusLight.cer'
$publicCertificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($certificatePath)
if ($publicCertificate.Subject -ne 'CN=ROG Status Light Local') { throw '安装证书不匹配。' }
$trusted = @(Get-ChildItem Cert:\LocalMachine\TrustedPeople | Where-Object { $_.Thumbprint -eq $publicCertificate.Thumbprint }).Count -gt 0
if (-not $trusted) {
    $principal = [System.Security.Principal.WindowsPrincipal]::new([System.Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',([char]34+$PSCommandPath+[char]34))
        if ($NoStart) { $arguments += '-NoStart' }
        $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList $arguments -PassThru -Wait
        exit $elevated.ExitCode
    }
    $trustStore = [System.Security.Cryptography.X509Certificates.X509Store]::new('TrustedPeople','LocalMachine')
    $trustStore.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
    try { $trustStore.Add($publicCertificate) } finally { $trustStore.Close() }
}
$dataFolder = Join-Path $env:USERPROFILE '.rog-status-light'
$appFolder = Join-Path $dataFolder 'app'
$sourceApp = Join-Path $sourceFolder 'Program'
if (-not (Test-Path -LiteralPath $sourceApp)) { $sourceApp = Join-Path $sourceFolder 'app' }
if (-not (Test-Path -LiteralPath (Join-Path $sourceApp 'ROGStatusLight.exe'))) { throw '请先完整解压安装包。' }
if (Get-Process -Name ROGStatusLight -ErrorAction SilentlyContinue) {
    & (Join-Path $sourceFolder 'Stop.ps1')
    for ($attempt=0; $attempt -lt 35; $attempt++) {
        if (-not (Get-Process -Name ROGStatusLight -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 200
    }
    if (Get-Process -Name ROGStatusLight -ErrorAction SilentlyContinue) { throw '请先从托盘退出 ROG 状态灯。' }
}
New-Item -ItemType Directory -Path $dataFolder,$appFolder -Force | Out-Null
if ([System.IO.Path]::GetFullPath($sourceApp) -ne [System.IO.Path]::GetFullPath($appFolder)) {
    Get-ChildItem -LiteralPath $sourceApp | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $appFolder -Recurse -Force }
}
foreach ($name in @('Install.ps1','Stop.ps1','Uninstall.ps1','ROGStatusLight.msix','ROGStatusLight.cer')) {
    $source = Join-Path $sourceFolder $name
    $target = Join-Path $dataFolder $name
    if ([System.IO.Path]::GetFullPath($source) -ne [System.IO.Path]::GetFullPath($target)) {
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
}
$configPath = Join-Path $dataFolder 'config.json'
if (-not (Test-Path -LiteralPath $configPath)) {
    $legacyConfig = Join-Path $env:LOCALAPPDATA 'ROGStatusLight\config.json'
    if (Test-Path -LiteralPath $legacyConfig) { Copy-Item -LiteralPath $legacyConfig -Destination $configPath }
    else { Copy-Item -LiteralPath (Join-Path $sourceFolder 'config.json') -Destination $configPath }
}
$bluetoothSnapshot = Join-Path $dataFolder 'bluetooth-lighting-restore.json'
if (-not (Test-Path -LiteralPath $bluetoothSnapshot)) {
    $sourceSnapshot = Join-Path $sourceFolder 'bluetooth-lighting-restore.json'
    if (Test-Path -LiteralPath $sourceSnapshot) { Copy-Item -LiteralPath $sourceSnapshot -Destination $bluetoothSnapshot }
}
$sourceValidation = Join-Path $sourceFolder 'bluetooth-validation.json'
$targetValidation = Join-Path $dataFolder 'bluetooth-validation.json'
if ((Test-Path -LiteralPath $sourceValidation) -and
    [System.IO.Path]::GetFullPath($sourceValidation) -ne [System.IO.Path]::GetFullPath($targetValidation)) {
    Copy-Item -LiteralPath $sourceValidation -Destination $targetValidation -Force
}
$installed = Get-AppxPackage -Name 'Local.ROGStatusLight'
if (-not $installed -or [version]$installed.Version -lt [version]'1.5.0.0') {
    Add-AppxPackage -Path (Join-Path $sourceFolder 'ROGStatusLight.msix') -ExternalLocation $appFolder
    $installed = Get-AppxPackage -Name 'Local.ROGStatusLight'
}
if (-not $installed -or [version]$installed.Version -lt [version]'1.5.0.0') { throw '后台灯光权限注册失败。' }
@{CertificateThumbprint=$publicCertificate.Thumbprint;PackageFullName=$installed.PackageFullName;AppFolder=$appFolder;InstalledAt=(Get-Date).ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dataFolder 'install-receipt.json') -Encoding utf8
$executable = Join-Path $appFolder 'ROGStatusLight.exe'
$setupProcess = Start-Process -FilePath $executable -ArgumentList '--configure-hooks' -WindowStyle Hidden -PassThru -Wait
if ($setupProcess.ExitCode -ne 0) { Write-Warning '事件接入状态请查看 hooks-setup.log。' }
$shell = New-Object -ComObject WScript.Shell
$startMenuFolder = Join-Path ([Environment]::GetFolderPath('Programs')) 'ROG 状态灯'
New-Item -ItemType Directory -Path $startMenuFolder -Force | Out-Null
$shortcut = $shell.CreateShortcut((Join-Path $startMenuFolder 'ROG 状态灯.lnk'))
$shortcut.TargetPath = $executable
$shortcut.Arguments = '--show'
$shortcut.WorkingDirectory = $appFolder
$shortcut.Save()
$startupLink = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'ROG 状态灯.lnk'))
$startupLink.TargetPath = $executable
$startupLink.WorkingDirectory = $appFolder
$startupLink.Save()
if (-not $NoStart) { Start-Process -FilePath $executable -WindowStyle Hidden }
Write-Output 'ROG 状态灯已安装，启动后常驻托盘。'
