$ErrorActionPreference = 'Stop'
$app = Join-Path (Join-Path (Join-Path $env:USERPROFILE '.rog-status-light') 'app') 'ROGStatusLight.exe'
if (-not (Test-Path -LiteralPath $app)) { throw '请先运行“安装”。' }
Start-Process -FilePath $app -ArgumentList '--show'
