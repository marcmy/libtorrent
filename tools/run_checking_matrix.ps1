[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Torrent,

    [Parameter(Mandatory = $true)]
    [string]$SavePath,

    [string]$BenchmarkExe = (Join-Path $PSScriptRoot 'checking_benchmark.exe'),

    [string[]]$Backends = @('default', 'pread', 'mmap'),

    [int[]]$CheckingMiB = @(32, 128, 512, 1024),

    [int[]]$HashThreads = @(1),

    [int]$AioThreads = 10,

    [int]$Runs = 1,

    [int]$CooldownSeconds = 5,

    [string]$Output = (Join-Path (Get-Location) 'checking-benchmark.csv')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $BenchmarkExe -PathType Leaf)) {
    throw "Benchmark executable not found: $BenchmarkExe"
}
if (-not (Test-Path -LiteralPath $Torrent -PathType Leaf)) {
    throw "Torrent file not found: $Torrent"
}
if (-not (Test-Path -LiteralPath $SavePath -PathType Container)) {
    throw "Save path not found: $SavePath"
}
if ($Runs -lt 1) {
    throw 'Runs must be at least 1.'
}
if ($AioThreads -lt 1) {
    throw 'AioThreads must be at least 1.'
}

Write-Warning 'Windows filesystem cache can make later passes faster than the first. For HDD comparisons, prefer one run per configuration and compare cold-ish runs after similar idle periods.'
Write-Host "Benchmark: $BenchmarkExe"
Write-Host "Torrent:   $Torrent"
Write-Host "Save path: $SavePath"
Write-Host "Output:    $Output"

$results = [System.Collections.Generic.List[object]]::new()
$total = $Backends.Count * $CheckingMiB.Count * $HashThreads.Count
$index = 0

foreach ($backend in $Backends) {
    foreach ($memory in $CheckingMiB) {
        foreach ($hashCount in $HashThreads) {
            $index++
            Write-Host ""
            Write-Host "[$index/$total] backend=$backend checking=${memory}MiB hashThreads=$hashCount aioThreads=$AioThreads"

            $arguments = @(
                '--torrent', $Torrent,
                '--save-path', $SavePath,
                '--backend', $backend,
                '--hash-threads', $hashCount,
                '--aio-threads', $AioThreads,
                '--checking-mib', $memory,
                '--runs', $Runs
            )

            $outputLines = & $BenchmarkExe @arguments 2>&1
            $exitCode = $LASTEXITCODE
            $outputLines | ForEach-Object { Write-Host $_ }

            if ($exitCode -ne 0) {
                throw "checking_benchmark exited with code $exitCode for backend=$backend checking=${memory}MiB hashThreads=$hashCount"
            }

            foreach ($line in $outputLines) {
                $text = [string]$line
                if (-not $text.StartsWith('RESULT,')) {
                    continue
                }

                $fields = $text.Split(',')
                if ($fields.Count -ne 9) {
                    throw "Unexpected RESULT line: $text"
                }

                $results.Add([pscustomobject]@{
                    Backend       = $fields[1]
                    HashThreads   = [int]$fields[2]
                    AioThreads    = [int]$fields[3]
                    CheckingMiB   = [int]$fields[4]
                    Run           = [int]$fields[5]
                    Seconds       = [double]::Parse($fields[6], [Globalization.CultureInfo]::InvariantCulture)
                    PayloadBytes  = [int64]$fields[7]
                    MiBPerSecond  = [double]::Parse($fields[8], [Globalization.CultureInfo]::InvariantCulture)
                })
            }

            if ($CooldownSeconds -gt 0 -and $index -lt $total) {
                Start-Sleep -Seconds $CooldownSeconds
            }
        }
    }
}

$results |
    Sort-Object MiBPerSecond -Descending |
    Format-Table Backend, CheckingMiB, HashThreads, AioThreads, Run, Seconds, MiBPerSecond -AutoSize

$results | Export-Csv -LiteralPath $Output -NoTypeInformation
Write-Host ""
Write-Host "Saved $($results.Count) result(s) to $Output"
