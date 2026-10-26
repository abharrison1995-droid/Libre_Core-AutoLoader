$ErrorActionPreference = 'Stop'
$script:Target = [System.IO.Path]::GetFullPath($PSScriptRoot)
$script:Curl = Join-Path $env:SystemRoot 'System32\curl.exe'
$script:StartedUtc = [DateTimeOffset]::UtcNow
$script:Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$script:AllowedHosts = @('documentation.ubuntu.com', 'archive.ubuntu.com')
$script:RunExit = 0

function Write-Utf8([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $script:Utf8NoBom)
}

function Assert-Condition([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Get-ChainSummary([string]$Output) {
    $block = [regex]::Match($Output, '(?ms)^LC_CERTS_BEGIN\r?\n(.*?)\r?\nLC_CERTS_END')
    Assert-Condition $block.Success 'curl %{certs} output was not retained'
    $pemMatches = [regex]::Matches($block.Groups[1].Value, '-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----')
    Assert-Condition ($pemMatches.Count -ge 2) "Expected peer and issuer certificates; got $($pemMatches.Count)"
    $certificates = [System.Collections.Generic.List[System.Security.Cryptography.X509Certificates.X509Certificate2]]::new()
    try {
        foreach ($match in $pemMatches) {
            $certificates.Add([System.Security.Cryptography.X509Certificates.X509Certificate2]::CreateFromPem($match.Value))
        }
        $chain = [System.Security.Cryptography.X509Certificates.X509Chain]::new()
        try {
            $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
            $chain.ChainPolicy.VerificationFlags = [System.Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
            foreach ($certificate in $certificates | Select-Object -Skip 1) {
                [void]$chain.ChainPolicy.ExtraStore.Add($certificate)
            }
            $built = $chain.Build($certificates[0])
            $elements = @(
                foreach ($element in $chain.ChainElements) {
                    $certificate = $element.Certificate
                    [pscustomobject]@{
                        subject = $certificate.Subject
                        issuer = $certificate.Issuer
                        thumbprint_sha1 = $certificate.Thumbprint
                        certificate_sha256 = [Convert]::ToHexString(
                            [System.Security.Cryptography.SHA256]::HashData($certificate.RawData)
                        ).ToLowerInvariant()
                        not_before_utc = $certificate.NotBefore.ToUniversalTime().ToString('o')
                        not_after_utc = $certificate.NotAfter.ToUniversalTime().ToString('o')
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
            $rootStoreHasCertificate = $false
            foreach ($location in @(
                [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser,
                [System.Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine
            )) {
                $store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
                    [System.Security.Cryptography.X509Certificates.StoreName]::Root,
                    $location
                )
                $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
                try {
                    foreach ($root in $store.Certificates) {
                        if ($root.Thumbprint -eq $rootThumbprint) { $rootStoreHasCertificate = $true }
                    }
                }
                finally { $store.Close() }
            }
            return [pscustomobject]@{
                peer_chain_pem_count = $certificates.Count
                peer_certificates = @(
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
                windows_x509_chain_build = $built
                windows_x509_chain_status = $statuses
                windows_built_chain = $elements
                windows_root_thumbprint_sha1 = $rootThumbprint
                windows_built_root_present_in_current_user_or_local_machine_root = $rootStoreHasCertificate
                supplemental_chain_revocation_mode = 'NoCheck; this supplemental chain build is not revocation evidence'
            }
        }
        finally { $chain.Dispose() }
    }
    finally {
        foreach ($certificate in $certificates) { $certificate.Dispose() }
    }
}

function Parse-CurlMetadata([string]$Output) {
    $block = [regex]::Match($Output, '(?ms)^LC_META_BEGIN\r?\n(.*?)\r?\nLC_META_END')
    Assert-Condition $block.Success 'curl write-out metadata block missing'
    $metadata = @{}
    foreach ($line in ($block.Groups[1].Value -split "`r?`n")) {
        $index = $line.IndexOf('=')
        if ($index -gt 0) { $metadata[$line.Substring(0, $index)] = $line.Substring($index + 1) }
    }
    foreach ($name in @('url_effective', 'http_code', 'num_redirects', 'ssl_verify_result', 'remote_ip')) {
        Assert-Condition $metadata.ContainsKey($name) "curl metadata missing $name"
    }
    return $metadata
}

function Invoke-CurlCapture([string]$Label, [string]$Url, [string]$OutputRelativePath) {
    $freeBefore = (Get-PSDrive -Name C).Free
    Assert-Condition ($freeBefore -ge 5GB) "C: reserve below 5 GiB before $Label ($freeBefore bytes)"
    $uri = [Uri]$Url
    Assert-Condition ($uri.Scheme -eq 'https' -and $uri.Host -in $script:AllowedHosts) "URL not in fixed HTTPS allowlist: $Url"
    $outputPath = Join-Path $script:Target $OutputRelativePath
    $writeOut = "LC_META_BEGIN`nurl_effective=%{url_effective}`nhttp_code=%{http_code}`nnum_redirects=%{num_redirects}`nssl_verify_result=%{ssl_verify_result}`nremote_ip=%{remote_ip}`nLC_META_END`nLC_CERTS_BEGIN`n%{certs}`nLC_CERTS_END`n"
    $arguments = @(
        '--proxy', '', '--noproxy', '*', '--proto', '=https', '--proto-redir', '=https',
        '--location', '--max-redirs', '5', '--fail', '--silent', '--show-error',
        '--output', $outputPath, '--write-out', $writeOut, $Url
    )
    $base = Join-Path $script:Target (Join-Path 'logs' $Label)
    Write-Utf8 ($base + '.argv.json') ((@{
        executable = $script:Curl
        argv = $arguments
        request_url = $Url
        output_file = $OutputRelativePath
        allowlisted_hosts = $script:AllowedHosts
        proxy_policy = 'direct; explicit --proxy empty and --noproxy *'
    } | ConvertTo-Json -Depth 6) + "`n")
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $script:Curl
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $arguments) { [void]$start.ArgumentList.Add($argument) }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $start
    Assert-Condition $process.Start() "Could not start native curl for $Label"
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $exit = $process.ExitCode
    $process.Dispose()
    Write-Utf8 ($base + '.stdout.txt') $stdout
    Write-Utf8 ($base + '.stderr.txt') $stderr
    Write-Utf8 ($base + '.exit') ($exit.ToString() + "`n")
    Assert-Condition ($exit -eq 0) "curl failed for $Label with exit $exit"

    $metadata = Parse-CurlMetadata $stdout
    $effective = [Uri]$metadata.url_effective
    Assert-Condition ([int]$metadata.http_code -eq 200) "$Label HTTP status was $($metadata.http_code)"
    Assert-Condition ([int]$metadata.ssl_verify_result -eq 0) "$Label Schannel verify result was $($metadata.ssl_verify_result)"
    Assert-Condition ($effective.Scheme -eq 'https' -and $effective.Host -in $script:AllowedHosts) "$Label final URL outside allowlist: $($effective.AbsoluteUri)"
    Assert-Condition (Test-Path -LiteralPath $outputPath -PathType Leaf) "$Label output not created"
    $file = Get-Item -LiteralPath $outputPath
    Assert-Condition ($file.Length -gt 0) "$Label output is empty"
    $chain = Get-ChainSummary $stdout
    Assert-Condition $chain.windows_x509_chain_build "$Label supplemental Windows X509Chain did not build"
    Assert-Condition ($chain.windows_x509_chain_status.Count -eq 0) "$Label supplemental Windows chain reports status: $($chain.windows_x509_chain_status -join ', ')"
    Assert-Condition $chain.windows_built_root_present_in_current_user_or_local_machine_root "$Label built root absent from Windows Root store"
    $freeAfter = (Get-PSDrive -Name C).Free
    Assert-Condition ($freeAfter -ge 5GB) "C: reserve below 5 GiB after $Label ($freeAfter bytes)"
    return [ordered]@{
        label = $Label
        request_url = $Url
        final_url = $effective.AbsoluteUri
        http_status = [int]$metadata.http_code
        redirect_count = [int]$metadata.num_redirects
        remote_ip = $metadata.remote_ip
        schannel_ssl_verify_result = [int]$metadata.ssl_verify_result
        tls_cert_chain = $chain
        file = $OutputRelativePath
        size_bytes = [long]$file.Length
        sha256 = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
        curl_argv = "logs/$Label.argv.json"
        stdout = "logs/$Label.stdout.txt"
        stderr = "logs/$Label.stderr.txt"
        exit_file = "logs/$Label.exit"
        c_free_before_bytes = $freeBefore
        c_free_after_bytes = $freeAfter
    }
}

try {
    Assert-Condition (Test-Path -LiteralPath $script:Curl -PathType Leaf) 'Windows System32 curl.exe not found'
    $freeBefore = (Get-PSDrive -Name C).Free
    Assert-Condition ($freeBefore -ge 5GB) "C: reserve below 5 GiB ($freeBefore bytes)"
    $curlVersion = (& $script:Curl -V 2>&1 | Out-String)
    $curlVersionExit = $LASTEXITCODE
    Write-Utf8 (Join-Path $script:Target 'curl-version.txt') $curlVersion
    Write-Utf8 (Join-Path $script:Target 'curl-version.exit') ($curlVersionExit.ToString() + "`n")
    Assert-Condition ($curlVersionExit -eq 0 -and $curlVersion -match 'Schannel') 'System curl is not a working Schannel build'
    $curlSignature = Get-AuthenticodeSignature -LiteralPath $script:Curl
    Assert-Condition ($curlSignature.Status -eq 'Valid') "curl Authenticode status is $($curlSignature.Status)"
    Assert-Condition ($curlSignature.SignerCertificate.Subject -match 'Microsoft Corporation') 'curl Authenticode signer is not Microsoft'
    $curlContext = [ordered]@{
        executable = $script:Curl
        sha256 = (Get-FileHash -LiteralPath $script:Curl -Algorithm SHA256).Hash.ToLowerInvariant()
        file_size = (Get-Item -LiteralPath $script:Curl).Length
        version_output = $curlVersion.TrimEnd()
        authenticode_status = $curlSignature.Status.ToString()
        signer_subject = $curlSignature.SignerCertificate.Subject
        signer_issuer = $curlSignature.SignerCertificate.Issuer
        signer_thumbprint_sha1 = $curlSignature.SignerCertificate.Thumbprint
        timestamp_signer_subject = if ($curlSignature.TimeStamperCertificate) { $curlSignature.TimeStamperCertificate.Subject } else { $null }
        timestamp_signer_thumbprint_sha1 = if ($curlSignature.TimeStamperCertificate) { $curlSignature.TimeStamperCertificate.Thumbprint } else { $null }
    }
    Write-Utf8 (Join-Path $script:Target 'curl-identity.json') (($curlContext | ConvertTo-Json -Depth 8) + "`n")
    Write-Utf8 (Join-Path $script:Target 'capture-invocation.txt') (
        '& "' + (Join-Path $PSHOME 'pwsh.exe') + '" -NoLogo -NoProfile -File "' +
        (Join-Path $script:Target 'capture_ubuntu_sources.ps1') + '"' + "`n"
    )
    Write-Utf8 (Join-Path $script:Target 'capture-start.txt') (
        'started_at_utc=' + $script:StartedUtc.ToString('o') + "`n" +
        'c_free_bytes_before=' + $freeBefore.ToString() + "`n" +
        'windows_os=' + [System.Runtime.InteropServices.RuntimeInformation]::OSDescription + "`n" +
        'powershell=' + $PSVersionTable.PSVersion.ToString() + "`n" +
        'dotnet=' + [System.Runtime.InteropServices.RuntimeInformation]::FrameworkDescription + "`n"
    )

    $captures = @(
        (Invoke-CurlCapture 'ubuntu-image-verification-doc' 'https://documentation.ubuntu.com/security/software-integrity/image-verification/' 'downloads/image-verification.html'),
        (Invoke-CurlCapture 'ubuntu-archive-verification-doc' 'https://documentation.ubuntu.com/security/software-integrity/archive-verification/' 'downloads/archive-verification.html'),
        (Invoke-CurlCapture 'ubuntu-archive-keyring' 'https://archive.ubuntu.com/ubuntu/project/ubuntu-archive-keyring.gpg' 'downloads/ubuntu-archive-keyring.gpg')
    )
    $summary = [ordered]@{
        status = 'PROPOSAL_REVIEW_REQUIRED'
        captured_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        curl_identity = 'curl-identity.json'
        transport_policy = 'Windows System32 curl 8.13.0 Schannel; --proxy empty, --noproxy *, HTTPS-only redirects, official Ubuntu hosts allowlisted in capture script'
        windows_tls = 'curl ssl_verify_result=0 required; Schannel default validation used without --ssl-no-revoke. Supplemental X509Chain used RevocationMode=NoCheck; it is not revocation evidence.'
        windows_root_boundary = 'Windows trust is relative to the active OS certificate store and provisioning; this capture does not independently authenticate Windows roots.'
        ubuntu_domain_boundary = 'Official-domain identity is based on HTTPS via Windows Schannel and the Windows certificate store; no independent Ubuntu signature was used to authenticate the downloaded documentation or keyring.'
        requests = $captures
        c_free_bytes_before = $freeBefore
        c_free_bytes_after = (Get-PSDrive -Name C).Free
        payload_execution = $false
        key_import = $false
        package_installation = $false
        arch_material_acquired = $false
        stage_a_authorized = $false
    }
    Write-Utf8 (Join-Path $script:Target 'capture-summary.json') (($summary | ConvertTo-Json -Depth 20) + "`n")
}
catch {
    $script:RunExit = 1
    try { Write-Utf8 (Join-Path $script:Target 'capture-error.txt') ($_.Exception.ToString() + "`n") } catch { }
    Write-Error $_.Exception.ToString()
}
finally {
    $freeAfter = (Get-PSDrive -Name C).Free
    Write-Utf8 (Join-Path $script:Target 'capture-finish.txt') (
        'finished_at_utc=' + [DateTimeOffset]::UtcNow.ToString('o') + "`n" +
        'c_free_bytes_after=' + $freeAfter.ToString() + "`n" +
        'exit=' + $script:RunExit.ToString() + "`n"
    )
}
exit $script:RunExit
