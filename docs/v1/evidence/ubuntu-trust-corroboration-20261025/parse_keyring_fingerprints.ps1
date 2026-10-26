$ErrorActionPreference = 'Stop'
$script:Target = [System.IO.Path]::GetFullPath($PSScriptRoot)
$script:Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$script:Wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'

function Write-Utf8([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $script:Utf8NoBom)
}

function Write-Utf8([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $script:Utf8NoBom)
}

function Invoke-WslCapture([string]$Label, [string[]]$Arguments) {
    $base = Join-Path $script:Target (Join-Path 'logs' $Label)
    Write-Utf8 ($base + '.argv.json') ((@{
        executable = $script:Wsl
        argv = $Arguments
        operation = 'Read-only key listing; no key import, payload execution, install, or network fetch'
    } | ConvertTo-Json -Depth 6) + "`n")
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $script:Wsl
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $start
    if (-not $process.Start()) { throw "Could not start WSL for $Label" }
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
    if ($exit -ne 0) { throw "$Label returned exit $exit; raw output retained" }
    return [pscustomobject]@{ Label = $Label; Arguments = $Arguments; Stdout = $stdout; Stderr = $stderr; Exit = $exit }
}

function Get-PrimaryFingerprints([string]$ColonListing) {
    $fingerprints = [System.Collections.Generic.List[string]]::new()
    $pendingPrimaryFingerprint = $false
    foreach ($line in ($ColonListing -split "`r?`n")) {
        if ($line.StartsWith('pub:')) {
            $pendingPrimaryFingerprint = $true
            continue
        }
        if ($line.StartsWith('sub:')) {
            $pendingPrimaryFingerprint = $false
            continue
        }
        if ($pendingPrimaryFingerprint -and $line.StartsWith('fpr:')) {
            $fields = $line.Split(':')
            if ($fields.Count -le 9 -or $fields[9] -notmatch '^[A-F0-9]{40}$') {
                throw "Malformed primary fingerprint record: $line"
            }
            $fingerprints.Add($fields[9])
            $pendingPrimaryFingerprint = $false
        }
    }
    if ($fingerprints.Count -eq 0) { throw 'No primary key fingerprints parsed from gpg colon listing' }
    return @($fingerprints | Sort-Object -Unique)
}

$downloadedKeyring = Join-Path $script:Target 'downloads\ubuntu-archive-keyring.gpg'
$packagePayload = Join-Path (Split-Path $script:Target -Parent) 'ubuntu-verifier-provenance-20261025\extracted\ubuntu-keyring\usr\share\keyrings\ubuntu-archive-keyring.gpg'
$imageDoc = Join-Path $script:Target 'downloads\image-verification.html'
$expectedFromDocs = @(
    '790BC7277767219C42C86F933B4FE6ACC0B21F32',
    '843938DF228D22F7B3742BC0D94AA3F0EFE21092',
    'F6ECB3762474EDA9D21B7022871920D1991BC93C'
)
$privateGpgHome = '/tmp/lc-ubuntu-keyring-readonly-20261025'
$downloadedLinux = '/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/ubuntu-trust-corroboration-20261025/downloads/ubuntu-archive-keyring.gpg'
$payloadLinux = '/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/ubuntu-verifier-provenance-20261025/extracted/ubuntu-keyring/usr/share/keyrings/ubuntu-archive-keyring.gpg'
$downloadedArgv = @('-d','Ubuntu','--','/usr/bin/gpg','--homedir',$privateGpgHome,'--batch','--no-options','--no-default-keyring','--keyring',$downloadedLinux,'--with-colons','--fingerprint','--list-keys')
$payloadArgv = @('-d','Ubuntu','--','/usr/bin/gpg','--homedir',$privateGpgHome,'--batch','--no-options','--no-default-keyring','--keyring',$payloadLinux,'--with-colons','--fingerprint','--list-keys')
$version = Invoke-WslCapture 'host-gpg-version' @('-d','Ubuntu','--','/usr/bin/gpg','--version')
$gpgHash = Invoke-WslCapture 'host-gpg-sha256' @('-d','Ubuntu','--','/usr/bin/sha256sum','/usr/bin/gpg')
$downloadListing = Invoke-WslCapture 'downloaded-keyring-listing-corrected' $downloadedArgv
$payloadListing = Invoke-WslCapture 'ubuntu-keyring-deb-payload-listing-corrected' $payloadArgv
$publishedDocText = [System.IO.File]::ReadAllText($imageDoc)
foreach ($fingerprint in $expectedFromDocs) {
    if (-not $publishedDocText.Contains($fingerprint)) { throw "Official image verification document did not contain $fingerprint" }
}
$downloadFingerprints = Get-PrimaryFingerprints $downloadListing.Stdout
$payloadFingerprints = Get-PrimaryFingerprints $payloadListing.Stdout
$common = @($downloadFingerprints | Where-Object { $_ -in $expectedFromDocs })
$unlisted = @($downloadFingerprints | Where-Object { $_ -notin $expectedFromDocs })
foreach ($fingerprint in $expectedFromDocs) {
    if ($fingerprint -notin $downloadFingerprints -or $fingerprint -notin $payloadFingerprints) {
        throw "Official fingerprint $fingerprint missing from downloaded keyring or deb payload listing"
    }
}
$downloadBytes = [System.IO.File]::ReadAllBytes($downloadedKeyring)
$payloadBytes = [System.IO.File]::ReadAllBytes($packagePayload)
$byteIdentical = ($downloadBytes.Length -eq $payloadBytes.Length)
if ($byteIdentical) {
    for ($index = 0; $index -lt $downloadBytes.Length; $index++) {
        if ($downloadBytes[$index] -ne $payloadBytes[$index]) { $byteIdentical = $false; break }
    }
}
$downloadHash = (Get-FileHash -LiteralPath $downloadedKeyring -Algorithm SHA256).Hash.ToLowerInvariant()
$payloadHash = (Get-FileHash -LiteralPath $packagePayload -Algorithm SHA256).Hash.ToLowerInvariant()
if (-not $byteIdentical) { throw 'Downloaded archive keyring and existing ubuntu-keyring .deb payload are not byte-identical' }

$publishedMap = @(
    [pscustomobject]@{ fingerprint = $expectedFromDocs[0]; label = 'Ubuntu Archive Automatic Signing Key (2012)'; documentation = 'downloads/image-verification.html' },
    [pscustomobject]@{ fingerprint = $expectedFromDocs[1]; label = 'Ubuntu CD Image Automatic Signing Key (2012)'; documentation = 'downloads/image-verification.html' },
    [pscustomobject]@{ fingerprint = $expectedFromDocs[2]; label = 'Ubuntu Archive Automatic Signing Key (2018)'; documentation = 'downloads/image-verification.html' }
)
$summary = [ordered]@{
    status = 'PROPOSAL_REVIEW_REQUIRED'
    parser = 'Pre-existing Ubuntu WSL /usr/bin/gpg, --batch --no-options --no-default-keyring; list-only operation, no import'
    private_gnupghome = $privateGpgHome
    parser_identity_host_relative = [ordered]@{
        version_log = 'logs/host-gpg-version.stdout.txt'
        sha256_log = 'logs/host-gpg-sha256.stdout.txt'
        sha256 = (($gpgHash.Stdout -split '\s+')[0]).ToLowerInvariant()
        package_provenance_independently_established = $false
    }
    published_primary_fingerprints = $publishedMap
    downloaded_keyring_primary_fingerprints = $downloadFingerprints
    existing_ubuntu_keyring_deb_payload_primary_fingerprints = $payloadFingerprints
    common_published_fingerprints_present_in_both = $common
    downloaded_keyring_fingerprints_not_listed_in_captured_image_doc = $unlisted
    downloaded_archive_keyring = [ordered]@{
        path = 'downloads/ubuntu-archive-keyring.gpg'
        size_bytes = $downloadBytes.Length
        sha256 = $downloadHash
    }
    existing_ubuntu_keyring_deb_payload = [ordered]@{
        path = '../ubuntu-verifier-provenance-20261025/extracted/ubuntu-keyring/usr/share/keyrings/ubuntu-archive-keyring.gpg'
        size_bytes = $payloadBytes.Length
        sha256 = $payloadHash
    }
    byte_identical = $byteIdentical
    source_deb_archive = [ordered]@{
        path = '../ubuntu-verifier-provenance-20261025/packages/ubuntu-keyring_2023.11.28.1build1_all.deb'
        size_bytes = (Get-Item -LiteralPath (Join-Path (Split-Path $script:Target -Parent) 'ubuntu-verifier-provenance-20261025\packages\ubuntu-keyring_2023.11.28.1build1_all.deb')).Length
        sha256 = (Get-FileHash -LiteralPath (Join-Path (Split-Path $script:Target -Parent) 'ubuntu-verifier-provenance-20261025\packages\ubuntu-keyring_2023.11.28.1build1_all.deb') -Algorithm SHA256).Hash.ToLowerInvariant()
        signature_validation_status = 'Earlier WSL APT signed-index validation is host-relative; installed gpgv and root keyring provisioning are not independently authenticated'
    }
    operations = [ordered]@{
        imported = $false
        package_installed = $false
        downloaded_payload_binary_executed = $false
        resolver_or_closure_run = $false
        arch_material_acquired = $false
        stage_a_authorized = $false
    }
}
$jsonPath = Join-Path $script:Target 'keyring-fingerprint-comparison.json'
Write-Utf8 $jsonPath (($summary | ConvertTo-Json -Depth 10) + "`n")
Write-Output ($summary | ConvertTo-Json -Depth 6)
