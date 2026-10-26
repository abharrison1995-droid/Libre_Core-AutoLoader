param(
    [string]$RepositoryRoot = 'C:\Users\Alex\Desktop\Libre_Core-AutoLoader'
)

$ErrorActionPreference = 'Stop'
$script:Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$script:Target = [System.IO.Path]::GetFullPath($PSScriptRoot)
$script:SourceBundle = Join-Path $RepositoryRoot 'docs\v1\evidence\ubuntu-verifier-provenance-20261025'
$script:Curl = Join-Path $env:SystemRoot 'System32\curl.exe'
$script:RunExit = 0

function Write-Utf8([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $script:Utf8NoBom)
}

function Assert-Condition([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Invoke-CurlLogged([string]$Label, [string[]]$Arguments) {
    $base = Join-Path $script:Target $Label
    Write-Utf8 ($base + '.argv.json') ((@{
        executable = $script:Curl
        argv = $Arguments
        proxy_policy = 'explicit --proxy empty and --noproxy *'
    } | ConvertTo-Json -Depth 5) + "`n")

    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $script:Curl
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $start
    Assert-Condition $process.Start() "Could not start native curl for $Label"
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $exitCode = $process.ExitCode
    $process.Dispose()

    Write-Utf8 ($base + '.stdout.txt') $stdout
    Write-Utf8 ($base + '.stderr.txt') $stderr
    Write-Utf8 ($base + '.exit') ($exitCode.ToString() + "`n")
    return [pscustomobject]@{
        Label = $Label
        ExitCode = $exitCode
        Stdout = $stdout
        Stderr = $stderr
        Argv = $Arguments
    }
}

function Read-CurlMetadata([string]$Stdout) {
    $block = [regex]::Match($Stdout, '(?ms)^LC_META_BEGIN\r?\n(.*?)\r?\nLC_META_END')
    Assert-Condition $block.Success 'curl write-out metadata block was not found'
    $metadata = @{}
    foreach ($line in ($block.Groups[1].Value -split "`r?`n")) {
        $separator = $line.IndexOf('=')
        if ($separator -gt 0) { $metadata[$line.Substring(0, $separator)] = $line.Substring($separator + 1) }
    }
    foreach ($required in @('url_effective', 'http_code', 'redirect_url', 'num_redirects', 'ssl_verify_result', 'remote_ip')) {
        Assert-Condition $metadata.ContainsKey($required) "curl metadata omitted $required"
    }
    return $metadata
}

function Get-WindowsRootThumbprints {
    $thumbprints = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($location in @(
        [System.Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine,
        [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
    )) {
        $store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
            [System.Security.Cryptography.X509Certificates.StoreName]::Root,
            $location
        )
        $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
        try {
            foreach ($certificate in $store.Certificates) { [void]$thumbprints.Add($certificate.Thumbprint) }
        }
        finally { $store.Close() }
    }
    return ,$thumbprints
}

function Get-ChainEvidence([string]$Stdout) {
    $block = [regex]::Match($Stdout, '(?ms)^LC_CERTS_BEGIN\r?\n(.*?)\r?\nLC_CERTS_END')
    Assert-Condition $block.Success 'curl did not return a certificate bundle in %{certs}'
    $matches = [regex]::Matches($block.Groups[1].Value, '-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----')
    Assert-Condition ($matches.Count -ge 2) "Expected peer and issuing certificates, found $($matches.Count)"

    $certificates = [System.Collections.Generic.List[System.Security.Cryptography.X509Certificates.X509Certificate2]]::new()
    try {
        foreach ($match in $matches) {
            $certificates.Add([System.Security.Cryptography.X509Certificates.X509Certificate2]::CreateFromPem($match.Value))
        }
        $chain = [System.Security.Cryptography.X509Certificates.X509Chain]::new()
        try {
            $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
            $chain.ChainPolicy.VerificationFlags = [System.Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
            foreach ($certificate in $certificates | Select-Object -Skip 1) {
                [void]$chain.ChainPolicy.ExtraStore.Add($certificate)
            }
            $chainBuilt = $chain.Build($certificates[0])
            $elements = @(
                foreach ($element in $chain.ChainElements) {
                    [pscustomobject]@{
                        subject = $element.Certificate.Subject
                        issuer = $element.Certificate.Issuer
                        thumbprint_sha1 = $element.Certificate.Thumbprint
                        certificate_sha256 = [Convert]::ToHexString(
                            [System.Security.Cryptography.SHA256]::HashData($element.Certificate.RawData)
                        ).ToLowerInvariant()
                        not_before_utc = $element.Certificate.NotBefore.ToUniversalTime().ToString('o')
                        not_after_utc = $element.Certificate.NotAfter.ToUniversalTime().ToString('o')
                        statuses = @($element.ChainElementStatus | ForEach-Object {
                            $_.Status.ToString() + ':' + $_.StatusInformation.Trim()
                        })
                    }
                }
            )
            $statuses = @($chain.ChainStatus | ForEach-Object {
                $_.Status.ToString() + ':' + $_.StatusInformation.Trim()
            })
            $rootThumbprint = if ($elements.Count -gt 0) { $elements[-1].thumbprint_sha1 } else { '' }
            $rootPresent = (Get-WindowsRootThumbprints).Contains($rootThumbprint)
            Assert-Condition $chainBuilt 'Windows X509Chain.Build did not validate the captured peer chain against the Windows stores'
            Assert-Condition ($statuses.Count -eq 0) "Windows chain policy returned status: $($statuses -join ', ')"
            Assert-Condition $rootPresent "Built chain terminal certificate is absent from CurrentUser/LocalMachine Root: $rootThumbprint"
            return [pscustomobject]@{
                curl_peer_chain_pem_count = $certificates.Count
                curl_peer_certificates = @(
                    foreach ($certificate in $certificates) {
                        [pscustomobject]@{
                            subject = $certificate.Subject
                            issuer = $certificate.Issuer
                            thumbprint_sha1 = $certificate.Thumbprint
                            certificate_sha256 = [Convert]::ToHexString(
                                [System.Security.Cryptography.SHA256]::HashData($certificate.RawData)
                            ).ToLowerInvariant()
                            not_before_utc = $certificate.NotBefore.ToUniversalTime().ToString('o')
                            not_after_utc = $certificate.NotAfter.ToUniversalTime().ToString('o')
                        }
                    }
                )
                windows_chain_build = $chainBuilt
                windows_chain_policy_status = $statuses
                windows_built_chain = $elements
                windows_trust_root_thumbprint_sha1 = $rootThumbprint
                windows_trust_root_in_user_or_machine_root_store = $rootPresent
                windows_chain_builder_revocation_mode = 'NoCheck; supplemental path construction only'
                schannel_curl_revocation_policy = 'curl default Schannel verification; ssl_verify_result must be 0'
            }
        }
        finally { $chain.Dispose() }
    }
    finally { foreach ($certificate in $certificates) { $certificate.Dispose() } }
}

function Assert-SamePackageUrl([string]$BaseUrl, [string]$CandidateUrl) {
    $baseUri = [uri]$BaseUrl
    $candidateUri = [uri]$CandidateUrl
    Assert-Condition ($candidateUri.Scheme -eq 'https') "Redirect is not HTTPS: $CandidateUrl"
    Assert-Condition ($candidateUri.Host -eq 'archive.ubuntu.com') "Redirect host is outside the exact Ubuntu archive host: $CandidateUrl"
    Assert-Condition (($candidateUri.Port -eq 443) -and (-not $candidateUri.UserInfo) -and (-not $candidateUri.Query) -and (-not $candidateUri.Fragment)) "Redirect changes authority/query/fragment: $CandidateUrl"
    Assert-Condition ($candidateUri.AbsolutePath -eq $baseUri.AbsolutePath) "Redirect changes the package path: $CandidateUrl"
}

function Get-HeaderContentLength([string]$Stdout) {
    $matches = [regex]::Matches($Stdout, '(?im)^Content-Length:\s*(\d+)\s*$')
    if ($matches.Count -eq 0) { return $null }
    return [long]$matches[$matches.Count - 1].Groups[1].Value
}

try {
    $script:StartedUtc = [DateTimeOffset]::UtcNow
    $freeBefore = (Get-PSDrive -Name C).Free
    Assert-Condition ($freeBefore -ge 5GB) "C: free space is below the 5 GiB reserve ($freeBefore bytes)"
    Assert-Condition ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) 'This capture must run natively on Windows'
    Assert-Condition (Test-Path -LiteralPath $script:Curl -PathType Leaf) "Native Windows curl not found at $script:Curl"
    Assert-Condition ((Get-Item -LiteralPath $script:Curl).Length -gt 0) 'Native Windows curl has an empty file size'
    $curlVersionOutput = (& $script:Curl -V 2>&1 | Out-String).Trim()
    Assert-Condition ($LASTEXITCODE -eq 0) 'curl -V failed'
    Assert-Condition ($curlVersionOutput -match '(?m)\bSchannel\b') 'Native curl is not built with Schannel'
    Assert-Condition ($curlVersionOutput -match '(?m)\bWindows\b') 'curl identifies itself as non-Windows'

    $sourceManifestPath = Join-Path $script:SourceBundle 'manifest.json'
    $sourceChecksumsPath = Join-Path $script:SourceBundle 'SHA256SUMS'
    Assert-Condition (Test-Path -LiteralPath $sourceManifestPath -PathType Leaf) 'Source package manifest is missing'
    Assert-Condition (Test-Path -LiteralPath $sourceChecksumsPath -PathType Leaf) 'Source package checksum manifest is missing'
    $sourceManifest = Get-Content -Raw -LiteralPath $sourceManifestPath | ConvertFrom-Json -AsHashtable
    $sourceManifestSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceManifestPath).Hash.ToLowerInvariant()
    $sourceManifestChecksumLine = Select-String -LiteralPath $sourceChecksumsPath -Pattern '^[0-9a-fA-F]{64}\s+manifest\.json$' | Select-Object -First 1
    Assert-Condition ($null -ne $sourceManifestChecksumLine) 'Source SHA256SUMS has no manifest.json entry'
    $sourceManifestListedHash = ($sourceManifestChecksumLine.Line -split '\s+')[0].ToLowerInvariant()
    Assert-Condition ($sourceManifestSha256 -eq $sourceManifestListedHash) 'Source manifest does not match its existing SHA256SUMS entry'
    $packages = @($sourceManifest.packages)
    Assert-Condition ($packages.Count -eq 3) "Expected exactly three source packages, found $($packages.Count)"
    Assert-Condition ((@($packages.name | Sort-Object -Unique) -join ',') -eq 'gpg,gpgv,ubuntu-keyring') 'Unexpected package set in source manifest'

    $packageEntries = [System.Collections.Generic.List[object]]::new()
    foreach ($package in $packages) {
        Assert-Condition ($package.filename -match '^pool/[^/]+/.+\.deb$') "Unsafe or unexpected filename: $($package.filename)"
        Assert-Condition ($package.download_url -match '^http://archive\.ubuntu\.com/ubuntu/(.+)$') "Unexpected source URL: $($package.download_url)"
        $relativeUrlPath = $Matches[1]
        Assert-Condition ($relativeUrlPath -eq $package.filename) "Manifest URL/path mismatch for $($package.name)"
        Assert-Condition ($package.sha256 -match '^[0-9a-f]{64}$') "Malformed package SHA-256 for $($package.name)"
        Assert-Condition ([long]$package.size -gt 0) "Malformed expected size for $($package.name)"
        $sourcePath = Join-Path (Join-Path $script:SourceBundle 'packages') ([System.IO.Path]::GetFileName($package.filename))
        Assert-Condition (Test-Path -LiteralPath $sourcePath -PathType Leaf) "Existing package archive missing: $sourcePath"
        $sourceFile = Get-Item -LiteralPath $sourcePath
        $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePath).Hash.ToLowerInvariant()
        Assert-Condition ($sourceFile.Length -eq [long]$package.size) "Existing package size differs from the signed-index row: $($package.name)"
        Assert-Condition ($sourceHash -eq $package.sha256) "Existing package SHA differs from the signed-index row: $($package.name)"
        $packageEntries.Add([pscustomobject]@{
            Name = [string]$package.name
            Filename = [string]$package.filename
            RelativeUrlPath = $relativeUrlPath
            Version = [string]$package.version
            Architecture = [string]$package.architecture
            ExpectedSize = [long]$package.size
            ExpectedSha256 = [string]$package.sha256
            ExistingArchiveSize = [long]$sourceFile.Length
            ExistingArchiveSha256 = $sourceHash
            SourceUrlHttp = [string]$package.download_url
            SourceUrlHttps = 'https://archive.ubuntu.com/ubuntu/' + $relativeUrlPath
            InRelease = [string]$package.signed_inrelease
            PackagesIndex = [string]$package.index
        })
    }

    $existingFiles = @(Get-ChildItem -LiteralPath $script:Target -Force -File)
    $allowedBeforeRun = @('README.md', 'capture_windows_tls.ps1', 'capture-invocation.txt', 'capture-run.stdout.txt', 'capture-run.stderr.txt')
    $unexpectedFiles = @($existingFiles | Where-Object { $_.Name -notin $allowedBeforeRun })
    Assert-Condition ($unexpectedFiles.Count -eq 0) "New evidence directory contains unexpected files: $($unexpectedFiles.Name -join ', ')"
    Assert-Condition (-not (Test-Path -LiteralPath (Join-Path $script:Target 'downloads'))) 'Refusing to reuse an existing downloads directory'
    $downloads = Join-Path $script:Target 'downloads'
    New-Item -ItemType Directory -Path $downloads | Out-Null
    $logs = Join-Path $script:Target 'logs'
    Assert-Condition (-not (Test-Path -LiteralPath $logs)) 'Refusing to reuse an existing logs directory'
    New-Item -ItemType Directory -Path $logs | Out-Null

    $proxyVariableNames = @(
        Get-ChildItem Env: | Where-Object { $_.Name -match '(?i)(^|_)https?_proxy$|^all_proxy$|^no_proxy$' } | Select-Object -ExpandProperty Name
    )
    $hostContext = [ordered]@{
        captured_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        operating_system = [Environment]::OSVersion.VersionString
        powershell_version = $PSVersionTable.PSVersion.ToString()
        dotnet_runtime = [System.Runtime.InteropServices.RuntimeInformation]::FrameworkDescription
        curl_executable = $script:Curl
        curl_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $script:Curl).Hash.ToLowerInvariant()
        curl_file_size = (Get-Item -LiteralPath $script:Curl).Length
        curl_version_output = $curlVersionOutput
        curl_backend = 'Windows libcurl with Schannel'
        proxy_environment_variable_names_present = $proxyVariableNames
        proxy_values_recorded = $false
        request_proxy_policy = 'Every curl request passed --proxy "" and --noproxy *; direct transport required'
        windows_root_store_validation = 'curl Schannel default peer verification; require ssl_verify_result=0; independently record a supplemental X509Chain build against Windows CurrentUser/LocalMachine Root stores'
        source_manifest_sha256 = $sourceManifestSha256
        source_manifest_listed_sha256 = $sourceManifestListedHash
        measured_c_free_bytes_before = $freeBefore
    }
    Write-Utf8 (Join-Path $script:Target 'host-context.json') (($hostContext | ConvertTo-Json -Depth 8) + "`n")

    $writeOut = "`nLC_META_BEGIN`nurl_effective=%{url_effective}`nhttp_code=%{http_code}`nredirect_url=%{redirect_url}`nnum_redirects=%{num_redirects}`nssl_verify_result=%{ssl_verify_result}`nremote_ip=%{remote_ip}`ncontent_type=%{content_type}`nLC_META_END`nLC_CERTS_BEGIN`n%{certs}`nLC_CERTS_END`n"
    $results = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in $packageEntries) {
        $entryStarted = [DateTimeOffset]::UtcNow
        $currentUrl = $entry.SourceUrlHttps
        $seenUrls = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
        $hops = [System.Collections.Generic.List[object]]::new()
        $finalMeta = $null
        $finalChain = $null
        for ($hop = 1; $hop -le 6; $hop++) {
            Assert-Condition $seenUrls.Add($currentUrl) "Redirect cycle for $($entry.Name): $currentUrl"
            Assert-SamePackageUrl $entry.SourceUrlHttps $currentUrl
            $label = 'logs/' + $entry.Name + '.hop-' + $hop + '.head'
            $headArgs = @(
                '--head', '--proxy', '', '--noproxy', '*', '--max-time', '30',
                '--silent', '--show-error', '--verbose', '--write-out', $writeOut,
                '--url', $currentUrl
            )
            $head = Invoke-CurlLogged $label $headArgs
            Assert-Condition ($head.ExitCode -eq 0) "Schannel HEAD failed for $($entry.Name), hop $hop (exit $($head.ExitCode))"
            $meta = Read-CurlMetadata $head.Stdout
            Assert-Condition ([int]$meta.ssl_verify_result -eq 0) "Schannel peer verification failed for $($entry.Name), hop ${hop}: $($meta.ssl_verify_result)"
            Assert-Condition ([int]$meta.num_redirects -eq 0) 'Redirect following must remain disabled for each captured hop'
            Assert-SamePackageUrl $entry.SourceUrlHttps $meta.url_effective
            $chainEvidence = Get-ChainEvidence $head.Stdout
            $headersLength = Get-HeaderContentLength $head.Stdout
            $hopRecord = [ordered]@{
                requested_url = $currentUrl
                response_url = [string]$meta.url_effective
                http_status = [int]$meta.http_code
                redirect_url = [string]$meta.redirect_url
                remote_ip = [string]$meta.remote_ip
                schannel_ssl_verify_result = [int]$meta.ssl_verify_result
                head_content_length_header = $headersLength
                chain = $chainEvidence
                raw_stdout = [IO.Path]::GetFileName($label + '.stdout.txt')
                raw_stderr = [IO.Path]::GetFileName($label + '.stderr.txt')
                argv = [IO.Path]::GetFileName($label + '.argv.json')
                exit = [IO.Path]::GetFileName($label + '.exit')
            }
            $hops.Add($hopRecord)
            if ([int]$meta.http_code -ge 300 -and [int]$meta.http_code -lt 400) {
                Assert-Condition ($hop -lt 6) "Redirect count exceeded the fixed six-hop cap for $($entry.Name)"
                Assert-Condition (-not [string]::IsNullOrWhiteSpace([string]$meta.redirect_url)) "HTTP $($meta.http_code) had no absolute redirect URL"
                Assert-SamePackageUrl $entry.SourceUrlHttps ([string]$meta.redirect_url)
                $currentUrl = [string]$meta.redirect_url
                continue
            }
            Assert-Condition ([int]$meta.http_code -eq 200) "Expected HTTP 200 for $($entry.Name), got $($meta.http_code)"
            Assert-Condition ($headersLength -eq $entry.ExpectedSize) "HEAD Content-Length mismatch for $($entry.Name): expected $($entry.ExpectedSize), got $headersLength"
            $finalMeta = $meta
            $finalChain = $chainEvidence
            break
        }
        Assert-Condition ($null -ne $finalMeta) "No final HTTPS response for $($entry.Name)"

        $downloadPath = Join-Path $downloads ([System.IO.Path]::GetFileName($entry.Filename))
        Assert-Condition (-not (Test-Path -LiteralPath $downloadPath)) "Refusing to overwrite package output: $downloadPath"
        $getLabel = 'logs/' + $entry.Name + '.get'
        $getArgs = @(
            '--proxy', '', '--noproxy', '*', '--max-time', '60',
            '--silent', '--show-error', '--verbose', '--write-out', $writeOut,
            '--output', $downloadPath, '--url', [string]$finalMeta.url_effective
        )
        $get = Invoke-CurlLogged $getLabel $getArgs
        Assert-Condition ($get.ExitCode -eq 0) "Schannel GET failed for $($entry.Name) (exit $($get.ExitCode))"
        $getMeta = Read-CurlMetadata $get.Stdout
        Assert-Condition ([int]$getMeta.ssl_verify_result -eq 0) "Schannel peer verification failed on GET for $($entry.Name)"
        Assert-SamePackageUrl $entry.SourceUrlHttps $getMeta.url_effective
        Assert-Condition ([int]$getMeta.http_code -eq 200) "Expected GET HTTP 200 for $($entry.Name), got $($getMeta.http_code)"
        Assert-Condition ([string]::IsNullOrEmpty([string]$getMeta.redirect_url)) "GET unexpectedly redirected for $($entry.Name); no redirect-following was enabled"
        $getChain = Get-ChainEvidence $get.Stdout
        $downloadFile = Get-Item -LiteralPath $downloadPath
        $downloadHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $downloadPath).Hash.ToLowerInvariant()
        Assert-Condition ($downloadFile.Length -eq $entry.ExpectedSize) "Downloaded size mismatch for $($entry.Name)"
        Assert-Condition ($downloadHash -eq $entry.ExpectedSha256) "Downloaded SHA-256 mismatch for $($entry.Name)"
        $freeNow = (Get-PSDrive -Name C).Free
        Assert-Condition ($freeNow -ge 5GB) "C: free space fell below the 5 GiB reserve after $($entry.Name): $freeNow bytes"

        $results.Add([ordered]@{
            package = $entry.Name
            version = $entry.Version
            architecture = $entry.Architecture
            signed_index = $entry.PackagesIndex
            inrelease = $entry.InRelease
            signed_index_row_source_url_http = $entry.SourceUrlHttp
            request_start_url = $entry.SourceUrlHttps
            redirects = $hops
            final_url = [string]$getMeta.url_effective
            final_get_status = [int]$getMeta.http_code
            final_get_remote_ip = [string]$getMeta.remote_ip
            final_get_schannel_ssl_verify_result = [int]$getMeta.ssl_verify_result
            final_get_chain = $getChain
            expected_size_from_signed_index = $entry.ExpectedSize
            downloaded_size = [long]$downloadFile.Length
            expected_sha256_from_signed_index = $entry.ExpectedSha256
            downloaded_sha256 = $downloadHash
            downloaded_file = 'downloads/' + [System.IO.Path]::GetFileName($entry.Filename)
            downloaded_file_matches_signed_index = $true
            preexisting_local_archive_size = $entry.ExistingArchiveSize
            preexisting_local_archive_sha256 = $entry.ExistingArchiveSha256
            preexisting_local_archive_matches_signed_index = $true
            get_command_argv = [IO.Path]::GetFileName($getLabel + '.argv.json')
            get_stdout = [IO.Path]::GetFileName($getLabel + '.stdout.txt')
            get_stderr = [IO.Path]::GetFileName($getLabel + '.stderr.txt')
            get_exit = [IO.Path]::GetFileName($getLabel + '.exit')
            started_at_utc = $entryStarted.ToString('o')
            finished_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        })
    }

    $freeAfter = (Get-PSDrive -Name C).Free
    Assert-Condition ($freeAfter -ge 5GB) "C: free space is below the 5 GiB reserve after capture ($freeAfter bytes)"
    $downloadTotal = [long]0
    foreach ($packageResult in $results) { $downloadTotal += [long]$packageResult.downloaded_size }
    $summary = [ordered]@{
        status = 'PROPOSAL_REVIEW_REQUIRED'
        captured_at_utc_start = $script:StartedUtc.ToString('o')
        captured_at_utc_finish = [DateTimeOffset]::UtcNow.ToString('o')
        capture_host = $hostContext
        source_bundle = '../ubuntu-verifier-provenance-20261025'
        source_manifest_sha256 = $sourceManifestSha256
        source_manifest_listed_sha256 = $sourceManifestListedHash
        transport = 'Windows-native curl 8.13.0 / Schannel; explicit empty proxy and --noproxy *; redirects handled one hop at a time and constrained to HTTPS archive.ubuntu.com with identical package path'
        tls_validation = 'Schannel default certificate/hostname/revocation validation; require curl ssl_verify_result=0. %{certs} peer chain is retained per request. Supplemental Windows X509Chain.Build must succeed with no status and terminate at a certificate present in CurrentUser/LocalMachine Root.'
        max_allowed_redirect_hops = 6
        c_free_bytes_before = $freeBefore
        c_free_bytes_after = $freeAfter
        package_results = $results
        package_download_total_bytes = $downloadTotal
        packages_installed = $false
        payload_binaries_executed = $false
        packages_extracted = $false
        arch_material_acquired = $false
        trust_status = 'This independently corroborates transport/bytes only; it does not bootstrap or independently authenticate the Ubuntu root keyring or authorize Arch acquisition.'
    }
    Write-Utf8 (Join-Path $script:Target 'capture-summary.json') (($summary | ConvertTo-Json -Depth 16) + "`n")
}
catch {
    $script:RunExit = 1
    $errorText = $_.Exception.ToString()
    try { Write-Utf8 (Join-Path $script:Target 'capture-error.txt') ($errorText + "`n") } catch { }
    Write-Error $errorText
}
finally {
    try {
        $finished = [DateTimeOffset]::UtcNow
        Write-Utf8 (Join-Path $script:Target 'capture-run.exit') ($script:RunExit.ToString() + "`n")
        Write-Utf8 (Join-Path $script:Target 'capture-run.timestamps.txt') (
            'started_utc=' + $(if ($script:StartedUtc) { $script:StartedUtc.ToString('o') } else { 'not-started' }) + "`n" +
            'finished_utc=' + $finished.ToString('o') + "`n" +
            'exit=' + $script:RunExit.ToString() + "`n"
        )
    }
    catch { $script:RunExit = 1 }
}

exit $script:RunExit
