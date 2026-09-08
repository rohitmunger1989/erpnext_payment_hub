$ErrorActionPreference = "Stop"

$Target = "C:\ERPNextPaymentHub"
$Script = Join-Path $Target "payment_hub_device_helper.py"

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item "$PSScriptRoot\payment_hub_device_helper.py" $Script -Force

$Python = (Get-Command python.exe -ErrorAction Stop).Source

$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`""
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName "ERPNext Payment Hub Device Helper" `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Force

Start-ScheduledTask -TaskName "ERPNext Payment Hub Device Helper"

Write-Host "Installed. Test: http://127.0.0.1:8765/device"
