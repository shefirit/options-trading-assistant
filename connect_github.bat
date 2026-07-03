@echo off
cd /d "%~dp0"
echo ============================================================
echo  Connect GitHub
echo ------------------------------------------------------------
echo  A browser window will open. Approve it (you may need to
echo  paste a short code it shows). Then come back to this window.
echo ============================================================
echo.
gh auth login --hostname github.com --git-protocol https --web
echo.
echo If it says "Logged in as ..." above, you're connected.
echo Tell the assistant, and it will upload your app for you.
echo.
pause
