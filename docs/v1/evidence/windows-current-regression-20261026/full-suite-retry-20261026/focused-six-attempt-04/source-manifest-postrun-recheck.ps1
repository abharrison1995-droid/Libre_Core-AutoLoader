$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..\..\..\..')).Path
$manifestPath = Join-Path $PSScriptRoot 'source-manifest.sha256'
$checked = 0
$mismatches = @()

foreach ($line in Get-Content -LiteralPath $manifestPath) {
    if ($line -notmatch '^([0-9a-f]{64})  (?:\./)?(.+)$') {
        throw "Invalid manifest row: $line"
    }
    $expected = $Matches[1]
    $relative = $Matches[2].Replace('/', [IO.Path]::DirectorySeparatorChar)
    $path = Join-Path $repoRoot $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $mismatches += "MISSING $relative"
        continue
    }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    $checked++
    if ($actual -ne $expected) {
        $mismatches += "HASH $relative expected=$expected actual=$actual"
    }
}

"manifest_sha256=$((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant())"
"paths_checked=$checked"
"mismatches=$($mismatches.Count)"
$mismatches
if ($mismatches.Count -ne 0) { exit 1 }
"result=PASS"
