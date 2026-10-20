@echo off
chcp 65001 >nul
echo ========================================================
echo       Flow2API Captcha Worker 扩展插件一键更新助手
echo ========================================================
echo.

set SERVER_URL=http://192.168.31.63:8000
if not "%~1"=="" set SERVER_URL=%~1

echo [1/3] 正在从 Flow2API 服务端获取最新插件安装包 (%SERVER_URL%)...
curl -fsSL "%SERVER_URL%/api/plugin/download" -o flow2api-extension.zip
if %errorlevel% neq 0 (
    echo.
    echo [错误] 下载失败！无法连接到 %SERVER_URL%/api/plugin/download
    echo 提示：若是远程公网访问，请在运行时指定地址，例如: update.bat http://your-domain:8000
    echo.
    pause
    exit /b 1
)

echo [2/3] 正在解压并覆盖本地插件目录...
tar -xf flow2api-extension.zip -C .
del flow2api-extension.zip

echo [3/3] 插件文件已更新为最新版本！
echo       浏览器插件后台将自动侦测并重新加载生效。
echo.
timeout /t 3 >nul
