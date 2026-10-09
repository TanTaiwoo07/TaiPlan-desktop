@echo off
rem TaiPlan —— 调试启动入口（会保留一个终端窗口，便于查看日志）
rem 正式启动请双击 start_todo_silent.vbs
cd /d "%~dp0"
.venv\Scripts\python.exe" -m taiplan.desktop_runtime --debug
