#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$PreflightOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# This runner stages a filtered, hash-manifested copy of the current source tree
# under a unique C:\w directory, then runs the complete pytest suite there.
# It never writes firmware and must be run as a normal, non-elevated user.

$GiB = [int64]1073741824
$MinimumStartFreeBytes = [int64]10 * $GiB
$MinimumLiveFreeBytes = [int64]2 * $GiB
$MaximumSnapshotBytes = [int64]2 * $GiB
$ExpectedTestCount = 1392
$WindowsRequiredCases = @(
    'tests.test_phase10a_write_approval::test_windows_directory_fsync_policy_keeps_approval_closed',
    'tests.test_usb_builder::test_real_build_usb_cli_fails_before_subprocess_on_windows'
)
$AttemptRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$ExpectedAttempt = 'attempt-13'
$ExpectedBundle = 'windows-current-regression-20261026'
$EvidenceRoot = (Resolve-Path -LiteralPath (Join-Path $AttemptRoot '..')).Path
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $AttemptRoot '..\..\..\..\..')).Path
$RepoRootPrefix = $RepoRoot.TrimEnd('\') + '\'
$RepoDriveRoot = [System.IO.Path]::GetPathRoot($RepoRoot)
$ScratchParent = 'C:\w'
$UtcId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 12)
$RunEvidence = Join-Path $AttemptRoot ('run-' + $UtcId)
$ScratchRoot = Join-Path $ScratchParent ('lc-v1wfr-a13-' + [Guid]::NewGuid().ToString('N'))
$ScratchToken = [Guid]::NewGuid().ToString('D')
$ScratchMarkerName = '.lc-v1wfr-attempt13-owner'
$ScratchMarker = Join-Path $ScratchRoot $ScratchMarkerName
$PreflightReceipt = Join-Path $RunEvidence 'preflight.txt'
$CleanupReceipt = Join-Path $RunEvidence 'cleanup.txt'
$HadScratch = $false
$PytestStarted = $false
$CompressionProbeStarted = $false
$PytestExit = $null
$TestProcess = $null
$TestProcessCreationDate = ''
$TestProcessTreeVerified = $false
$UnexpectedTestDescendants = $false
$ObservedTestDescendants = @{}
$FreeBytesAtStart = [int64]-1
$FreeBytesAtLaunch = [int64]-1
$FreeBytesAfter = [int64]-1
$MinimumObservedFreeBytes = [int64]::MaxValue
$MinimumObservedAtUtc = ''
$FinalExit = 0
$RunErrorMessage = ''
$OldEnvironment = @{}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Get-DriveInfoForPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $root = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Path))
    return New-Object -TypeName System.IO.DriveInfo -ArgumentList $root
}

function Assert-NotReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing reparse-point path: $Path"
    }
}

function Assert-ContainedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Parent
    )
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    $candidateFull = [System.IO.Path]::GetFullPath($Candidate)
    if (-not $candidateFull.StartsWith($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes its permitted parent: $candidateFull"
    }
    return $candidateFull
}

function Get-GitPathList {
    param([Parameter(Mandatory = $true)][string[]]$GitArguments)
    Push-Location -LiteralPath $RepoRoot
    try {
        $paths = & git -c core.quotepath=false @GitArguments
        $gitExit = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($gitExit -ne 0) {
        throw "git $($GitArguments -join ' ') failed with exit $gitExit"
    }
    return @($paths | Where-Object { $_ -ne '' })
}

function Test-AllowedUntrackedSource {
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    $normalized = $RelativePath.Replace('\', '/')
    $extension = [System.IO.Path]::GetExtension($normalized).ToLowerInvariant()
    $sourceExtensions = @(
        '.py', '.pyi', '.pyw', '.toml', '.ini', '.cfg', '.conf', '.json', '.jsonl',
        '.yaml', '.yml', '.md', '.txt', '.sha256', '.sh', '.ps1', '.psm1', '.spec',
        '.service', '.target', '.desktop', '.html', '.css', '.tcss', '.js', '.ts',
        '.c', '.h', '.inc', '.mk', '.patch', '.diff', '.xml', '.svg', '.bmp', '.png',
        '.ppm', '.log', '.exit', '.bin', '.rom'
    )
    if ($normalized.StartsWith('tests/', [System.StringComparison]::OrdinalIgnoreCase) -or
        $normalized.StartsWith('autoloader/', [System.StringComparison]::OrdinalIgnoreCase) -or
        $normalized.StartsWith('usb_builder/', [System.StringComparison]::OrdinalIgnoreCase) -or
        $normalized.StartsWith('docs/', [System.StringComparison]::OrdinalIgnoreCase)) {
        return $sourceExtensions -contains $extension
    }
    if ($normalized.Contains('/')) {
        return $false
    }
    if ($normalized -in @('Makefile', 'LICENSE', 'NOTICE', '.gitignore', '.gitattributes')) {
        return $true
    }
    return $sourceExtensions -contains $extension
}

function Get-SourceEntries {
    $tracked = Get-GitPathList -GitArguments @('ls-files', '--cached')
    $untracked = Get-GitPathList -GitArguments @('ls-files', '--others', '--exclude-standard')
    $trackedSet = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($path in $tracked) { [void]$trackedSet.Add([string]$path) }

    $all = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($path in $tracked) { [void]$all.Add([string]$path) }
    foreach ($path in $untracked) {
        if (Test-AllowedUntrackedSource -RelativePath ([string]$path)) { [void]$all.Add([string]$path) }
    }

    $excludedPrefixes = @(
        '.venv/', '.git/', '.pytest_cache/', '__pycache__/', 'build/', 'dist/', 'target/',
        'docs/v1/evidence/windows-current-regression-20261026/attempt-13/'
    )
    $entries = New-Object System.Collections.Generic.List[object]
    foreach ($relative in $all) {
        $normalized = ([string]$relative).Replace('\', '/')
        if ($normalized -match '(^|/)(\.pytesttmp[^/]*|__pycache__|\.mypy_cache|\.ruff_cache)(/|$)') { continue }
        if ($normalized -match '^\.pytesttmp[^/]*(/|$)') { continue }
        $excluded = $false
        foreach ($prefix in $excludedPrefixes) {
            if ($normalized.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                $excluded = $true
                break
            }
        }
        if ($excluded) { continue }
        if (-not $trackedSet.Contains([string]$relative) -and -not (Test-AllowedUntrackedSource -RelativePath $normalized)) { continue }

        $sourcePath = Assert-ContainedPath -Candidate (Join-Path $RepoRoot $normalized.Replace('/', '\')) -Parent $RepoRoot
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            throw "Manifest path disappeared or is not a file: $sourcePath"
        }
        Assert-NotReparsePoint -Path $sourcePath
        $file = Get-Item -LiteralPath $sourcePath -Force
        $entry = [pscustomobject]@{
            RelativePath = $normalized
            SourcePath = $sourcePath
            Length = [int64]$file.Length
            IsTracked = [bool]$trackedSet.Contains([string]$relative)
        }
        $entries.Add($entry)
    }
    return @($entries | Sort-Object RelativePath)
}

function Get-FileManifestRows {
    param(
        [Parameter(Mandatory = $true)][object[]]$Entries,
        [Parameter(Mandatory = $true)][string]$BasePath,
        [switch]$UseSourcePaths
    )
    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($entry in $Entries) {
        if ($UseSourcePaths) { $path = [string]$entry.SourcePath }
        else { $path = Join-Path $BasePath ([string]$entry.RelativePath).Replace('/', '\') }
        $path = Assert-ContainedPath -Candidate $path -Parent $BasePath
        Assert-NotReparsePoint -Path $path
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        $rows.Add([pscustomobject]@{ Path = [string]$entry.RelativePath; SHA256 = $hash; Bytes = [int64](Get-Item -LiteralPath $path).Length })
    }
    return @($rows | Sort-Object Path)
}

function Save-ManifestCsv {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][object[]]$Rows)
    $csv = @($Rows | Select-Object Path, SHA256, Bytes | ConvertTo-Csv -NoTypeInformation) -join "`r`n"
    Write-Utf8NoBom -Path $Path -Content ($csv + "`r`n")
}

function ConvertTo-WindowsArgument {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes++
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (2 * $slashes + 1)))
            [void]$builder.Append('"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) { [void]$builder.Append(('\' * $slashes)); $slashes = 0 }
        [void]$builder.Append($character)
    }
    if ($slashes -gt 0) { [void]$builder.Append(('\' * (2 * $slashes))) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Get-CompressedFileSizeBytes {
    param([Parameter(Mandatory = $true)][string]$Path)
    $className = 'CompressedSize_' + [Guid]::NewGuid().ToString('N')
    $typeDefinition = @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace LibreCoreAttempt13 {
    public static class $className {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "GetCompressedFileSizeW")]
        private static extern uint GetCompressedFileSize(string fileName, out uint fileSizeHigh);
        public static long Read(string fileName) {
            uint high;
            uint low = GetCompressedFileSize(fileName, out high);
            int error = Marshal.GetLastWin32Error();
            if (low == UInt32.MaxValue && error != 0) throw new Win32Exception(error);
            ulong size = ((ulong)high << 32) | low;
            if (size > Int64.MaxValue) throw new OverflowException("Compressed size exceeds Int64.");
            return (long)size;
        }
    }
}
"@
    $measurementType = Add-Type -TypeDefinition $typeDefinition -PassThru -ErrorAction Stop
    $method = $measurementType[0].GetMethod('Read')
    try {
        return [int64]$method.Invoke($null, [object[]]@($Path))
    }
    catch [System.Reflection.TargetInvocationException] {
        $inner = $_.Exception.InnerException
        if ($null -ne $inner) { throw "GetCompressedFileSizeW failed: $($inner.Message)" }
        throw "GetCompressedFileSizeW failed: $($_.Exception.Message)"
    }
}

function Get-TestProcessDescendants {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)
    $processes = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
    $parentIds = New-Object 'System.Collections.Generic.HashSet[uint32]'
    [void]$parentIds.Add([uint32]$RootProcessId)
    $descendants = New-Object System.Collections.Generic.List[object]
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($item in $processes) {
            $processId = [uint32]$item.ProcessId
            if ($parentIds.Contains([uint32]$item.ParentProcessId) -and -not $parentIds.Contains($processId)) {
                [void]$parentIds.Add($processId)
                $descendants.Add($item)
                $ObservedTestDescendants[[string]$processId] = [string]$item.CreationDate
                $changed = $true
            }
        }
    }
    return @($descendants)
}

function Get-CimProcessById {
    param([Parameter(Mandatory = $true)][uint32]$ProcessId)
    $matches = @(Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop)
    if ($matches.Count -gt 1) { throw "Multiple Win32_Process rows found for PID $ProcessId." }
    if ($matches.Count -eq 0) { return $null }
    return $matches[0]
}

function Assert-CimProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][uint32]$ProcessId,
        [Parameter(Mandatory = $true)][string]$ExpectedCreationDate
    )
    if ([string]::IsNullOrWhiteSpace($ExpectedCreationDate)) {
        throw "Missing captured CreationDate for process PID $ProcessId; refusing taskkill."
    }
    $current = Get-CimProcessById -ProcessId $ProcessId
    if ($null -eq $current -or [string]$current.CreationDate -ne $ExpectedCreationDate) {
        throw "PID $ProcessId is absent or its CreationDate changed; refusing taskkill."
    }
    return $current
}

function Stop-AndVerifyTestProcessTree {
    param(
        [Parameter(Mandatory = $true)][int]$RootProcessId,
        [Parameter(Mandatory = $true)][string]$RootCreationDate,
        [Parameter(Mandatory = $true)][string]$ReceiptPath,
        [switch]$NormalExit
    )
    $treeReceipt = New-Object System.Collections.Generic.List[string]
    $treeReceipt.Add("root_pid=$RootProcessId;created=$RootCreationDate")
    try {
        $rootNow = Get-CimProcessById -ProcessId ([uint32]$RootProcessId)
        if ($null -ne $rootNow) {
            if ([string]$rootNow.CreationDate -ne $RootCreationDate) {
                throw "Root PID $RootProcessId was reused; refusing to inspect or terminate its process tree."
            }
            $descendants = @(Get-TestProcessDescendants -RootProcessId $RootProcessId)
        }
        else {
            # The captured pytest root has exited. Only descendants observed while it was alive are candidates.
            $descendants = @()
        }
        $liveAtStart = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
        foreach ($observedPid in @($ObservedTestDescendants.Keys)) {
            $expectedCreation = [string]$ObservedTestDescendants[$observedPid]
            $same = @($liveAtStart | Where-Object { [string]$_.ProcessId -eq [string]$observedPid -and [string]$_.CreationDate -eq $expectedCreation })
            foreach ($child in $same) {
                $already = @($descendants | Where-Object { [string]$_.ProcessId -eq [string]$observedPid -and [string]$_.CreationDate -eq $expectedCreation })
                if ($already.Count -eq 0) {
                    $descendants += $child
                }
            }
        }
        if ($NormalExit -and $descendants.Count -gt 0) { $script:UnexpectedTestDescendants = $true }
        foreach ($child in $descendants) {
            $childId = [string]$child.ProcessId
            $expectedCreation = [string]$ObservedTestDescendants[$childId]
            $treeReceipt.Add("observed_descendant_pid=$childId;created=$expectedCreation;name=$($child.Name)")
        }
        if ($descendants.Count -eq 0) {
            $treeReceipt.Add('state=no-observed-descendant-writers')
            Write-Utf8NoBom -Path $ReceiptPath -Content (($treeReceipt -join "`r`n") + "`r`n")
            return $true
        }
        foreach ($child in $descendants) {
            $childId = [int]$child.ProcessId
            $childKey = [string]$childId
            if (-not $ObservedTestDescendants.ContainsKey($childKey)) {
                throw "No captured CreationDate exists for observed descendant PID $childId; refusing taskkill."
            }
            $expectedCreation = [string]$ObservedTestDescendants[$childKey]
            $treeReceipt.Add("terminate_descendant_pid=$childId")
            $null = Assert-CimProcessIdentity -ProcessId ([uint32]$childId) -ExpectedCreationDate $expectedCreation
            & taskkill.exe /PID $childId /T /F 2>&1 | ForEach-Object { $treeReceipt.Add([string]$_) }
        }
        for ($attempt = 0; $attempt -lt 10; $attempt++) {
            Start-Sleep -Seconds 1
            $live = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
            $remaining = New-Object System.Collections.Generic.List[object]
            foreach ($childId in @($ObservedTestDescendants.Keys)) {
                $expectedCreation = [string]$ObservedTestDescendants[$childId]
                $same = @($live | Where-Object { [string]$_.ProcessId -eq $childId -and [string]$_.CreationDate -eq $expectedCreation })
                foreach ($found in $same) { $remaining.Add($found) }
            }
            if ($remaining.Count -eq 0) {
                $treeReceipt.Add($(if ($NormalExit) { 'state=unexpected-descendants-terminated;run-not-accepted' } else { 'state=observed-descendants-terminated' }))
                Write-Utf8NoBom -Path $ReceiptPath -Content (($treeReceipt -join "`r`n") + "`r`n")
                return $true
            }
        }
        $treeReceipt.Add('state=descendants-remain;preserve-scratch')
        Write-Utf8NoBom -Path $ReceiptPath -Content (($treeReceipt -join "`r`n") + "`r`n")
        return $false
    }
    catch {
        $treeReceipt.Add("state=verification-error;preserve-scratch;error=$($_.Exception.Message)")
        Write-Utf8NoBom -Path $ReceiptPath -Content (($treeReceipt -join "`r`n") + "`r`n")
        return $false
    }
}

function Read-AndValidateJunit {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$ReceiptPath)
    try {
        Assert-NotReparsePoint -Path $Path
        [xml]$document = [System.IO.File]::ReadAllText($Path)
        if ($document.DocumentElement.Name -ne 'testsuites') { throw 'JUnit root is not testsuites.' }
        $suites = @($document.SelectNodes('/testsuites/testsuite'))
        if ($suites.Count -ne 1) { throw "Expected one pytest testsuite; got $($suites.Count)." }
        $suite = $suites[0]
        $cases = @($suite.SelectNodes('./testcase'))
        $actualTests = [int]$suite.GetAttribute('tests')
        $actualFailures = [int]$suite.GetAttribute('failures')
        $actualErrors = [int]$suite.GetAttribute('errors')
        $actualSkipped = [int]$suite.GetAttribute('skipped')
        $nodeFailures = @($suite.SelectNodes('.//testcase/failure')).Count
        $nodeErrors = @($suite.SelectNodes('.//testcase/error')).Count
        $nodeSkipped = @($suite.SelectNodes('.//testcase/skipped')).Count
        if ($actualTests -ne $ExpectedTestCount -or $cases.Count -ne $ExpectedTestCount) {
            throw "JUnit is incomplete or changed: expected $ExpectedTestCount cases, tests=$actualTests, testcase nodes=$($cases.Count)."
        }
        if ($actualFailures -ne $nodeFailures -or $actualErrors -ne $nodeErrors -or $actualSkipped -ne $nodeSkipped) {
            throw 'JUnit aggregate totals do not match testcase result nodes.'
        }
        $windowsResults = New-Object System.Collections.Generic.List[string]
        foreach ($nodeId in $WindowsRequiredCases) {
            $separator = $nodeId.IndexOf('::')
            $className = $nodeId.Substring(0, $separator)
            $testName = $nodeId.Substring($separator + 2)
            $matches = @($cases | Where-Object { $_.GetAttribute('classname') -eq $className -and $_.GetAttribute('name') -eq $testName })
            if ($matches.Count -ne 1) { throw "Required Windows test missing or duplicated: $nodeId" }
            $case = $matches[0]
            if ($case.SelectSingleNode('./skipped') -or $case.SelectSingleNode('./failure') -or $case.SelectSingleNode('./error')) {
                throw "Required Windows test did not pass: $nodeId"
            }
            $windowsResults.Add("passed=$nodeId")
        }
        $actualPassed = $actualTests - $actualFailures - $actualErrors - $actualSkipped
        $summary = @(
            'status=complete'
            "tests=$actualTests"
            "passed=$actualPassed"
            "skipped=$actualSkipped"
            "failures=$actualFailures"
            "errors=$actualErrors"
            "expected_tests=$ExpectedTestCount"
            "windows_case=$($windowsResults[0])"
            "windows_case=$($windowsResults[1])"
        ) -join "`r`n"
        Write-Utf8NoBom -Path $ReceiptPath -Content ($summary + "`r`n")
        if ($actualFailures -ne 0 -or $actualErrors -ne 0) { return $false }
        return $true
    }
    catch {
        Write-Utf8NoBom -Path $ReceiptPath -Content ("status=invalid-or-incomplete`r`nerror=$($_.Exception.Message)`r`n")
        return $false
    }
}

function Write-RunHashManifest {
    param([Parameter(Mandatory = $true)][string]$Root)
    $lines = New-Object System.Collections.Generic.List[string]
    $files = Get-ChildItem -LiteralPath $Root -File -Force | Where-Object { $_.Name -notin @('SHA256SUMS', 'run-status.txt') } | Sort-Object Name
    foreach ($file in $files) {
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $lines.Add("$hash  $($file.Name)")
    }
    Write-Utf8NoBom -Path (Join-Path $Root 'SHA256SUMS') -Content (($lines -join "`r`n") + "`r`n")
}

function Restore-Environment {
    foreach ($name in $OldEnvironment.Keys) {
        $oldValue = $OldEnvironment[$name]
        if ($null -eq $oldValue) { Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath "Env:$name" -Value ([string]$oldValue) }
    }
}

function Write-CleanupReceipt {
    param([Parameter(Mandatory = $true)][string]$State, [string]$Details = '')
    $text = @(
        "state=$State"
        "utc=$([DateTime]::UtcNow.ToString('o'))"
        "scratch=$ScratchRoot"
        "scratch_created=$HadScratch"
        "details=$Details"
    ) -join "`r`n"
    Write-Utf8NoBom -Path $CleanupReceipt -Content ($text + "`r`n")
}

try {
    if ((Split-Path -Leaf $AttemptRoot) -ne $ExpectedAttempt -or
        (Split-Path -Leaf $EvidenceRoot) -ne $ExpectedBundle -or
        (Split-Path -Leaf $RepoRoot) -ne 'Libre_Core-AutoLoader') {
        throw 'Runner is not located in the expected repository attempt-13 directory.'
    }
    Assert-NotReparsePoint -Path $AttemptRoot
    Assert-NotReparsePoint -Path $EvidenceRoot
    Assert-NotReparsePoint -Path $RepoRoot

    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Refusing elevated execution. Run from a normal, non-administrator PowerShell session.'
    }

    if (Test-Path -LiteralPath $RunEvidence) { throw "Refusing to overwrite run evidence: $RunEvidence" }
    New-Item -ItemType Directory -Path $RunEvidence -ErrorAction Stop | Out-Null
    $driveInfo = Get-DriveInfoForPath -Path $RepoRoot
    $freeBefore = [int64]$driveInfo.AvailableFreeSpace
    $FreeBytesAtStart = $freeBefore
    $MinimumObservedFreeBytes = $freeBefore
    $MinimumObservedAtUtc = [DateTime]::UtcNow.ToString('o')
    $modeName = if ($PreflightOnly) { 'probe-only' } else { 'full-suite' }
    $preflightLines = @(
        'status=checking'
        "mode=$modeName"
        "utc=$([DateTime]::UtcNow.ToString('o'))"
        "repository=$RepoRoot"
        "attempt=$AttemptRoot"
        "drive_root=$RepoDriveRoot"
        "drive_format=$($driveInfo.DriveFormat)"
        "free_bytes_before=$freeBefore"
        "minimum_start_free_bytes=$MinimumStartFreeBytes"
        "minimum_live_free_bytes=$MinimumLiveFreeBytes"
        "maximum_snapshot_bytes=$MaximumSnapshotBytes"
        'pytest_started=false'
        'compression_probe_started=false'
    )

    if ($driveInfo.DriveFormat -ne 'NTFS') { throw "C: must be NTFS for the compressed scratch contract; detected $($driveInfo.DriveFormat)." }
    if ($PreflightOnly -and $freeBefore -lt $MinimumLiveFreeBytes) {
        $preflightLines[0] = 'status=blocked-below-probe-floor'
        Write-Utf8NoBom -Path $PreflightReceipt -Content (($preflightLines -join "`r`n") + "`r`n")
        throw "Probe-only mode blocked before scratch/probe creation: $freeBefore bytes free; requires at least $MinimumLiveFreeBytes bytes."
    }
    if (-not $PreflightOnly -and $freeBefore -lt $MinimumStartFreeBytes) {
        $preflightLines[0] = 'status=blocked-insufficient-free-space'
        Write-Utf8NoBom -Path $PreflightReceipt -Content (($preflightLines -join "`r`n") + "`r`n")
        throw "Preflight blocked before scratch/probe creation: $freeBefore bytes free; requires $MinimumStartFreeBytes (10 GiB)."
    }

    $pythonPath = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (-not $PreflightOnly) {
        if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw "Required venv Python is missing: $pythonPath" }
        Assert-NotReparsePoint -Path $pythonPath
        $gitCommand = Get-Command git.exe -ErrorAction Stop
    }
    if (-not (Test-Path -LiteralPath $ScratchParent -PathType Container)) { throw "Short scratch parent is missing: $ScratchParent" }
    Assert-NotReparsePoint -Path $ScratchParent

    $scratchDrive = Get-DriveInfoForPath -Path $ScratchParent
    if ($scratchDrive.Name -ne $driveInfo.Name) { throw 'Scratch and evidence must be on the same drive for a single capacity guard.' }
    if (Test-Path -LiteralPath $ScratchRoot) { throw "Refusing existing scratch path: $ScratchRoot" }
    New-Item -ItemType Directory -Path $ScratchRoot -ErrorAction Stop | Out-Null
    $HadScratch = $true
    Assert-ContainedPath -Candidate $ScratchRoot -Parent $ScratchParent | Out-Null
    Assert-NotReparsePoint -Path $ScratchRoot
    Write-Utf8NoBom -Path $ScratchMarker -Content ($ScratchToken + "`r`n")

    $compactOutput = & compact.exe /C /I /Q $ScratchRoot 2>&1
    $compactExit = $LASTEXITCODE
    Write-Utf8NoBom -Path (Join-Path $RunEvidence 'compression-setup.txt') -Content ((@("exit=$compactExit") + @($compactOutput | ForEach-Object { [string]$_ })) -join "`r`n" + "`r`n")
    if ($compactExit -ne 0) { throw "NTFS compression setup failed with exit $compactExit." }

    $probeDirectory = Join-Path $ScratchRoot 'compression-probe'
    $probeFile = Join-Path $probeDirectory 'probe.bin'
    $probeBytes = New-Object byte[] (1024 * 1024)
    for ($index = 0; $index -lt $probeBytes.Length; $index++) { $probeBytes[$index] = [byte]65 }
    $compressedFlag = [System.IO.FileAttributes]::Compressed
    $probeCompactCommand = 'compact.exe /C /I /Q "' + $probeFile + '"'
    $probeCompactCommands = New-Object 'System.Collections.Generic.List[string]'
    $probeCompactExitCodes = New-Object 'System.Collections.Generic.List[string]'
    $probeCompactOutput = New-Object 'System.Collections.Generic.List[string]'
    $probePassed = $false
    $probeError = ''
    $probeCleanupError = ''
    $probeDirectoryAttributes = 'unavailable'
    $probeFileAttributesBeforeCompact = 'unavailable'
    $probeFileAttributes = 'unavailable'
    $probeAllocationBeforeCompactBytes = [int64]-1
    $probeAllocationAfterCompactBytes = [int64]-1
    $probeAllocationAfterForceBytes = [int64]-1
    $probeAllocatedBytes = [int64]-1
    try {
        $CompressionProbeStarted = $true
        $probeStartedReceiptIndex = [Array]::IndexOf($preflightLines, 'compression_probe_started=false')
        if ($probeStartedReceiptIndex -lt 0) { throw 'The preflight receipt is missing its compression probe start field.' }
        $preflightLines[$probeStartedReceiptIndex] = 'compression_probe_started=true'
        New-Item -ItemType Directory -Path $probeDirectory -ErrorAction Stop | Out-Null
        $probeDirectoryAttributes = [System.IO.File]::GetAttributes($probeDirectory)
        [System.IO.File]::WriteAllBytes($probeFile, $probeBytes)
        $probeFileAttributesBeforeCompact = [System.IO.File]::GetAttributes($probeFile)
        $probeAllocationBeforeCompactBytes = Get-CompressedFileSizeBytes -Path $probeFile
        $probeCompactCommands.Add($probeCompactCommand)
        $compactOutputFirst = @(& compact.exe /C /I /Q $probeFile 2>&1 | ForEach-Object { [string]$_ })
        $compactExitFirst = [int]$LASTEXITCODE
        $probeCompactExitCodes.Add([string]$compactExitFirst)
        foreach ($line in $compactOutputFirst) { $probeCompactOutput.Add("first=$line") }
        $probeFileAttributes = [System.IO.File]::GetAttributes($probeFile)
        $probeAllocationAfterCompactBytes = Get-CompressedFileSizeBytes -Path $probeFile
        $probeAllocatedBytes = $probeAllocationAfterCompactBytes
        if ($compactExitFirst -ne 0) { throw "compact.exe /C /I /Q failed for the exact probe file with exit $compactExitFirst." }
        if ($probeAllocationAfterCompactBytes -ge $probeBytes.Length) {
            $probeCompactForceCommand = 'compact.exe /C /I /Q /F "' + $probeFile + '"'
            $probeCompactCommands.Add($probeCompactForceCommand)
            $compactForceOutput = @(& compact.exe /C /I /Q /F $probeFile 2>&1 | ForEach-Object { [string]$_ })
            $compactForceExit = [int]$LASTEXITCODE
            $probeCompactExitCodes.Add([string]$compactForceExit)
            foreach ($line in $compactForceOutput) { $probeCompactOutput.Add("force=$line") }
            $probeFileAttributes = [System.IO.File]::GetAttributes($probeFile)
            $probeAllocationAfterForceBytes = Get-CompressedFileSizeBytes -Path $probeFile
            $probeAllocatedBytes = $probeAllocationAfterForceBytes
            if ($compactForceExit -ne 0) { throw "compact.exe /C /I /Q /F failed for the exact probe file with exit $compactForceExit." }
        }
        if (($probeDirectoryAttributes -band $compressedFlag) -eq 0 -or ($probeFileAttributes -band $compressedFlag) -eq 0) {
            throw 'The compressed attribute was not present on the disposable directory and file after file-level compression.'
        }
        if ($probeAllocatedBytes -ge $probeBytes.Length) {
            throw "GetCompressedFileSizeW reported $probeAllocatedBytes bytes for a $($probeBytes.Length)-byte compressible probe."
        }
        $probePassed = $true
    }
    catch { $probeError = $_.Exception.Message }
    try {
        if (Test-Path -LiteralPath $probeFile) {
            Assert-ContainedPath -Candidate $probeFile -Parent $ScratchRoot | Out-Null
            Assert-NotReparsePoint -Path $probeFile
            Remove-Item -LiteralPath $probeFile -Force -ErrorAction Stop
        }
        if (Test-Path -LiteralPath $probeDirectory) {
            Assert-ContainedPath -Candidate $probeDirectory -Parent $ScratchRoot | Out-Null
            Assert-NotReparsePoint -Path $probeDirectory
            Remove-Item -LiteralPath $probeDirectory -Force -ErrorAction Stop
        }
        if ((Test-Path -LiteralPath $probeFile) -or (Test-Path -LiteralPath $probeDirectory)) {
            throw 'Exact compression probe cleanup did not complete.'
        }
    }
    catch { $probeCleanupError = $_.Exception.Message }
    if ($probeCleanupError) { $probePassed = $false }
    if (-not $probePassed -and -not $probeError) { $probeError = 'Compression allocation or exact probe cleanup validation failed.' }
    $probeStatus = if ($probePassed) { 'passed' } else { 'failed' }
    $probeAllocationBeforeCompactText = if ($probeAllocationBeforeCompactBytes -lt 0) { 'not-measured' } else { [string]$probeAllocationBeforeCompactBytes }
    $probeAllocationAfterCompactText = if ($probeAllocationAfterCompactBytes -lt 0) { 'not-measured' } else { [string]$probeAllocationAfterCompactBytes }
    $probeAllocationAfterForceText = if ($probeAllocationAfterForceBytes -lt 0) { 'not-measured' } else { [string]$probeAllocationAfterForceBytes }
    $probeFinalAllocationText = if ($probeAllocatedBytes -lt 0) { 'not-measured' } else { [string]$probeAllocatedBytes }
    Write-Utf8NoBom -Path (Join-Path $RunEvidence 'compression-probe.txt') -Content ((@(
        "status=$probeStatus"
        "utc=$([DateTime]::UtcNow.ToString('o'))"
        "probe_filesystem=$($driveInfo.DriveFormat)"
        "directory_attributes=$probeDirectoryAttributes"
        "file_attributes_before_compact=$probeFileAttributesBeforeCompact"
        "file_attributes=$probeFileAttributes"
        "compact_target=$probeFile"
        "compact_commands=$($probeCompactCommands -join ' || ')"
        "compact_exit_codes=$($probeCompactExitCodes -join ',')"
        "compact_output=$($probeCompactOutput -join ' | ')"
        "probe_logical_bytes=$($probeBytes.Length)"
        "probe_allocation_before_compact_bytes=$probeAllocationBeforeCompactText"
        "probe_allocation_after_compact_bytes=$probeAllocationAfterCompactText"
        "probe_allocation_after_force_bytes=$probeAllocationAfterForceText"
        "probe_compressed_allocation_bytes=$probeFinalAllocationText"
        "probe_error=$probeError"
        "cleanup_error=$probeCleanupError"
        "probe_file_removed=$(-not (Test-Path -LiteralPath $probeFile))"
        "probe_directory_removed=$(-not (Test-Path -LiteralPath $probeDirectory))"
    ) -join "`r`n") + "`r`n")
    if (-not $probePassed) { throw "NTFS compression probe failed: $probeError $probeCleanupError" }
    $freeAfterProbe = [int64](Get-DriveInfoForPath -Path $RepoRoot).AvailableFreeSpace
    if ($freeAfterProbe -lt $MinimumLiveFreeBytes) { throw "Probe-only live floor reached after probe cleanup: $freeAfterProbe bytes." }
    if ($freeAfterProbe -lt $MinimumObservedFreeBytes) {
        $MinimumObservedFreeBytes = $freeAfterProbe
        $MinimumObservedAtUtc = [DateTime]::UtcNow.ToString('o')
    }
    if ($PreflightOnly) {
        $preflightLines[0] = 'status=probe-only-passed'
        $preflightLines += 'compression_probe=passed'
        $preflightLines += 'source_snapshot=not-created'
        $preflightLines += "free_bytes_after_probe=$freeAfterProbe"
        Write-Utf8NoBom -Path $PreflightReceipt -Content (($preflightLines -join "`r`n") + "`r`n")
        $FinalExit = 0
    }
    else {
    $sourceEntries = Get-SourceEntries
    if ($sourceEntries.Count -eq 0) { throw 'The current source manifest is empty.' }
    $snapshotBytes = [int64](($sourceEntries | Measure-Object -Property Length -Sum).Sum)
    $preflightLines += "source_file_count=$($sourceEntries.Count)"
    $preflightLines += "source_logical_bytes=$snapshotBytes"
    if ($snapshotBytes -gt $MaximumSnapshotBytes) {
        $preflightLines[0] = 'status=blocked-snapshot-too-large'
        Write-Utf8NoBom -Path $PreflightReceipt -Content (($preflightLines -join "`r`n") + "`r`n")
        throw "Source snapshot estimate $snapshotBytes exceeds the $MaximumSnapshotBytes byte cap."
    }

    $sourceRoot = Join-Path $ScratchRoot 'source'
    $testTempRoot = Join-Path $ScratchRoot 'pytest-temp'
    $systemTempRoot = Join-Path $ScratchRoot 'system-tmp'
    $gitTemplateRoot = Join-Path $ScratchRoot 'git-template-empty'
    foreach ($directory in @($sourceRoot, $testTempRoot, $systemTempRoot, $gitTemplateRoot)) {
        New-Item -ItemType Directory -Path $directory -ErrorAction Stop | Out-Null
    }
    foreach ($entry in $sourceEntries) {
        $destination = Assert-ContainedPath -Candidate (Join-Path $sourceRoot $entry.RelativePath.Replace('/', '\')) -Parent $sourceRoot
        $destinationDirectory = Split-Path -Parent $destination
        if (-not (Test-Path -LiteralPath $destinationDirectory -PathType Container)) {
            New-Item -ItemType Directory -Path $destinationDirectory -Force -ErrorAction Stop | Out-Null
        }
        if (Test-Path -LiteralPath $destination) { throw "Snapshot destination collision: $destination" }
        Copy-Item -LiteralPath $entry.SourcePath -Destination $destination -ErrorAction Stop
    }
    $sourceBefore = Get-FileManifestRows -Entries $sourceEntries -BasePath $RepoRoot -UseSourcePaths
    $snapshotManifest = Get-FileManifestRows -Entries $sourceEntries -BasePath $sourceRoot
    if (($sourceBefore | ConvertTo-Json -Depth 4 -Compress) -ne ($snapshotManifest | ConvertTo-Json -Depth 4 -Compress)) {
        throw 'Staged source snapshot does not match the source manifest.'
    }
    Save-ManifestCsv -Path (Join-Path $RunEvidence 'source-manifest-before.csv') -Rows $sourceBefore
    Save-ManifestCsv -Path (Join-Path $RunEvidence 'snapshot-manifest.csv') -Rows $snapshotManifest

    $freeAtLaunch = [int64](Get-DriveInfoForPath -Path $RepoRoot).AvailableFreeSpace
    if ($freeAtLaunch -lt $MinimumLiveFreeBytes) { throw "Live free-space floor reached before pytest launch: $freeAtLaunch bytes." }
    $FreeBytesAtLaunch = $freeAtLaunch
    if ($freeAtLaunch -lt $MinimumObservedFreeBytes) {
        $MinimumObservedFreeBytes = $freeAtLaunch
        $MinimumObservedAtUtc = [DateTime]::UtcNow.ToString('o')
    }

    $environmentNames = @('TEMP', 'TMP', 'TMPDIR', 'GIT_TEMPLATE_DIR', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0', 'PYTHONPATH')
    foreach ($name in $environmentNames) { $OldEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    $env:TEMP = $systemTempRoot
    $env:TMP = $systemTempRoot
    $env:TMPDIR = $systemTempRoot
    $env:GIT_TEMPLATE_DIR = $gitTemplateRoot
    $env:GIT_CONFIG_COUNT = '1'
    $env:GIT_CONFIG_KEY_0 = 'core.longpaths'
    $env:GIT_CONFIG_VALUE_0 = 'true'
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue

    $pytestVersion = & $pythonPath -m pytest --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "pytest version check failed: $($pytestVersion -join ' ')" }
    $pythonVersion = & $pythonPath -VV 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Python version check failed: $($pythonVersion -join ' ')" }
    $pytestArgs = @(
        '-m', 'pytest', '-p', 'no:cacheprovider',
        "--basetemp=$testTempRoot", "--junitxml=$(Join-Path $RunEvidence 'junit.xml')",
        '-q', '--color=no', 'tests'
    )
    $argumentLine = (@($pytestArgs | ForEach-Object { ConvertTo-WindowsArgument -Value ([string]$_) }) -join ' ')
    $commandReceipt = [ordered]@{
        executable = $pythonPath
        arguments = $pytestArgs
        windows_argument_line = $argumentLine
        working_directory = $sourceRoot
        stdout = Join-Path $RunEvidence 'pytest.log'
        stderr = Join-Path $RunEvidence 'pytest.stderr.log'
        exit_receipt = Join-Path $RunEvidence 'pytest.exit'
        source_snapshot_manifest = Join-Path $RunEvidence 'snapshot-manifest.csv'
        pytest_started = $false
    }
    Write-Utf8NoBom -Path (Join-Path $RunEvidence 'command.json') -Content ($commandReceipt | ConvertTo-Json -Depth 6)
    $environmentLines = @(
        "utc=$([DateTime]::UtcNow.ToString('o'))"
        "python=$($pythonVersion -join ' ')"
        "pytest=$($pytestVersion -join ' ')"
        "platform=$([Environment]::OSVersion.VersionString)"
        "powershell=$($PSVersionTable.PSVersion)"
        "GIT_TEMPLATE_DIR=$env:GIT_TEMPLATE_DIR"
        'GIT_CONFIG_COUNT=1'
        'GIT_CONFIG_KEY_0=core.longpaths'
        'GIT_CONFIG_VALUE_0=true'
        "TEMP=$env:TEMP"
        "TMP=$env:TMP"
        "TMPDIR=$env:TMPDIR"
        'PYTHONPATH=<cleared for snapshot isolation>'
        "free_bytes_at_launch=$freeAtLaunch"
    )
    Write-Utf8NoBom -Path (Join-Path $RunEvidence 'environment.txt') -Content (($environmentLines -join "`r`n") + "`r`n")
    $preflightLines[0] = 'status=passed'
    $preflightLines += "free_bytes_at_launch=$freeAtLaunch"
    $preflightLines += 'compression_probe=passed'
    $preflightLines += 'source_snapshot=verified'
    Write-Utf8NoBom -Path $PreflightReceipt -Content (($preflightLines -join "`r`n") + "`r`n")

    $stdoutPath = Join-Path $RunEvidence 'pytest.log'
    $stderrPath = Join-Path $RunEvidence 'pytest.stderr.log'
    $process = Start-Process -FilePath $pythonPath -ArgumentList $argumentLine -WorkingDirectory $sourceRoot -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru -WindowStyle Hidden
    $TestProcess = $process
    $PytestStarted = $true
    $commandReceipt.pytest_started = $true
    $commandReceipt.process_id = $process.Id
    $rootIdentity = $null
    for ($identityAttempt = 0; $identityAttempt -lt 10 -and $null -eq $rootIdentity; $identityAttempt++) {
        $process.Refresh()
        if ($process.HasExited) { break }
        $rootIdentity = Get-CimProcessById -ProcessId ([uint32]$process.Id)
        if ($null -eq $rootIdentity) { Start-Sleep -Milliseconds 100 }
    }
    if ($null -ne $rootIdentity) { $TestProcessCreationDate = [string]$rootIdentity.CreationDate }
    $commandReceipt.process_creation_date = $TestProcessCreationDate
    Write-Utf8NoBom -Path (Join-Path $RunEvidence 'command.json') -Content ($commandReceipt | ConvertTo-Json -Depth 6)

    while (-not $process.HasExited) {
        Start-Sleep -Seconds 1
        $process.Refresh()
        if ($process.HasExited) { break }
        [void](Get-TestProcessDescendants -RootProcessId $process.Id)
        $freeNow = [int64](Get-DriveInfoForPath -Path $RepoRoot).AvailableFreeSpace
        if ($freeNow -lt $MinimumObservedFreeBytes) {
            $MinimumObservedFreeBytes = $freeNow
            $MinimumObservedAtUtc = [DateTime]::UtcNow.ToString('o')
        }
        if ($freeNow -lt $MinimumLiveFreeBytes) {
            $liveGuard = @(
                'status=stopped-live-space-floor'
                "utc=$([DateTime]::UtcNow.ToString('o'))"
                "free_bytes=$freeNow"
                "minimum_live_free_bytes=$MinimumLiveFreeBytes"
                "process_id=$($process.Id)"
                'action=taskkill-process-tree'
            ) -join "`r`n"
            Write-Utf8NoBom -Path (Join-Path $RunEvidence 'live-space-guard.txt') -Content ($liveGuard + "`r`n")
            $null = Assert-CimProcessIdentity -ProcessId ([uint32]$process.Id) -ExpectedCreationDate $TestProcessCreationDate
            & taskkill.exe /PID $process.Id /T /F 2>&1 | Out-File -LiteralPath (Join-Path $RunEvidence 'taskkill.log') -Encoding utf8
            $process.Refresh()
            if (-not $process.HasExited -and -not $process.WaitForExit(15000)) {
                throw 'Live-space guard could not confirm pytest process-tree termination; scratch will be preserved.'
            }
            $TestProcessTreeVerified = Stop-AndVerifyTestProcessTree -RootProcessId $process.Id -RootCreationDate $TestProcessCreationDate -ReceiptPath (Join-Path $RunEvidence 'process-tree.txt')
            if (-not $TestProcessTreeVerified) { throw 'Live-space guard could not confirm descendant termination; scratch will be preserved.' }
            $PytestExit = 125
            break
        }
    }
    if ($null -eq $PytestExit) { $process.WaitForExit(); $PytestExit = [int]$process.ExitCode }
    if (-not $TestProcessTreeVerified) {
        $TestProcessTreeVerified = Stop-AndVerifyTestProcessTree -RootProcessId $process.Id -RootCreationDate $TestProcessCreationDate -ReceiptPath (Join-Path $RunEvidence 'process-tree.txt') -NormalExit
    }
    if (-not $TestProcessTreeVerified) { throw 'Process descendants could not be confirmed stopped; scratch will be preserved.' }
    if ($UnexpectedTestDescendants) { $FinalExit = 1 }
    Write-Utf8NoBom -Path (Join-Path $RunEvidence 'pytest.exit') -Content ([string]$PytestExit + "`r`n")
    $FreeBytesAfter = [int64](Get-DriveInfoForPath -Path $RepoRoot).AvailableFreeSpace
    if ($FreeBytesAfter -lt $MinimumObservedFreeBytes) {
        $MinimumObservedFreeBytes = $FreeBytesAfter
        $MinimumObservedAtUtc = [DateTime]::UtcNow.ToString('o')
    }
    Write-Utf8NoBom -Path (Join-Path $RunEvidence 'space.txt') -Content ((@(
        "free_bytes_at_start=$FreeBytesAtStart"
        "free_bytes_at_launch=$FreeBytesAtLaunch"
        "free_bytes_minimum_sampled=$MinimumObservedFreeBytes"
        "minimum_sample_utc=$MinimumObservedAtUtc"
        "free_bytes_after_pytest=$FreeBytesAfter"
        "live_floor_bytes=$MinimumLiveFreeBytes"
    ) -join "`r`n") + "`r`n")
    $junitValid = Read-AndValidateJunit -Path (Join-Path $RunEvidence 'junit.xml') -ReceiptPath (Join-Path $RunEvidence 'junit-validation.txt')
    if ($PytestExit -eq 0 -and $junitValid -and -not $UnexpectedTestDescendants) { $FinalExit = 0 } else { $FinalExit = 1 }

    $sourceAfter = Get-FileManifestRows -Entries (Get-SourceEntries) -BasePath $RepoRoot -UseSourcePaths
    Save-ManifestCsv -Path (Join-Path $RunEvidence 'source-manifest-after.csv') -Rows $sourceAfter
    $sourceStable = (($sourceBefore | ConvertTo-Json -Depth 4 -Compress) -eq ($sourceAfter | ConvertTo-Json -Depth 4 -Compress))
    Write-Utf8NoBom -Path (Join-Path $RunEvidence 'source-stability.txt') -Content ("status=$(if ($sourceStable) {'unchanged'} else {'changed'})`r`n" + "source_manifest_before_sha256=$((Get-FileHash -LiteralPath (Join-Path $RunEvidence 'source-manifest-before.csv') -Algorithm SHA256).Hash.ToLowerInvariant())`r`nsource_manifest_after_sha256=$((Get-FileHash -LiteralPath (Join-Path $RunEvidence 'source-manifest-after.csv') -Algorithm SHA256).Hash.ToLowerInvariant())`r`n")
    if (-not $sourceStable) { $FinalExit = 1 }
    }
}
catch {
    $FinalExit = 1
    $message = $_.Exception.Message
    $RunErrorMessage = $message
    if (-not (Test-Path -LiteralPath $RunEvidence)) {
        if (-not (Test-Path -LiteralPath $AttemptRoot -PathType Container)) { throw }
        $RunEvidence = Join-Path $AttemptRoot ('run-' + $UtcId)
        if (-not (Test-Path -LiteralPath $RunEvidence)) { New-Item -ItemType Directory -Path $RunEvidence -ErrorAction SilentlyContinue | Out-Null }
    }
    if (-not (Test-Path -LiteralPath $PreflightReceipt)) {
        $free = try { [int64](Get-DriveInfoForPath -Path $RepoRoot).AvailableFreeSpace } catch { -1 }
        $preflightStatus = if ($PreflightOnly -and $HadScratch -and $CompressionProbeStarted) { 'probe-only-failed' } else { 'aborted' }
        Write-Utf8NoBom -Path $PreflightReceipt -Content ("status=$preflightStatus`r`nmode=$(if ($PreflightOnly) {'probe-only'} else {'full-suite'})`r`nerror=$message`r`nfree_bytes=$free`r`nminimum_start_free_bytes=$MinimumStartFreeBytes`r`ncompression_probe_started=$CompressionProbeStarted`r`npytest_started=$PytestStarted`r`n")
    }
}
finally {
    Restore-Environment
    $SafeToRemoveScratch = $true
    if ($PytestStarted -and -not $TestProcessTreeVerified) {
        $SafeToRemoveScratch = $false
        try {
            if ($null -ne $TestProcess) {
                $TestProcess.Refresh()
                if (-not $TestProcess.HasExited) {
                    $null = Assert-CimProcessIdentity -ProcessId ([uint32]$TestProcess.Id) -ExpectedCreationDate $TestProcessCreationDate
                    & taskkill.exe /PID $TestProcess.Id /T /F 2>&1 | Out-File -LiteralPath (Join-Path $RunEvidence 'taskkill-finally.log') -Encoding utf8
                    $TestProcess.Refresh()
                    if (-not $TestProcess.HasExited -and -not $TestProcess.WaitForExit(15000)) {
                        throw 'Pytest process did not exit after tree termination request.'
                    }
                }
                $TestProcess.Refresh()
                if ($TestProcess.HasExited) {
                    $TestProcessTreeVerified = Stop-AndVerifyTestProcessTree -RootProcessId $TestProcess.Id -RootCreationDate $TestProcessCreationDate -ReceiptPath (Join-Path $RunEvidence 'process-tree-finally.txt')
                    $SafeToRemoveScratch = $TestProcessTreeVerified
                }
            }
        }
        catch {
            $SafeToRemoveScratch = $false
            try { Write-Utf8NoBom -Path (Join-Path $RunEvidence 'process-tree-finally-error.txt') -Content ($_.Exception.Message + "`r`n") } catch { }
        }
    }
    if ($HadScratch) {
        if (-not $SafeToRemoveScratch) {
            $FinalExit = 1
            Write-CleanupReceipt -State 'preserved-process-tree-unverified' -Details 'The pytest process tree could not be proven stopped; scratch was preserved.'
        }
        else { try {
            $resolvedScratch = Assert-ContainedPath -Candidate $ScratchRoot -Parent $ScratchParent
            Assert-NotReparsePoint -Path $resolvedScratch
            if (-not (Test-Path -LiteralPath $ScratchMarker -PathType Leaf)) { throw 'Ownership marker is missing; preserving scratch.' }
            $markerContents = [System.IO.File]::ReadAllText($ScratchMarker).Trim()
            if ($markerContents -ne $ScratchToken) { throw 'Ownership marker does not match; preserving scratch.' }
            $reparseChildren = @(Get-ChildItem -LiteralPath $resolvedScratch -Force -Recurse -ErrorAction Stop | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
            if ($reparseChildren.Count -gt 0) { throw 'Reparse points found in scratch; preserving scratch.' }
            Remove-Item -LiteralPath $resolvedScratch -Recurse -Force -ErrorAction Stop
            if (Test-Path -LiteralPath $resolvedScratch) { throw 'Scratch root remains after cleanup.' }
            Write-CleanupReceipt -State 'removed' -Details 'Exact GUID-named scratch root removed after marker and reparse checks.'
        }
        catch {
            $FinalExit = 1
            Write-CleanupReceipt -State 'preserved-cleanup-check-failed' -Details $_.Exception.Message
        } }
    }
    elseif ($null -ne $RunEvidence -and (Test-Path -LiteralPath $RunEvidence -PathType Container)) {
        Write-CleanupReceipt -State 'not-created' -Details 'Capacity or structural preflight stopped before any scratch path or compression probe was created.'
    }
    if (Test-Path -LiteralPath $RunEvidence -PathType Container) {
        $hashManifestOk = $false
        $hashManifestError = ''
        $manifestHash = ''
        try {
            Write-RunHashManifest -Root $RunEvidence
            $manifestHash = (Get-FileHash -LiteralPath (Join-Path $RunEvidence 'SHA256SUMS') -Algorithm SHA256).Hash.ToLowerInvariant()
            $hashManifestOk = $true
        }
        catch {
            $FinalExit = 1
            $hashManifestError = $_.Exception.Message
        }
        $state = if (-not $hashManifestOk) { 'run-hash-manifest-failed' } elseif ($PreflightOnly -and $HadScratch -and $FinalExit -eq 0) { 'probe-only-passed' } elseif ($PreflightOnly -and $HadScratch -and $CompressionProbeStarted) { 'probe-only-failed' } elseif ($PytestStarted -and $PytestExit -eq 0 -and $FinalExit -eq 0) { 'completed-exit-0' } elseif ($PytestStarted) { 'completed-nonzero-or-incomplete' } else { 'prelaunch-blocked' }
        $statusLines = @(
            "status=$state"
            "mode=$(if ($PreflightOnly) {'probe-only'} else {'full-suite'})"
            "utc=$([DateTime]::UtcNow.ToString('o'))"
            "pytest_started=$PytestStarted"
            "pytest_exit=$(if ($null -eq $PytestExit) {'not-started'} else {$PytestExit})"
            "run_hash_manifest=$(if ($hashManifestOk) {'passed'} else {'failed'})"
            "run_hash_manifest_sha256=$manifestHash"
            "run_hash_manifest_error=$hashManifestError"
            "error=$RunErrorMessage"
        )
        Write-Utf8NoBom -Path (Join-Path $RunEvidence 'run-status.txt') -Content (($statusLines -join "`r`n") + "`r`n")
    }
}

exit $FinalExit
