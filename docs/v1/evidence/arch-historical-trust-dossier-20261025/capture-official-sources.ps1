$ErrorActionPreference = 'Stop'
$outputRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$allowedHosts = @('archlinux.org', 'archive.archlinux.org', 'gitlab.archlinux.org')
$writeOut = @"
http_code=%{http_code}
url_effective=%{url_effective}
num_redirects=%{num_redirects}
ssl_verify_result=%{ssl_verify_result}
certs=%{certs}
"@
$requests = @(
    @{ id = 'master-keys-current'; method = 'GET'; url = 'https://archlinux.org/master-keys/'; output = 'master-keys-current.html' },
    @{ id = 'archlinux-keyring-package-page'; method = 'GET'; url = 'https://archlinux.org/packages/core/any/archlinux-keyring/'; output = 'archlinux-keyring-package-page.html' },
    @{ id = 'pacman-current-package-page'; method = 'GET'; url = 'https://archlinux.org/packages/core/x86_64/pacman/'; output = 'pacman-current-package-page.html' },
    @{ id = 'keyring-source-commit'; method = 'GET'; url = 'https://gitlab.archlinux.org/archlinux/packaging/packages/archlinux-keyring/-/commit/56acf30f9492d6b6e9cc7c16fd1d969afc462046'; output = 'archlinux-keyring-source-commit.html' },
    @{ id = 'dated-core-index'; method = 'HEAD'; url = 'https://archive.archlinux.org/repos/2026/09/30/core/os/x86_64/'; output = 'dated-core-index.headers' },
    @{ id = 'dated-core-db'; method = 'HEAD'; url = 'https://archive.archlinux.org/repos/2026/09/30/core/os/x86_64/core.db'; output = 'dated-core-db.headers' },
    @{ id = 'dated-extra-db'; method = 'HEAD'; url = 'https://archive.archlinux.org/repos/2026/09/30/extra/os/x86_64/extra.db'; output = 'dated-extra-db.headers' },
    @{ id = 'dated-keyring-archive-metadata'; method = 'HEAD'; url = 'https://archive.archlinux.org/repos/2026/09/30/core/os/x86_64/archlinux-keyring-20260909-1-any.pkg.tar.zst'; output = 'dated-keyring-archive.headers' },
    @{ id = 'dated-pacman-archive-metadata'; method = 'HEAD'; url = 'https://archive.archlinux.org/repos/2026/09/30/core/os/x86_64/pacman-7.1.0.r9.g54d9411-2-x86_64.pkg.tar.zst'; output = 'dated-pacman-archive.headers' }
)
$records = @()
foreach ($request in $requests) {
    $uri = [Uri]$request.url
    if ($uri.Scheme -ne 'https' -or $uri.Host -notin $allowedHosts) {
        throw "Refusing non-allowlisted URL: $($request.url)"
    }
    $bodyPath = Join-Path $outputRoot $request.output
    $headerPath = Join-Path $outputRoot ($request.id + '.response-headers.txt')
    $transportPath = Join-Path $outputRoot ($request.id + '.transport.txt')
    $stderrPath = Join-Path $outputRoot ($request.id + '.stderr.txt')
    $exitPath = Join-Path $outputRoot ($request.id + '.exit')
    $args = @('--noproxy', '*', '--proto', '=https', '--proto-redir', '=https', '--max-redirs', '3', '--max-time', '30', '--max-filesize', '524288', '--silent', '--show-error', '--location', '--dump-header', $headerPath, '--write-out', $writeOut)
    if ($request.method -eq 'HEAD') {
        $args += @('--head', '--output', 'NUL')
    } else {
        $args += @('--output', $bodyPath)
    }
    $args += $request.url
    $commandLine = 'curl.exe ' + (($args | ForEach-Object { '"' + ($_ -replace '"', '""') + '"' }) -join ' ')
    [IO.File]::WriteAllText((Join-Path $outputRoot ($request.id + '.argv.txt')), $commandLine + "`r`n", [Text.Encoding]::UTF8)
    & curl.exe @args 1> $transportPath 2> $stderrPath
    $code = $LASTEXITCODE
    [IO.File]::WriteAllText($exitPath, "$code`r`n", [Text.Encoding]::ASCII)
    $transport = Get-Content -LiteralPath $transportPath -Raw
    $effective = [regex]::Match($transport, '(?m)^url_effective=(.+)$').Groups[1].Value.Trim()
    if ($effective) {
        $finalUri = [Uri]$effective
        if ($finalUri.Scheme -ne 'https' -or $finalUri.Host -notin $allowedHosts) {
            throw "Refusing non-allowlisted redirect result for $($request.id): $effective"
        }
    }
    $bodyReceived = $request.method -ne 'HEAD' -and (Test-Path -LiteralPath $bodyPath)
    $records += [pscustomobject]@{ id = $request.id; method = $request.method; requested_url = $request.url; effective_url = $effective; exit_code = $code; body_received = $bodyReceived; output_file = if ($bodyReceived) { $request.output } else { $null }; output_bytes = if ($bodyReceived) { (Get-Item -LiteralPath $bodyPath).Length } else { 0 }; headers_file = [IO.Path]::GetFileName($headerPath); transport_file = [IO.Path]::GetFileName($transportPath); stderr_file = [IO.Path]::GetFileName($stderrPath) }
}
$records | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $outputRoot 'capture-index.json') -Encoding UTF8
Write-Output "Completed $($records.Count) bounded official-source requests. Exit codes: $((($records | ForEach-Object { $_.exit_code }) -join ','))"
