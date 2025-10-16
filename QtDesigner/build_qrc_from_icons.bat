@echo on
setlocal EnableExtensions EnableDelayedExpansion

REM =====================================================================
REM Build icons.qrc (for Designer) and compile to Python module icons_rc.py
REM Run this .bat FROM: QuickMaterials\QtDesigner\
REM   - Sources:   QuickMaterials\QtDesigner\icons\  (recursive)
REM   - Output QRC: QuickMaterials\QtDesigner\icons.qrc
REM   - Output PY : QuickMaterials\icons_rc.py  (one level up for easy import)
REM =====================================================================

cd /d "%~dp0"
echo.
echo [INFO] Working dir: "%CD%"

set "ICONS_DIR=icons"
set "QRC=icons.qrc"
set "OUT_PY_PARENT=..\icons_rc.py"   REM Python module beside your quick_materials.py

if not exist "%ICONS_DIR%" (
  echo [ERROR] "%ICONS_DIR%" not found. Create it and add .png files.
  pause
  exit /b 1
)

echo.
echo [STEP 1] Generating "%QRC%" from all PNGs under "%ICONS_DIR%"...
del "%QRC%" 2>nul

> "%QRC%" echo ^<RCC^>
>> "%QRC%" echo   ^<qresource prefix="/icons"^>

REM Use CWD with trailing slash so the replace is unambiguous
set "CWD=%CD%\"
set COUNT=0
for /r "%ICONS_DIR%" %%F in (*.png) do (
  set "FULL=%%~fF"
  set "REL=!FULL:%CWD%=!"
  set "REL_UNIX=!REL:\=/!"
  set "ALIAS=%%~nxF"
  >> "%QRC%" echo     ^<file alias="!ALIAS!"^>!REL_UNIX!^</file^>
  set /a COUNT+=1
)

>> "%QRC%" echo   ^</qresource^>
>> "%QRC%" echo ^</RCC^>

if %COUNT% EQU 0 (
  echo [WARN] No PNGs found in "%ICONS_DIR%".
) else (
  for %%A in ("%QRC%") do echo [OK] Wrote "%%~nxA" with %COUNT% file^(s^).
)

echo.
echo [STEP 2] Compiling "%QRC%" to Python module "%OUT_PY_PARENT%"...
del "%OUT_PY_PARENT%" 2>nul

where pyside2-rcc
echo [DEBUG] where pyside2-rcc errorlevel: %errorlevel%
pyside2-rcc -o "%OUT_PY_PARENT%" "%QRC%"
echo [DEBUG] pyside2-rcc compile errorlevel: %errorlevel%

if not exist "%OUT_PY_PARENT%" (
  echo [INFO] pyside2-rcc did not produce "%OUT_PY_PARENT%". Trying pyside6-rcc...
  where pyside6-rcc
  echo [DEBUG] where pyside6-rcc errorlevel: %errorlevel%
  pyside6-rcc -o "%OUT_PY_PARENT%" "%QRC%"
  echo [DEBUG] pyside6-rcc compile errorlevel: %errorlevel%
)

if not exist "%OUT_PY_PARENT%" (
  echo [INFO] pyside6-rcc did not produce "%OUT_PY_PARENT%". Trying Qt rcc...
  where rcc
  echo [DEBUG] where rcc errorlevel: %errorlevel%
  rcc -g python -o "%OUT_PY_PARENT%" "%QRC%"
  echo [DEBUG] rcc compile errorlevel: %errorlevel%
)

if exist "%OUT_PY_PARENT%" (
  for %%A in ("%OUT_PY_PARENT%") do echo [OK] Created "%%~nxA" (%%~zA bytes).
) else (
  echo [ERROR] Failed to create "%OUT_PY_PARENT%".
  echo        Try running manually:
  echo        pyside2-rcc -o "%OUT_PY_PARENT%" "%QRC%"
  echo        or: pyside6-rcc -o "%OUT_PY_PARENT%" "%QRC%"
  echo        or: rcc -g python -o "%OUT_PY_PARENT%" "%QRC%"
  echo.
  echo ============================== SUMMARY ===============================
  if exist "%QRC%" ( for %%A in ("%QRC%") do echo   icons.qrc   : %%~zA bytes ) else ( echo   icons.qrc   : MISSING )
  if exist "%OUT_PY_PARENT%" ( for %%A in ("%OUT_PY_PARENT%") do echo   icons_rc.py: %%~zA bytes ) else ( echo   icons_rc.py: MISSING )
  echo =====================================================================
  pause
  exit /b 2
)

echo.
echo ============================== SUMMARY ===============================
if exist "%QRC%"          ( for %%A in ("%QRC%")          do echo   icons.qrc   : %%~zA bytes ) else ( echo   icons.qrc   : MISSING )
if exist "%OUT_PY_PARENT%"( for %%A in ("%OUT_PY_PARENT%")do echo   icons_rc.py: %%~zA bytes ) else ( echo   icons_rc.py: MISSING )
echo =====================================================================
echo.
echo Use in Maya Python (before applying stylesheets that reference :/icons/...):
echo   try:
echo     import icons_rc
echo   except Exception:
echo     from . import icons_rc
echo.
echo Press any key to close...
pause
endlocal
