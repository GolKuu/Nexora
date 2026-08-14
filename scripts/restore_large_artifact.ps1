[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('venv-train-windows', 'kase-ai-1.7b-v0.2-checkpoint-25')]
    [string]$Artifact,

    [string]$Destination
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$extractRoot = if ($Destination) {
    if ([System.IO.Path]::IsPathRooted($Destination)) {
        [System.IO.Path]::GetFullPath($Destination)
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Destination))
    }
}
else {
    $repoRoot
}
$artifactDir = Join-Path $repoRoot 'artifacts\multipart'
$manifestPath = Join-Path $artifactDir "$Artifact.manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Manifest not found: $manifestPath"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$archivePath = Join-Path $artifactDir "$Artifact.restored.zip"
Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue

$output = [System.IO.File]::Create($archivePath)
try {
    foreach ($part in $manifest.parts) {
        $partPath = Join-Path $artifactDir $part.file
        if (-not (Test-Path -LiteralPath $partPath)) {
            throw "Missing archive part: $partPath"
        }
        $actualPartHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $partPath).Hash.ToLowerInvariant()
        if ($actualPartHash -ne $part.sha256) {
            throw "Checksum mismatch: $($part.file)"
        }
        $input = [System.IO.File]::OpenRead($partPath)
        try {
            $input.CopyTo($output)
        }
        finally {
            $input.Dispose()
        }
    }
}
finally {
    $output.Dispose()
}

$actualArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
if ($actualArchiveHash -ne $manifest.archive_sha256) {
    throw "Combined archive checksum mismatch for $Artifact"
}

New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
Push-Location $extractRoot
try {
    & tar.exe -xf $archivePath
    if ($LASTEXITCODE -ne 0) {
        throw "tar extraction failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
}

Write-Host "Restored $($manifest.source) under $extractRoot and verified SHA-256."
