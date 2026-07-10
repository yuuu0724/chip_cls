@echo off
REM Build chipocr desktop app (PyInstaller onedir, GPU).
REM Output: dist\chipocr\chipocr.exe

setlocal

cd /d "%~dp0"

echo === Clean old build output ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo === Run PyInstaller ===
python -m PyInstaller chipocr.spec --clean --noconfirm
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

set "APP_DIR=%CD%\dist\chipocr"
set "INTERNAL_DIR=%APP_DIR%\_internal"
set "BUILD_INFO=%APP_DIR%\BUILD_INFO.txt"
set "MISSING="

echo === Verify required files ===
call :require_file "%APP_DIR%\chipocr.exe"
call :require_file "%INTERNAL_DIR%\onnx\chip\chip_best.onnx"
call :require_file "%INTERNAL_DIR%\onnx\chip\chip_best_opset21.onnx"
call :require_file "%INTERNAL_DIR%\onnx\det\inference.onnx"
call :require_file "%INTERNAL_DIR%\onnx\cls\inference.onnx"
call :require_file "%INTERNAL_DIR%\onnx\rec\inference.onnx"
call :require_file "%INTERNAL_DIR%\config\templates.json"
call :require_file "%INTERNAL_DIR%\config\logo.png"

if defined MISSING (
    echo.
    echo [ERROR] Required build files are missing. Check chipocr.spec.
    exit /b 1
)

echo === Write BUILD_INFO.txt ===
echo ChipOCR build>"%BUILD_INFO%"
echo Built at: %DATE% %TIME%>>"%BUILD_INFO%"
echo Source: %CD%>>"%BUILD_INFO%"
echo Command: python -m PyInstaller chipocr.spec --clean --noconfirm>>"%BUILD_INFO%"
echo Includes:>>"%BUILD_INFO%"
echo - onnx/chip/chip_best.onnx>>"%BUILD_INFO%"
echo - onnx/chip/chip_best_opset21.onnx>>"%BUILD_INFO%"
echo - onnx/det, onnx/cls, onnx/rec>>"%BUILD_INFO%"
echo - config/templates.json>>"%BUILD_INFO%"
echo - config/logo.png>>"%BUILD_INFO%"

echo.
echo === Build complete ===
echo Output: %APP_DIR%\chipocr.exe
echo Build info: %BUILD_INFO%
echo Send the whole dist\chipocr folder to the customer.

endlocal
exit /b 0

:require_file
if not exist "%~1" (
    echo [MISSING] %~1
    set "MISSING=1"
)
exit /b 0
