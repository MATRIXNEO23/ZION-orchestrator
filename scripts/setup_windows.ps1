# ZION: preparazione Windows senza avviare Claude, Codex o chiamate ai modelli.
param(
    [Parameter(Mandatory = $true)][string]$LogPath,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$failures = New-Object 'System.Collections.Generic.List[string]'
$transcribing = $false
$exitCode = 0

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
    $env:Path = ((@($env:Path, $machine, $user) -join ';').Split(';') + $extras |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Unique) -join ';'
}

function Find-ZionCommand([string]$Name) {
    $candidates = @($Name)
    if ($Name -in @('npm', 'claude', 'codex')) {
        $candidates = @("$Name.cmd", $Name)
    }
    foreach ($candidate in $candidates) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) { return $found.Source }
    }
    return $null
}

function Test-ZionTool([string]$Name) {
    $command = Find-ZionCommand $Name
    if (-not $command) { return $false }
    try {
        & $command --version *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Test-ZionPython {
    foreach ($name in @('py', 'python')) {
        $command = Find-ZionCommand $name
        if (-not $command) { continue }
        $arguments = @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)')
        if ($name -eq 'py') {
            $arguments = @('-3') + $arguments
        }
        try {
            & $command @arguments *> $null
            if ($LASTEXITCODE -eq 0) { return $true }
        } catch {}
    }
    return $false
}

function Add-ZionFailure([string]$Text) {
    $script:failures.Add($Text)
    Write-Host "[ERRORE] $Text" -ForegroundColor Red
}

function Ensure-ZionDependency {
    param(
        [string]$Label,
        [scriptblock]$Ready,
        [ValidateSet('winget','uv','npm')][string]$Method,
        [string]$Package
    )
    if (& $Ready) {
        Write-Host "[OK] $Label"
        return
    }
    if ($CheckOnly) {
        Add-ZionFailure "$Label mancante (solo controllo: nessuna installazione)"
        return
    }
    Write-Host "[INSTALLAZIONE] $Label assente. Installo: $Package"
    try {
        if ($Method -eq 'winget') {
            $installer = Find-ZionCommand 'winget'
            if (-not $installer) {
                throw "WinGet non disponibile. Installa o ripristina App Installer di Microsoft."
            }
            & $installer install --id $Package --exact --source winget --accept-package-agreements --accept-source-agreements
        } elseif ($Method -eq 'uv') {
            $installer = Find-ZionCommand 'uv'
            if (-not $installer) { throw "uv non disponibile" }
            & $installer tool install $Package
        } elseif ($Method -eq 'npm') {
            $installer = Find-ZionCommand 'npm'
            if (-not $installer) { throw "npm non disponibile" }
            & $installer install -g $Package
        }
        if ($LASTEXITCODE -ne 0) {
            throw "L'installer ha restituito il codice $LASTEXITCODE"
        }
        Refresh-ZionPath
        if (-not (& $Ready)) {
            throw "Installazione terminata ma comando non disponibile. Potrebbe essere necessario riaprire il terminale."
        }
        Write-Host "[OK] $Label installato"
    } catch {
        Add-ZionFailure "$($Label): $($_.Exception.Message)"
    }
}

try {
    $folder = Split-Path -Parent $LogPath
    New-Item -Path $folder -ItemType Directory -Force | Out-Null
    Start-Transcript -Path $LogPath -Append | Out-Null
    $transcribing = $true
    Write-Host "[ZION] Verifica requisiti Windows - $(Get-Date -Format s)"
    if ($CheckOnly) { Write-Host '[ZION] Modalita sola diagnosi: nessuna modifica al PC' }
    if ($env:OS -ne 'Windows_NT') {
        Add-ZionFailure 'Questo avvio e riservato a Windows.'
    } else {
        Refresh-ZionPath
        Ensure-ZionDependency 'Git' { Test-ZionTool 'git' } 'winget' 'Git.Git'
        Ensure-ZionDependency 'Python 3.10+' { Test-ZionPython } 'winget' 'Python.Python.3.12'

        if (-not (Test-ZionTool 'hcom')) {
            Ensure-ZionDependency 'uv' { Test-ZionTool 'uv' } 'winget' 'astral-sh.uv'
        }
        Ensure-ZionDependency 'hcom' { Test-ZionTool 'hcom' } 'uv' 'hcom'

        if (-not (Test-ZionTool 'claude') -or -not (Test-ZionTool 'codex')) {
            Ensure-ZionDependency 'Node.js/npm' { Test-ZionTool 'npm' } 'winget' 'OpenJS.NodeJS.LTS'
        }
        Ensure-ZionDependency 'Claude Code CLI' { Test-ZionTool 'claude' } 'npm' '@anthropic-ai/claude-code'
        Ensure-ZionDependency 'Codex CLI' { Test-ZionTool 'codex' } 'npm' '@openai/codex'

        if ($failures.Count -eq 0) {
            $python = Find-ZionCommand 'py'
            $arguments = @('-3', (Join-Path $PSScriptRoot '..\zion.py'), 'doctor')
            if (-not $python) {
                $python = Find-ZionCommand 'python'
                $arguments = @((Join-Path $PSScriptRoot '..\zion.py'), 'doctor')
            }
            Write-Host '[ZION] Diagnostica finale interna:'
            & $python @arguments
            if ($LASTEXITCODE -ne 0) {
                Add-ZionFailure "La diagnostica zion.py doctor e fallita (codice $LASTEXITCODE)"
            }
        }
    }
    if ($failures.Count -gt 0) {
        $exitCode = 1
        Write-Host "[ZION] Controllo non superato: $($failures.Count) problema/i" -ForegroundColor Yellow
        foreach ($failure in $failures) { Write-Host " - $failure" }
    } else {
        Write-Host '[ZION] Tutti i comandi richiesti risultano disponibili.'
        Write-Host '[ZION] L''accesso a Claude e Codex va effettuato dall''utente e non e stato verificato.'
        Write-Host '[ZION] Nessun modello e stato chiamato: nessun consumo di quota in questa verifica.'
    }
} catch {
    $exitCode = 1
    Write-Host "[ZION] ERRORE INATTESO: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    Write-Host "[ZION] Log locale: $LogPath"
    if ($transcribing) { Stop-Transcript | Out-Null }
}
exit $exitCode
