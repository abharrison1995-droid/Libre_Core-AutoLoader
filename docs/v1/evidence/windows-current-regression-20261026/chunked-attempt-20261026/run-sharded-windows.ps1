param(
    [int]$ShardSize = 25,
    [long]$MinimumFreeBytes = 3221225472
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$evidenceRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $evidenceRoot '..\..\..\..\..')).Path
$collectionPath = Join-Path $evidenceRoot 'collect.stdout.txt'
$previousManifest = Join-Path $repoRoot 'docs\v1\evidence\software-regression-20261025\exact-tree-recapture-20261025\attempt-13\source-manifest.sha256'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$scratchRoot = 'C:\w\lc-v1-win-shards-20261026'
$shardSize = [Math]::Max(1, $ShardSize)

if (-not (Test-Path -LiteralPath $collectionPath -PathType Leaf)) {
    throw "Missing current-tree collection output: $collectionPath"
}
if (-not (Test-Path -LiteralPath $previousManifest -PathType Leaf)) {
    throw "Missing source path manifest: $previousManifest"
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing project Python environment: $python"
}
if (Test-Path -LiteralPath $scratchRoot) {
    throw "Refusing to reuse existing scratch path: $scratchRoot"
}

$nodes = @(
    Get-Content -LiteralPath $collectionPath |
        Where-Object { $_ -match '^tests/.+::' }
)
if ($nodes.Count -ne 1392) {
    throw "Expected 1392 collected test node IDs; found $($nodes.Count)."
}

$manifestPaths = @(
    Get-Content -LiteralPath $previousManifest |
        ForEach-Object {
            $parts = $_ -split '\s{2}', 2
            if ($parts.Count -ne 2) { throw "Invalid source manifest row: $_" }
            $relative = $parts[1].Trim()
            if ($relative.StartsWith('./')) { $relative = $relative.Substring(2) }
            $relative = $relative.Replace('/', '\')
            if ([string]::IsNullOrWhiteSpace($relative)) { throw "Invalid source path row: $_" }
            $relative
        }
)

function Write-SourceManifest([string]$Destination) {
    $rows = foreach ($relativePath in $manifestPaths) {
        $absolutePath = Join-Path $repoRoot $relativePath
        if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
            throw "Source manifest path is missing: $relativePath"
        }
        $digest = (Get-FileHash -LiteralPath $absolutePath -Algorithm SHA256).Hash.ToLowerInvariant()
        "$digest  $relativePath"
    }
    Set-Content -LiteralPath $Destination -Value $rows -Encoding ascii
}

function Remove-OwnedChild([string]$Target, [string]$OwnerRoot) {
    if (-not (Test-Path -LiteralPath $Target)) { return }
    $rootFull = [IO.Path]::GetFullPath($OwnerRoot).TrimEnd('\') + '\'
    $targetFull = [IO.Path]::GetFullPath($Target).TrimEnd('\')
    if (-not $targetFull.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing cleanup outside the task scratch root: $targetFull"
    }
    Remove-Item -LiteralPath $targetFull -Recurse -Force
}

$beforeManifest = Join-Path $evidenceRoot 'source-manifest-before.sha256'
$afterManifest = Join-Path $evidenceRoot 'source-manifest-after.sha256'
Write-SourceManifest -Destination $beforeManifest
$sourceHashBefore = (Get-FileHash -LiteralPath $beforeManifest -Algorithm SHA256).Hash.ToLowerInvariant()

New-Item -ItemType Directory -Path $scratchRoot | Out-Null
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONOPTIMIZE = '0'
$env:GIT_TEMPLATE_DIR = Join-Path $repoRoot '.git-template-empty'
$env:GIT_CONFIG_COUNT = '1'
$env:GIT_CONFIG_KEY_0 = 'core.longpaths'
$env:GIT_CONFIG_VALUE_0 = 'true'

$pythonVersion = (& $python --version 2>&1 | Out-String).Trim()
$pytestVersion = (& $python -m pytest --version 2>&1 | Out-String).Trim()
$startedAt = [DateTime]::UtcNow.ToString('o')
$shardSummaries = [System.Collections.Generic.List[object]]::new()
$failedShard = $false
$shardCount = [Math]::Ceiling($nodes.Count / [double]$shardSize)

Set-Content -LiteralPath (Join-Path $evidenceRoot 'environment.txt') -Encoding utf8 -Value @(
    "os=$([Environment]::OSVersion.VersionString)"
    "python=$pythonVersion"
    "pytest=$pytestVersion"
    "commit=$((& git -C $repoRoot rev-parse HEAD).Trim())"
    "source_manifest_entries=$($manifestPaths.Count)"
    "collected_test_nodes=$($nodes.Count)"
    "shard_size=$shardSize"
    "shard_count=$shardCount"
    "minimum_free_bytes=$MinimumFreeBytes"
    "scratch_root=$scratchRoot"
    "pytest_basetemp=$(Join-Path $scratchRoot 'pytest')"
    "git_template=$env:GIT_TEMPLATE_DIR"
    'git_core.longpaths=true'
    "started_utc=$startedAt"
)

for ($shardIndex = 0; $shardIndex -lt $shardCount; $shardIndex++) {
    $freeBefore = (Get-PSDrive C).Free
    if ($freeBefore -lt $MinimumFreeBytes) {
        throw "Disk safety stop before shard $($shardIndex + 1): C: has $freeBefore bytes free."
    }

    $firstNode = $shardIndex * $shardSize
    $lastNodeExclusive = [Math]::Min($nodes.Count, $firstNode + $shardSize)
    $batch = [System.Collections.Generic.List[string]]::new()
    for ($nodeIndex = $firstNode; $nodeIndex -lt $lastNodeExclusive; $nodeIndex++) {
        $batch.Add($nodes[$nodeIndex])
    }

    $shardNumber = $shardIndex + 1
    $shardName = 'shard-{0:D2}' -f $shardNumber
    $shardDirectory = Join-Path $evidenceRoot $shardName
    if (Test-Path -LiteralPath $shardDirectory) {
        throw "Refusing to overwrite existing shard evidence: $shardDirectory"
    }
    New-Item -ItemType Directory -Path $shardDirectory | Out-Null

    $pytestBase = Join-Path $scratchRoot 'pytest'
    $systemTemp = Join-Path $scratchRoot ("tmp-{0:D2}" -f $shardNumber)
    New-Item -ItemType Directory -Path $systemTemp | Out-Null
    $env:TEMP = $systemTemp
    $env:TMP = $systemTemp
    $env:TMPDIR = $systemTemp

    $junitPath = Join-Path $shardDirectory 'junit.xml'
    $stdoutPath = Join-Path $shardDirectory 'stdout.txt'
    $stderrPath = Join-Path $shardDirectory 'stderr.txt'
    $nodeListPath = Join-Path $shardDirectory 'nodes.txt'
    $commandPath = Join-Path $shardDirectory 'command.txt'
    $exitPath = Join-Path $shardDirectory 'pytest.exit'
    $endNode = $lastNodeExclusive - 1

    Set-Content -LiteralPath $nodeListPath -Value $batch -Encoding utf8
    Set-Content -LiteralPath $commandPath -Encoding utf8 -Value @(
        "executable=$python"
        'arguments=-m pytest -p no:cacheprovider -q -ra --tb=short'
        "junit=$junitPath"
        "basetemp=$pytestBase"
        "node_range=$firstNode..$endNode of $($nodes.Count)"
        "environment_TEMP_TMP_TMPDIR=$systemTemp"
        'environment_PYTHONDONTWRITEBYTECODE=1'
        'environment_PYTHONOPTIMIZE=0'
        'environment_GIT_CONFIG=core.longpaths=true'
        'node IDs are preserved in nodes.txt'
    )

    $pytestArguments = @(
        '-m', 'pytest', '-p', 'no:cacheprovider', '-q', '-ra', '--tb=short',
        "--basetemp=$pytestBase", "--junitxml=$junitPath"
    ) + @($batch.ToArray())
    $shardStarted = [DateTime]::UtcNow.ToString('o')
    Write-Output "Starting $shardName/$shardCount ($($batch.Count) tests), C: free $freeBefore bytes"
    & $python @pytestArguments 1> $stdoutPath 2> $stderrPath
    $exitCode = $LASTEXITCODE
    Set-Content -LiteralPath $exitPath -Value "$exitCode" -Encoding ascii
    $freeAfter = (Get-PSDrive C).Free
    $shardEnded = [DateTime]::UtcNow.ToString('o')

    if (-not (Test-Path -LiteralPath $junitPath -PathType Leaf)) {
        $failedShard = $true
        $shardSummaries.Add([PSCustomObject]@{
            shard = $shardNumber; tests = $batch.Count; exit_code = $exitCode
            failures = $null; errors = $null; skipped = $null
            free_before = $freeBefore; free_after = $freeAfter
            started_utc = $shardStarted; ended_utc = $shardEnded
            status = 'STOP_NO_JUNIT'
        })
        Write-Output "Stopped at ${shardName}: pytest did not produce JUnit; scratch preserved at $scratchRoot"
        break
    }

    [xml]$junit = Get-Content -LiteralPath $junitPath -Raw
    $testSuites = @($junit.SelectNodes('//testsuite'))
    $tests = [int](($testSuites | ForEach-Object { [int]$_.GetAttribute('tests') } | Measure-Object -Sum).Sum)
    $failures = [int](($testSuites | ForEach-Object { [int]$_.GetAttribute('failures') } | Measure-Object -Sum).Sum)
    $errors = [int](($testSuites | ForEach-Object { [int]$_.GetAttribute('errors') } | Measure-Object -Sum).Sum)
    $skipped = [int](($testSuites | ForEach-Object { [int]$_.GetAttribute('skipped') } | Measure-Object -Sum).Sum)
    $status = if ($exitCode -eq 0 -and $failures -eq 0 -and $errors -eq 0) { 'PASS' } else { 'FAIL' }
    $shardSummaries.Add([PSCustomObject]@{
        shard = $shardNumber; tests = $tests; exit_code = $exitCode
        failures = $failures; errors = $errors; skipped = $skipped
        free_before = $freeBefore; free_after = $freeAfter
        started_utc = $shardStarted; ended_utc = $shardEnded
        status = $status
    })
    $shardSummaries | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $evidenceRoot 'shard-progress.json') -Encoding utf8

    if ($status -ne 'PASS') {
        $failedShard = $true
        Write-Output "Stopped at ${shardName} with exit=$exitCode failures=$failures errors=$errors; scratch preserved at $scratchRoot"
        break
    }
    if ($freeAfter -lt $MinimumFreeBytes) {
        $failedShard = $true
        Write-Output "Disk safety stop after ${shardName}: C: has $freeAfter bytes free; scratch preserved at $scratchRoot"
        break
    }

    Remove-OwnedChild -Target $pytestBase -OwnerRoot $scratchRoot
    Remove-OwnedChild -Target $systemTemp -OwnerRoot $scratchRoot
    Write-Output "Completed ${shardName}: $tests tests, $skipped skipped, C: free $freeAfter bytes"
}

Write-SourceManifest -Destination $afterManifest
$sourceHashAfter = (Get-FileHash -LiteralPath $afterManifest -Algorithm SHA256).Hash.ToLowerInvariant()
$totalTests = [int](($shardSummaries | Measure-Object -Property tests -Sum).Sum)
$totalFailures = [int](($shardSummaries | Measure-Object -Property failures -Sum).Sum)
$totalErrors = [int](($shardSummaries | Measure-Object -Property errors -Sum).Sum)
$totalSkipped = [int](($shardSummaries | Measure-Object -Property skipped -Sum).Sum)
$allShardsRan = $shardSummaries.Count -eq $shardCount
$sourceUnchanged = $sourceHashBefore -eq $sourceHashAfter
$passed = (-not $failedShard) -and $allShardsRan -and ($totalTests -eq $nodes.Count) -and
    ($totalFailures -eq 0) -and ($totalErrors -eq 0) -and $sourceUnchanged
$completedAt = [DateTime]::UtcNow.ToString('o')
$runStatus = if ($passed) { 'PASS_ALL_SHARDS' } else { 'INCOMPLETE_OR_FAILED' }

$finalSummary = [PSCustomObject]@{
    status = $runStatus
    expected_tests = $nodes.Count
    executed_tests = $totalTests
    shards_expected = $shardCount
    shards_completed = $shardSummaries.Count
    failures = $totalFailures
    errors = $totalErrors
    skipped = $totalSkipped
    passed = $totalTests - $totalFailures - $totalErrors - $totalSkipped
    source_manifest_entries = $manifestPaths.Count
    source_manifest_unchanged = $sourceUnchanged
    source_manifest_before_sha256 = $sourceHashBefore
    source_manifest_after_sha256 = $sourceHashAfter
    c_free_final_bytes = (Get-PSDrive C).Free
    started_utc = $startedAt
    completed_utc = $completedAt
}
$finalSummary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $evidenceRoot 'final-summary.json') -Encoding utf8
$shardSummaries | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $evidenceRoot 'shard-results.json') -Encoding utf8
Set-Content -LiteralPath (Join-Path $evidenceRoot 'run-status.txt') -Value $runStatus -Encoding ascii

if (-not $failedShard -and (Test-Path -LiteralPath $scratchRoot)) {
    $remaining = @(Get-ChildItem -LiteralPath $scratchRoot -Force)
    if ($remaining.Count -eq 0) {
        Remove-OwnedChild -Target $scratchRoot -OwnerRoot (Split-Path -Parent $scratchRoot)
    }
}

$checksumFile = Join-Path $evidenceRoot 'SHA256SUMS'
$checksumRows = Get-ChildItem -LiteralPath $evidenceRoot -File -Recurse |
    Where-Object { $_.FullName -ne $checksumFile } |
    Sort-Object FullName |
    ForEach-Object {
        $relativePath = $_.FullName.Substring($evidenceRoot.Length).TrimStart('\').Replace('\', '/')
        "{0}  {1}" -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $relativePath
    }
Set-Content -LiteralPath $checksumFile -Value $checksumRows -Encoding ascii

$finalSummary | Format-List | Out-String | Write-Output
if (-not $passed) { exit 1 }
