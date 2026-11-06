@echo off
rem ============================================================
rem 启动项目.bat —— 光影影城 · 在线选座购票系统 一键启动脚本
rem （项目规范第 6 节：UTF-8、CRLF；检查 Python -> 缺 Flask 用
rem  清华镜像安装 -> 没有数据库就初始化 -> python app.py --open）
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"
title 光影影城 · 在线选座购票系统

echo ============================================================
echo   光影影城 · 在线选座购票系统
echo   LUMIERE CINEMA - 每一帧都值得在大银幕上看
echo ============================================================
echo.

rem ---- [1/4] 检查 Python ----
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python。请先安装 Python 3.10 或更高版本，
    echo        安装时勾选 "Add Python to PATH"，然后重新运行本脚本。
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [1/4] 已找到 Python %PYVER%

rem ---- [2/4] 检查 Flask，缺失则用清华镜像安装 ----
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [2/4] 未检测到 Flask，正在通过清华镜像安装（requirements.txt: Flask^>=3.0）...
    python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "Flask>=3.0"
    if errorlevel 1 (
        echo [错误] Flask 安装失败，请检查网络后重新运行本脚本。
        echo.
        pause
        exit /b 1
    )
    echo [2/4] Flask 安装完成。
) else (
    echo [2/4] Flask 已安装。
)

rem ---- [3/4] 没有数据库就初始化（写入演示数据） ----
if not exist "instance\guangying.db" (
    echo [3/4] 首次运行，正在初始化演示数据库...
    python init_db.py
    if errorlevel 1 (
        echo [错误] 数据库初始化失败，请把上方报错信息发给开发同学。
        echo.
        pause
        exit /b 1
    )
) else (
    echo [3/4] 数据库已存在，跳过初始化。（如需重置演示数据：删除 instance 文件夹后重跑）
)

rem ---- [4/4] 启动服务并自动打开浏览器 ----
echo [4/4] 启动服务：http://127.0.0.1:5000/  （按 Ctrl+C 停止）
echo.
python app.py --open

echo.
echo 服务已停止。
pause
