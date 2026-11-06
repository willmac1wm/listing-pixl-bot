# Listing Pixl bot — Legion deploy.
# Run on Legion as willm (admin) from an elevated PowerShell:
#   powershell -NoProfile -File C:\Users\willm\pixlbot-deploy.ps1
#
# What it does:
#  1. Copies staged code from C:\Users\willm\pixlbot-stage\ to C:\listing-pixl-bot\
#     (preserves an existing .env and data/ on re-deploy).
#  2. Creates/updates the venv and installs requirements.txt.
#  3. Writes C:\listing-pixl-bot\.env if missing. The Telnyx API key is copied
#     from C:\ai-comms-service\.env ON THIS MACHINE — the key value is never
#     printed, logged, or written anywhere except the bot .env.
#  4. Creates/restarts the NSSM service "ListingPixlBot" (uvicorn, port 8102).
#  5. Adds bot.listingpixl.com -> 127.0.0.1:8102 to the capedispatch tunnel
#     ingress and restarts the Cloudflared service (brief blip for the
#     tunnel's other hostnames: capemaydispatch.com, api.faxsnappro.com).
#  6. Verifies http://127.0.0.1:8102/health.
#
# The bot number is NOT set here — TELNYX_BOT_NUMBER stays empty until the
# number is bought at launch. DNS for bot.listingpixl.com (CNAME to the
# tunnel) is also a launch step; see LAUNCH.md.

$ErrorActionPreference = "Stop"

$stage = "C:\Users\willm\pixlbot-stage"
$dest  = "C:\listing-pixl-bot"
$port  = 8102

Write-Output "== 1. Sync code $stage -> $dest =="
if (-not (Test-Path $stage)) { throw "Stage dir missing: $stage" }
robocopy $stage $dest /MIR /XD .venv data __pycache__ .git /XF .env | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed: $LASTEXITCODE" }

Write-Output "== 2. Venv + deps =="
if (-not (Test-Path "$dest\.venv\Scripts\python.exe")) {
    python -m venv "$dest\.venv"
}
& "$dest\.venv\Scripts\python.exe" -m pip install --quiet -r "$dest\requirements.txt"
Write-Output "deps installed"

Write-Output "== 3. .env (key copied on-machine, never printed) =="
$envFile = "$dest\.env"
if (-not (Test-Path $envFile)) {
    $srcEnv = "C:\ai-comms-service\.env"
    $keyLine = Get-Content $srcEnv | Where-Object { $_ -match '^TELNYX_API_KEY=' } | Select-Object -First 1
    if (-not $keyLine) { throw "TELNYX_API_KEY not found in $srcEnv" }
    $keyValue = ($keyLine -split '=', 2)[1].Trim()
    if (-not $keyValue) { throw "TELNYX_API_KEY is empty in $srcEnv" }
    $lines = @(
        "# Listing Pixl bot — Legion .env. Do not commit. Key copied from the FaxSnap .env on this machine."
        "TELNYX_API_KEY=$keyValue"
        "TELNYX_BOT_NUMBER="
        "WILLIAM_CELL=+16097807027"
        "PUBLIC_BASE_URL=https://bot.listingpixl.com"
        "TELNYX_PUBLIC_KEY="
        "DATA_DIR=C:\listing-pixl-bot\data"
        "LOG_LEVEL=INFO"
    )
    Set-Content -Path $envFile -Value $lines -Encoding UTF8
    Write-Output ".env written (key present, value not shown)"
} else {
    Write-Output ".env already exists — kept as-is"
}

Write-Output "== 4. NSSM service ListingPixlBot =="
$svc = Get-Service ListingPixlBot -ErrorAction SilentlyContinue
if ($svc) { nssm stop ListingPixlBot }
else { nssm install ListingPixlBot "$dest\.venv\Scripts\python.exe" }
nssm set ListingPixlBot AppDirectory "$dest"
nssm set ListingPixlBot AppParameters "-m uvicorn app.main:app --host 127.0.0.1 --port $port"
nssm set ListingPixlBot Start SERVICE_AUTO_START
nssm start ListingPixlBot
Get-Service ListingPixlBot | Format-Table Name, Status -AutoSize

Write-Output "== 5. Tunnel ingress bot.listingpixl.com -> 127.0.0.1:$port =="
$cfg = "C:\Windows\System32\config\systemprofile\.cloudflared\config.yml"
$text = Get-Content $cfg -Raw
if ($text -notmatch 'bot\.listingpixl\.com') {
    $rule = "  - hostname: bot.listingpixl.com`n    service: http://127.0.0.1:$port`n"
    $text = $text -replace '(?m)^(  - service: http_status:404)', ($rule + '$1')
    Set-Content -Path $cfg -Value $text -Encoding UTF8 -NoNewline
    Write-Output "ingress rule added"
    Restart-Service Cloudflared
    Write-Output "Cloudflared restarted"
} else {
    Write-Output "ingress rule already present"
}

Write-Output "== 6. Verify =="
Start-Sleep -Seconds 5
Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" | ConvertTo-Json
Write-Output "Deploy done. Bot number + DNS CNAME are launch steps (see LAUNCH.md)."
