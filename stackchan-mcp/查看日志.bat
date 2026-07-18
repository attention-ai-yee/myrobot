@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -Path 'C:\Users\honin\Documents\Kimi\Workspaces\myrobot\stackchan-mcp\bridge.log' -Tail 30 -Wait -Encoding UTF8"
