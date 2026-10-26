$ErrorActionPreference = 'Stop'
$outputRoot = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'attempt-02-head-only'
if (Test-Path -LiteralPath $outputRoot) { throw "Refusing to overwrite existing output: $outputRoot" }
New-Item -ItemType Directory -Path $outputRoot | Out-Null
$allowedHosts = @('archive.archlinux.org')
$writeOut = @"
http_code=%{http_code}
url_effective=%{url_effective}
num_redirects=%{num_redirects}
ssl_verify_result=%{ssl_verify_result}
certs=%{certs}
"@
$requests = @(
    @{ id = 'dated-extra-db'; url = 'https://archive.archlinux.org/repos/2026/09/30/extra/os/x86_64/extra.db' },
    @{ id = 'dated-keyring-archive-metadata'; url = 'https://archive.archlinux.org/repos/2026/09/30/core/os/x86_64/archlinux-keyring-20260909-1-any.pkg.tar.zst' },
    @{ id = 'dated-pacman-archive-metadata'; url = 'https://archive.archlinux.org/repos/2026/09/30/core/os/x86_64/pacman-7.1.0.r9.g54d9411-2-x86_64.pkg.tar.zst' }
)
$records = @()
foreach ($request in $requests) {
    $uri = [Uri]$request.url
    if ($uri.Scheme -ne 'https' -or $uri.Host -notin $allowedHosts) { throw "Refusing non-allowlisted URL: $($request.url)" }
    $headerPath = Join-Path $outputRoot ($request.id + '.response-headers.txt')
    $transportPath = Join-Path $outputRoot ($request.id + '.transport.txt')
    $stderrPath = Join-Path $outputRoot ($request.id + '.stderr.txt')
    $exitPath = Join-Path $outputRoot ($request.id + '.exit')
    $args = @('--noproxy', '*', '--proto', '=https', '--proto-redir', '=https', '--max-redirs', '3', '--max-time', '30', '--silent', '--show-error', '--location', '--head', '--dump-header', $headerPath, '--output', 'NUL', '--write-out', $writeOut, $request.url)
    $commandLine = 'curl.exe ' + (($args | ForEach-Object { '"' + ($_ -replace '"', '""') + '"' }) -join ' ')
    [IO.File]::WriteAllText((Join-Path $outputRoot ($request.id + '.argv.txt')), $commandLine + "`r`n", [Text.Encoding]::UTF8)
    & curl.exe @args 1> $transportPath 2> $stderrPath
    $code = $LASTEXITCODE
    [IO.File]::WriteAllText($exitPath, "$code`r`n", [Text.Encoding]::ASCII)
    $transport = Get-Content -LiteralPath $transportPath -Raw
    $effective = [regex]::Match($transport, '(?m)^url_effective=(.+)$').Groups[1].Value.Trim()
    $finalUri = [Uri]$effective
    if ($finalUri.Scheme -ne 'https' -or $finalUri.Host -notin $allowedHosts) { throw "Refusing non-allowlisted redirect result: $effective" }
    $records += [pscustomobject]@{ id = $request.id; method = 'HEAD'; requested_url = $request.url; effective_url = $effective; exit_code = $code; body_received = $false; headers_file = [IO.Path]::GetFileName($headerPath); transport_file = [IO.Path]::GetFileName($transportPath); stderr_file = [IO.Path]::GetFileName($stderrPath) }
}
$records | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $outputRoot 'capture-index.json') -Encoding UTF8
Write-Output "Completed $($records.Count) HEAD-only archive metadata requests. Exit codes: $((($records | ForEach-Object { $_.exit_code }) -join ','))"
