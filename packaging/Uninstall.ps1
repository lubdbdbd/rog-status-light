$ErrorActionPreference = 'Stop'
$currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [System.Security.Principal.WindowsPrincipal]::new($currentIdentity)
if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',([char]34 + $PSCommandPath + [char]34))
    $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList $arguments -PassThru -Wait
    exit $elevated.ExitCode
}
$dataFolder = [System.IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.rog-status-light'))
$expectedParent = [System.IO.Path]::GetFullPath($env:USERPROFILE)
if ((Split-Path -Path $dataFolder -Parent) -ne $expectedParent -or (Split-Path -Path $dataFolder -Leaf) -ne '.rog-status-light') { throw '卸载目录校验失败。' }
if (-not (Test-Path -LiteralPath $dataFolder)) { Write-Output 'ROG 状态灯未安装。'; return }
& (Join-Path $dataFolder 'Stop.ps1')
for ($attempt=0; $attempt -lt 50; $attempt++) {
    if (-not (Get-Process -Name ROGStatusLight -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 200
}
if (Get-Process -Name ROGStatusLight -ErrorAction SilentlyContinue) { throw '请先从托盘退出程序，再卸载。' }
$hookFile = Join-Path (Join-Path $env:USERPROFILE '.codex') 'hooks.json'
if (Test-Path -LiteralPath $hookFile) {
    $hooks = Get-Content -Raw -LiteralPath $hookFile | ConvertFrom-Json
    foreach ($event in $hooks.hooks.PSObject.Properties) {
        $retained = @($event.Value | Where-Object {
            -not (@($_.hooks | Where-Object { $_.command -like '*ROGStatusLight.exe*--hook*' }).Count)
        })
        $event.Value = $retained
    }
    [System.IO.File]::WriteAllText($hookFile,($hooks | ConvertTo-Json -Depth 40),[System.Text.UTF8Encoding]::new($false))
}
Get-AppxPackage -Name 'Local.ROGStatusLight' | Remove-AppxPackage
$receiptPath = Join-Path $dataFolder 'install-receipt.json'
if (Test-Path -LiteralPath $receiptPath) {
    $receipt = Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json
    foreach ($storeInfo in @(@('TrustedPeople','LocalMachine'),@('TrustedPeople','CurrentUser'),@('My','CurrentUser'))) {
        $store = [System.Security.Cryptography.X509Certificates.X509Store]::new($storeInfo[0],$storeInfo[1])
        $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        try {
            $certificates = @($store.Certificates | Where-Object { $_.Thumbprint -eq $receipt.CertificateThumbprint -and $_.Subject -eq 'CN=ROG Status Light Local' })
            foreach ($certificate in $certificates) { $store.Remove($certificate) }
        } finally { $store.Close() }
    }
}
$startupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'ROG 状态灯.lnk'
if (Test-Path -LiteralPath $startupLink) { Remove-Item -LiteralPath $startupLink }
$menuFolder = [System.IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('Programs')) 'ROG 状态灯'))
if ((Split-Path -Path $menuFolder -Leaf) -eq 'ROG 状态灯' -and (Test-Path -LiteralPath $menuFolder)) { Remove-Item -LiteralPath $menuFolder -Recurse -Force }
Remove-Item -LiteralPath $dataFolder -Recurse -Force
Write-Output 'ROG 状态灯已卸载。'
