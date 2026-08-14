[CmdletBinding()]
param(
    [ValidateRange(1000000, 99000000)]
    [int]$PartSizeBytes = 94000000
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$outputDir = Join-Path $repoRoot 'artifacts\multipart'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

function New-MultipartArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$SourcePath
    )

    $archivePath = Join-Path $outputDir "$Name.archive.zip"
    Get-ChildItem -LiteralPath $outputDir -Filter "$Name.part*" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue

    Write-Host "Packing $SourcePath..."
    Push-Location $repoRoot
    try {
        & tar.exe -a -cf $archivePath $SourcePath
        if ($LASTEXITCODE -ne 0) {
            throw "tar failed for $SourcePath with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }

    $archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
    $input = [System.IO.File]::OpenRead($archivePath)
    $buffer = New-Object byte[] $PartSizeBytes
    $parts = @()
    try {
        $partNumber = 1
        while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $partName = '{0}.part{1:d3}' -f $Name, $partNumber
            $partPath = Join-Path $outputDir $partName
            $output = [System.IO.File]::Create($partPath)
            try {
                $output.Write($buffer, 0, $read)
            }
            finally {
                $output.Dispose()
            }
            $parts += [ordered]@{
                file = $partName
                bytes = $read
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $partPath).Hash.ToLowerInvariant()
            }
            Write-Host "Created $partName ($read bytes)"
            $partNumber++
        }
    }
    finally {
        $input.Dispose()
    }

    $manifest = [ordered]@{
        format = 1
        artifact = $Name
        source = $SourcePath
        archive = "$Name.archive.zip"
        archive_bytes = (Get-Item -LiteralPath $archivePath).Length
        archive_sha256 = $archiveHash
        part_size_bytes = $PartSizeBytes
        parts = $parts
    }
    $manifestPath = Join-Path $outputDir "$Name.manifest.json"
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Remove-Item -LiteralPath $archivePath -Force
    Write-Host "Wrote $manifestPath"
}

New-MultipartArtifact -Name 'venv-train-windows' -SourcePath '.venv-train'
New-MultipartArtifact -Name 'kase-ai-1.7b-v0.2-checkpoint-25' -SourcePath 'models\kase-ai-1.7b-v0.2\checkpoints\checkpoint-25'
