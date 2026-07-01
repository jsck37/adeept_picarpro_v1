@echo off

setlocal EnableExtensions EnableDelayedExpansion
pushd "%~dp0"
chcp 65001>nul

call :msg "Clearing python cache..." 08
echo.
echo.

for /R "%~dp0" %%f in (__pycache__) do (
    echo %%f | findstr /i /c:".git\\" >nul || (
        if exist "%%f" (
            rmdir /q /s "%%f"
            call :msg "Cleaning was successful!" 0A
            echo.
            echo %%f
            echo.
        ) else (
            call :msg "An error occurred while cleaning!" 0C
            echo.
            echo %%f
            echo.
        )
    )
)

:: Удаление папок 'files' и 'logs' в корне скрипта
if exist "files" (
	rmdir /q /s "files"
	call :msg "Removed 'files' folder." 0A
	echo.
	echo %~dp0files
	echo.
) else (
	call :msg "'files' folder not found." 0C
	echo.
	echo %~dp0files
	echo.
)

if exist "logs" (
	rmdir /q /s "logs"
	call :msg "Removed 'logs' folder." 0A
	echo.
	echo %~dp0logs
	echo.
) else (
	call :msg "'logs' folder not found." 0C
	echo.
	echo %~dp0logs
	echo.
)

call :msg "The script has ended." 08
echo.
pause
goto eol

:msg
	chcp 866>nul

	for /f %%i in ('"prompt $h& for %%i in (.) do rem"') do (set Z=%%i)
	pushd "%TEMP%" && (
		<nul>"%~1^" set /p="%Z%%Z%  %Z%%Z%"
		findstr /a:%2 . "%~1^*"
		del "%~1^"
		popd
		)
	chcp 65001>nul
	exit /b

:eol