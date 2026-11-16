# Avvio guidato Windows di ZION. Non esegue chiamate ai modelli senza conferma.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$logPath = Join-Path $logs ("avvio_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
$transcribing = $false
$exitCode = 1

# Allinea il PATH del nuovo processo con quello usato dall'installer.
# Necessario quando hcom (uv) viene installato in una cartella utente non ancora
# visibile ai processi Windows avviati con il vecchio ambiente.
function Refresh-ZionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $extras = @(
        "$env:APPDATA\npm",
        "$HOME\.local\bin",
        "$env:ProgramFiles\Git\cmd",
        "$env:ProgramFiles\nodejs",
        "$env:LOCALAPPDATA\Programs\Git\cmd",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links",
        "$env:LOCALAPPDATA\Programs\Python\Python312",
        "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"
    )
    if ($env:UV_TOOL_BIN_DIR) { $extras += $env:UV_TOOL_BIN_DIR }
    $env:Path = (((@($env:Path, $machine, $user) -join ';').Split(';') + $extras |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Unique) -join ';')

    # uv conosce la directory effettiva anche se l'utente l'ha personalizzata.
    $uv = Get-Command uv -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($uv) {
        try {
            $uvBin = & $uv.Source tool dir --bin 2>$null | Select-Object -First 1
            if ($LASTEXITCODE -eq 0 -and $uvBin -and
                (Test-Path -LiteralPath $uvBin -PathType Container) -and
                (($env:Path -split ';') -notcontains $uvBin)) {
                $env:Path += ";$uvBin"
            }
        } catch { }
    }
}

function Stop-Zion([string]$Message) {
    Write-Host "[ZION] ERRORE: $Message" -ForegroundColor Red
    throw $Message
}

try {
    Start-Transcript -Path $logPath -Append | Out-Null
    $transcribing = $true
    Write-Host '[ZION] Avvio guidato su Windows'
    Write-Host '[ZION] Le IA saranno chiamate SOLO dopo la conferma del compito.'
    Refresh-ZionPath
    Write-Host '[ZION] Percorsi Windows aggiornati per questa sessione.'

    $python = Get-Command py -ErrorAction SilentlyContinue | Select-Object -First 1
    $pyArgs = @('-3')
    if (-not $python) {
        $python = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
        $pyArgs = @()
    }
    if (-not $python) { Stop-Zion 'Python non trovato. Esegui INSTALLA_ZION.bat.' }

    foreach ($tool in @('git', 'hcom', 'claude', 'codex')) {
        $found = Get-Command $tool -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $found) {
            Stop-Zion "$tool non trovato anche dopo aggiornamento PATH. Controlla logs e INSTALLA_ZION.bat."
        }
        if ($tool -eq 'hcom') {
            Write-Host "[ZION] hcom trovato: $($found.Source)"
        }
    }

    $projectInput = Read-Host 'Incolla il percorso della cartella del progetto Git su cui lavorare (es. C:\LUMI)'
    $projectInput = $projectInput.Trim().Trim('"')
    if (-not $projectInput) { Stop-Zion 'Percorso non specificato.' }
    if (-not (Test-Path -LiteralPath $projectInput -PathType Container)) {
        Stop-Zion "Cartella inesistente: $projectInput"
    }
    $project = (Resolve-Path -LiteralPath $projectInput).Path
    $gitRoot = & git -C $project rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $gitRoot) {
        Stop-Zion 'Il progetto deve essere una repository Git.'
    }
    $normalRoot = [IO.Path]::GetFullPath(($gitRoot | Select-Object -First 1)).TrimEnd('\','/')
    if ($normalRoot -ine $project.TrimEnd('\','/')) {
        Stop-Zion "Indica la cartella principale del repository: $normalRoot"
    }

    Write-Host '[ZION] Apro Claude e Codex in due finestre separate.'
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/k hcom claude' -WorkingDirectory $project
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/k hcom codex' -WorkingDirectory $project
    Write-Host 'Completa eventuali accessi nelle due finestre. Non chiuderle.'
    [void](Read-Host 'Quando entrambi sono pronti, premi INVIO per continuare')
    Write-Host '[ZION] Agenti hcom rilevati:'
    & hcom list
    $claudeName = Read-Host 'Nome agente Claude (INVIO = claude)'
    $codexName = Read-Host 'Nome agente Codex (INVIO = codex)'
    if (-not $claudeName) { $claudeName = 'claude' }
    if (-not $codexName) { $codexName = 'codex' }

    $task = Read-Host 'Descrivi il compito da far eseguire a Claude e revisionare a Codex'
    if (-not $task) { Stop-Zion 'Nessun compito inserito.' }
    Write-Host '[ZION] ATTENZIONE: l''esecuzione consuma quota dei servizi Claude e Codex.'
    $choice = Read-Host 'Vuoi inviare il compito adesso? (scrivi SI per confermare)'
    if ($choice -ine 'SI') {
        Write-Host '[ZION] Annullato: nessuna richiesta inviata alle IA.'
        $exitCode = 0
    } else {
        & $python.Source @pyArgs (Join-Path $root 'zion.py') run $task --project $project --claude $claudeName --codex $codexName
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) {
            Write-Host '[ZION] Workflow concluso con revisione superata.' -ForegroundColor Green
        } else {
            Write-Host "[ZION] Workflow non completato (codice $exitCode). Leggi il motivo qui sopra e in .zion\runs del progetto." -ForegroundColor Yellow
        }
    }
} catch {
    Write-Host "[ZION] Avvio interrotto: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
} finally {
    Write-Host "[ZION] Log avvio: $logPath"
    if ($transcribing) { Stop-Transcript | Out-Null }
}
exit $exitCode
