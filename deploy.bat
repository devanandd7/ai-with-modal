@echo off
title AI Server Deploy
cd /d "%~dp0"

echo.
echo ============================================
echo   AI Server - Deploy to Modal
echo ============================================
echo.

if not exist .env (
    echo [ERROR] .env file not found!
    echo.
    echo Copy .env.example to .env and fill in your API keys:
    echo   copy .env.example .env
    echo.
    pause
    exit /b 1
)

echo [1/3] Reading API keys from .env ...
echo.

REM Parse .env using PowerShell and create Modal Secret
powershell -Command ^
    "$envFile = '.env'; ^
     $keys = @(); ^
     Get-Content $envFile | ForEach-Object { ^
         $line = $_.Trim(); ^
         if ($line -and !$line.StartsWith('#')) { ^
             $parts = $line.Split('=', 2); ^
             if ($parts[0] -and $parts[1]) { $keys += $line } ^
         } ^
     }; ^
     if ($keys.Count -eq 0) { Write-Host 'No keys found in .env'; exit 1 }; ^
     $argStr = $keys -join ' '; ^
     Write-Host 'Keys found:'; ^
     $keys | ForEach-Object { Write-Host '  - ' $_ }; ^
     Write-Host ''; ^
     $cmd = 'modal secret create ai-server-keys ' + $argStr; ^
     Write-Host ('Running: ' + $cmd); ^
     $result = Invoke-Expression $cmd 2>&1; ^
     if ($LASTEXITCODE -ne 0) { ^
         Write-Host 'Secret may already exist. Trying to update...'; ^
         $envVars = @{}; ^
         $keys | ForEach-Object { $k,$v = $_.Split('=',2); $envVars[$k] = $v }; ^
         $updateCmd = 'modal secret create ai-server-keys'; ^
         $envVars.Keys | ForEach-Object { $updateCmd += ' ' + $_ + '=' + $envVars[$_] }; ^
         Invoke-Expression $updateCmd 2>&1 ^
     }"

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Failed to create Modal Secret.
    echo Try manually: modal secret create ai-server-keys GROQ_API_KEY=gsk_... GEMINI_API_KEY=AIza_...
    pause
    exit /b 1
)

echo.
echo [2/3] Modal Secret created/updated successfully!
echo.

echo [3/3] Deploying to Modal...
echo.
call modal deploy app_test.py

if %ERRORLEVEL% equ 0 (
    echo.
    echo ============================================
    echo   ✅ Deploy successful!
    echo ============================================
    echo.
    echo Test your server:
    echo   curl https://crosseye315--ai-server-web.modal.run/ping
    echo   curl https://crosseye315--ai-server-web.modal.run/models
) else (
    echo.
    echo [ERROR] Deploy failed!
)

echo.
pause
