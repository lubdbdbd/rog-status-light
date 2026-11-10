param([Parameter(Mandatory=$true)][string]$Workspace)
$ErrorActionPreference = 'Stop'
$repoFolder = Split-Path -Parent $PSScriptRoot
$stateFolder = Join-Path $env:USERPROFILE '.rog-status-light\publishing'
New-Item -ItemType Directory -Path $stateFolder -Force | Out-Null
$configPath = Join-Path $stateFolder 'config.json'
$pythonPath = (Get-Command python.exe).Source
$pythonwPath = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwPath)) { throw '未找到后台 Python 启动程序。' }
$gitPath = (Get-Command git.exe).Source
@{workspace=[IO.Path]::GetFullPath($Workspace);repository=$repoFolder;python=$pythonPath;git=$gitPath} |
    ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding utf8
& $pythonPath (Join-Path $PSScriptRoot 'sync_local.py') --config $configPath --initialize
if ($LASTEXITCODE -ne 0) { throw '自动发布初始化失败。' }
$arguments = '"'+(Join-Path $PSScriptRoot 'sync_local.py')+'" --config "'+$configPath+'"'
$action = New-ScheduledTaskAction -Execute $pythonwPath -Argument $arguments -WorkingDirectory $repoFolder
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'ROGStatusLight-AutoPublish' -Action $action -Trigger $trigger -Settings $settings -Description '同步已完成的 ROG 状态灯安装包源码，由 GitHub 自动构建和发布。' -Force | Out-Null
Write-Output '自动发布已启用：每 5 分钟检查完成的安装包。'
