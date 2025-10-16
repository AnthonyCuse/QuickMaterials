REM ---------- Process all PNGs in current folder ----------
set "COUNT=0"

for %%F in ("*.png") do (
  set "SRC=%%~fF"
  set "BASE=%%~nF"
  call :process_one
)

if %COUNT%==0 (
  echo [WARN] No PNG files found in: "%CD%"
) else (
  echo.
  echo [DONE] Processed %COUNT% file(s).
)

goto :AFTER

:process_one
REM Create rotated copies for the current !SRC!/!BASE! unless it is already a rotated variant
setlocal EnableDelayedExpansion

set "B=!BASE!"
set "END3=!B:~-3!"
set "END5=!B:~-5!"
set "END6=!B:~-6!"
if /I "!END3!"=="_up"    (echo [SKIP] "!SRC!" & goto :eof)
if /I "!END5!"=="_down"  (echo [SKIP] "!SRC!" & goto :eof)
if /I "!END6!"=="_right" (echo [SKIP] "!SRC!" & goto :eof)
if /I "!END5!"=="_left"  (echo [SKIP] "!SRC!" & goto :eof)

set "OUT_UP=!B!_up.png"
set "OUT_RIGHT=!B!_right.png"
set "OUT_DOWN=!B!_down.png"
set "OUT_LEFT=!B!_left.png"

echo.
echo [SRC] "!SRC!"
echo [OUT] "!OUT_UP!", "!OUT_RIGHT!", "!OUT_DOWN!", "!OUT_LEFT!"

REM Common flags to preserve transparency while rotating
set "IM_PRE="
set "IM_EXTRAS=-alpha on -background none -virtual-pixel transparent"

magick "!SRC!" !IM_PRE! !IM_EXTRAS! -distort SRT "0"   "!OUT_UP!"
magick "!SRC!" !IM_PRE! !IM_EXTRAS! -distort SRT "90"  "!OUT_RIGHT!"
magick "!SRC!" !IM_PRE! !IM_EXTRAS! -distort SRT "180" "!OUT_DOWN!"
magick "!SRC!" !IM_PRE! !IM_EXTRAS! -distort SRT "270" "!OUT_LEFT!"

endlocal & set /a COUNT+=1
goto :eof

:AFTER
