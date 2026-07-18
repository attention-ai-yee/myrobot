[Console]::OutputEncoding = [Text.Encoding]::UTF8

Write-Host "[1/4] Stopping old processes..." -ForegroundColor Cyan
$killed = 0
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*bridge.py*' -or $_.CommandLine -like '*feishu_listener*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $killed++ }
Write-Host "      stopped $killed process(es)."

Write-Host "[2/4] Starting bridge + feishu listener..." -ForegroundColor Cyan
$dir = 'C:\Users\honin\Documents\Kimi\Workspaces\myrobot\stackchan-mcp'
$py  = Join-Path $dir '.venv\Scripts\pythonw.exe'
Start-Process -FilePath $py -ArgumentList 'bridge.py' -WorkingDirectory $dir
Start-Process -FilePath $py -ArgumentList 'feishu_listener.py' -WorkingDirectory $dir
Write-Host "      waiting for them to come online..."
Start-Sleep -Seconds 14

Write-Host "[3/4] Latest bridge log:" -ForegroundColor Cyan
Write-Host "--------------------------------------------------------"
Get-Content (Join-Path $dir 'bridge.log') -Tail 4 -Encoding UTF8
Write-Host "--------------------------------------------------------"

Write-Host "[4/4] Latest feishu listener log:" -ForegroundColor Cyan
Write-Host "--------------------------------------------------------"
Get-Content (Join-Path $dir 'feishu_listener.log') -Tail 3 -Encoding UTF8
Write-Host "--------------------------------------------------------"
Write-Host ""
Write-Host "HEALTHY = bridge shows '26 tools' AND listener shows 'connected to wss://msg-frontier.feishu.cn'" -ForegroundColor Green
