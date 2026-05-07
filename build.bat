@echo off
REM 打包 chipocr 桌面应用 (onedir, GPU)
REM 产物路径: dist\chipocr\chipocr.exe

setlocal

cd /d "%~dp0"

echo === 清理旧产物 ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo === 运行 PyInstaller ===
python -m PyInstaller chipocr.spec --clean --noconfirm
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller 打包失败
    exit /b 1
)

echo.
echo === 打包完成 ===
echo 产物: %CD%\dist\chipocr\chipocr.exe
echo 把整个 dist\chipocr\ 文件夹发给客户即可。

endlocal
