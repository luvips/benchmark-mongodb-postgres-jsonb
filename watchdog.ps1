<#
  watchdog.ps1 - Guardia de la campaña de benchmark (Windows + Docker Desktop).

  Corre en una SEGUNDA terminal de PowerShell, en la raíz del repo, mientras la campaña avanza:
      powershell -ExecutionPolicy Bypass -File .\watchdog.ps1 -NtfyTopic mi-tema-secreto

  Qué hace cada $IntervalSec segundos:
    1. Mantiene Windows despierto mientras el script corre (SetThreadExecutionState).
    2. Lee state\journal.jsonl y state\campaign.pid y detecta:
         - campaña detenida (halted), con su motivo;
         - campaña "running" cuyo proceso ya no existe (crash, reinicio de Windows);
         - campaña sin eventos durante más de $StallMin minutos (cuelgue).
    3. Ante un problema: junta evidencia, llama a Claude Code en modo headless (claude -p) con
       permisos acotados y el prompt de watchdog_prompt.md, verifica que el manifiesto SHA-256
       siga intacto y SOLO reanuda si Claude terminó con SAFE_TO_RESUME.
    4. Notifica por consola, por state\watchdog\watchdog.log y, opcionalmente, al celular vía ntfy.sh.

  Requisitos: Claude Code instalado y con sesión iniciada en esta máquina (`claude` en el PATH),
  .venv con las dependencias del proyecto, y watchdog_prompt.md junto a este script.
#>
param(
    [int]$IntervalSec = 120,
    [int]$StallMin = 25,          # una corrida tarda ~7 min; 25 min sin eventos = cuelgue
    [int]$MaxAutoFixes = 3,       # tope de intervenciones automáticas en toda la campaña
    [string]$NtfyTopic = $env:BENCH_NTFY_TOPIC
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$Py      = ".\.venv\Scripts\python.exe"
$Journal = "state\journal.jsonl"
$WdDir   = "state\watchdog"
New-Item -ItemType Directory -Force $WdDir | Out-Null

# Evita suspensión mientras el watchdog esté vivo (ES_CONTINUOUS | ES_SYSTEM_REQUIRED).
Add-Type -Namespace Bench -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[Bench.Power]::SetThreadExecutionState(0x80000001) | Out-Null

function Notify([string]$Msg) {
    $line = "$(Get-Date -Format s)  $Msg"
    Write-Host $line
    Add-Content -Path "$WdDir\watchdog.log" -Value $line -Encoding UTF8
    if ($NtfyTopic) {
        try { Invoke-RestMethod -Method Post -Uri "https://ntfy.sh/$NtfyTopic" -Body $Msg | Out-Null } catch {}
    }
}

function Get-Events {
    if (-not (Test-Path $Journal)) { return @() }
    Get-Content $Journal -Encoding UTF8 | ForEach-Object { try { $_ | ConvertFrom-Json } catch {} }
}

function Get-CampaignState($Events) {
    $map = @{ campaign_started = "running"; resumed = "running"; paused = "paused"; halted = "halted"; done = "done" }
    $state = "new"; $reason = $null
    foreach ($e in $Events) {
        if ($map.ContainsKey($e.event)) {
            $state = $map[$e.event]
            if ($e.event -eq "halted") { $reason = $e.reason }
        }
    }
    [pscustomobject]@{ State = $state; Reason = $reason; Last = $Events[-1] }
}

function Test-CampaignAlive {
    if (-not (Test-Path "state\campaign.pid")) { return $false }
    $campaignPid = [int](Get-Content "state\campaign.pid" -Raw).Trim()
    return [bool](Get-Process -Id $campaignPid -ErrorAction SilentlyContinue)
}

function Get-ProtectedDrift {
    # Mismo chequeo que usa el orquestador: archivos protegidos cuyo SHA-256 cambió.
    $code = "from orchestrator import manifest; from benchmark_runner.config import STATE_DIR, DATA_PATH; " +
            "print(','.join(manifest.changed(STATE_DIR / 'manifest.json', DATA_PATH)))"
    return ((& $Py -c $code) | Out-String).Trim()
}

function Invoke-ClaudeFix([string]$Problem) {
    $dir = "$WdDir\$(Get-Date -Format yyyyMMdd-HHmmss)"
    New-Item -ItemType Directory -Force $dir | Out-Null

    # Evidencia para el agente
    & $Py -m orchestrator.cli status *> "$dir\status.txt"
    Get-Content $Journal -Tail 40 -Encoding UTF8 | Set-Content "$dir\journal_tail.jsonl" -Encoding UTF8
    if (Test-Path "state\campaign.log") {
        Get-Content "state\campaign.log" -Tail 200 -Encoding UTF8 | Set-Content "$dir\campaign_log_tail.txt" -Encoding UTF8
    }
    docker ps -a --format "{{.Names}}`t{{.Status}}" *> "$dir\docker_ps.txt"
    docker system df *> "$dir\docker_df.txt"

    $prompt = (Get-Content "$PSScriptRoot\watchdog_prompt.md" -Raw -Encoding UTF8).
        Replace("{{PROBLEM}}", $Problem).Replace("{{DIR}}", ($dir -replace '\\', '/'))

    $allowed = @(
        "Read", "Grep", "Glob", "Edit", "Write",
        "Bash(docker:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
        "Bash(./.venv/Scripts/python.exe:*)", "Bash(.venv/Scripts/python.exe:*)",
        "Bash(tail:*)", "Bash(cat:*)", "Bash(ls:*)", "Bash(grep:*)"
    )
    $denied = @(
        "Bash(docker volume rm:*)", "Bash(docker system prune:*)", "Bash(docker compose down:*)",
        "Bash(git push:*)", "Bash(git reset:*)", "Bash(git checkout:*)", "Bash(git clean:*)",
        "Bash(rm:*)"
    )

    Notify "Invocando Claude Code para: $Problem (evidencia en $dir)"
    claude -p $prompt --permission-mode acceptEdits --allowedTools $allowed --disallowedTools $denied `
        --max-turns 40 --output-format json > "$dir\claude.json" 2> "$dir\claude.err"

    # Compuerta 1: el código de medición y los datos protegidos no pueden haber cambiado.
    $drift = Get-ProtectedDrift
    if ($drift) {
        Notify "BLOQUEADO: cambiaron archivos protegidos ($drift). No se reanuda; revisa $dir."
        return $false
    }
    # Compuerta 2: el agente debe declarar explícitamente que es seguro reanudar.
    $diag = if (Test-Path "$dir\diagnosis.md") { Get-Content "$dir\diagnosis.md" -Raw -Encoding UTF8 } else { "" }
    $verdict = ($diag.Trim() -split "`n")[-1].Trim()
    if ($verdict -ne "SAFE_TO_RESUME") {
        Notify "Claude pide intervención humana (veredicto: '$verdict'). Ver $dir\diagnosis.md"
        return $false
    }
    if (Test-CampaignAlive) {
        Notify "Claude dejó la infraestructura lista y la campaña sigue viva; no se reanuda."
        return $true
    }
    & $Py -m orchestrator.cli doctor *> "$dir\doctor.txt"
    & $Py -m orchestrator.cli resume *> "$dir\resume.txt"
    Notify "Campaña reanudada tras autocorrección. Detalle: $dir"
    return $true
}

Notify "Watchdog iniciado (intervalo $IntervalSec s, cuelgue > $StallMin min, máx. $MaxAutoFixes autocorrecciones)."
$fixes = 0
while ($true) {
    $events = @(Get-Events)
    if ($events.Count -eq 0) { Start-Sleep $IntervalSec; continue }
    $st = Get-CampaignState $events
    $alive = Test-CampaignAlive
    $ageMin = ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$st.Last.ts) / 60

    $problem = $null
    switch ($st.State) {
        "done"    { Notify "Campaña terminada."; exit 0 }
        "paused"  { }   # pausa manual del usuario: no intervenir
        "halted"  { $problem = "campaña detenida (halted): $($st.Reason)" }
        "running" {
            if (-not $alive)            { $problem = "el proceso de la campaña ya no existe (estado running)" }
            elseif ($ageMin -gt $StallMin) { $problem = "sin eventos en el journal desde hace $([int]$ageMin) min (posible cuelgue)" }
        }
    }

    if ($problem) {
        Notify "PROBLEMA: $problem"
        if ($fixes -ge $MaxAutoFixes) {
            Notify "Se alcanzó el tope de $MaxAutoFixes autocorrecciones. Se requiere intervención manual."
            exit 1
        }
        $fixes++
        if (-not (Invoke-ClaudeFix $problem)) {
            Notify "El watchdog queda en espera; cuando lo resuelvas a mano, reinícialo."
            exit 2
        }
    }
    Start-Sleep $IntervalSec
}
