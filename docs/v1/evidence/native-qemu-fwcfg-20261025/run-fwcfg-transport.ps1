$ErrorActionPreference = 'Stop'

$evidence = [System.IO.Path]::GetFullPath($PSScriptRoot)
$expectedEvidence = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) 'docs/v1/evidence/native-qemu-fwcfg-20261025'))
if (-not [string]::Equals($evidence, $expectedEvidence, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Evidence script is outside its fixed directory: $evidence"
}

$iso = 'C:\Users\Alex\Downloads\LibreCore-VMSetup-20261015\archlinux-2026.10.01-x86_64.iso'
$qemu = 'C:\Users\Alex\msys64\ucrt64\bin\qemu-system-x86_64.exe'
$extractRoot = 'C:\Users\Alex\AppData\Local\Temp\librecore-fwcfg-whpx-20261025-152000'
$kernel = Join-Path $extractRoot 'arch\boot\x86_64\vmlinuz-linux'
$initrd = Join-Path $extractRoot 'arch\boot\x86_64\initramfs-linux.img'
$expectedIso = '684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5'
$expectedKernel = '8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901'
$expectedInitrd = '35b4362207fcabc66535444aea0b97f3508128b32e97e66aa5f56487007bfe52'
$expectedQemu = 'a812b50865ffcc7ddd43f9c33e9bb1ed7b3501c1f847ee99ede8d4b4a4a93742'
$fixture = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
$fwCfgName = 'opt/org.librecore/worker-image-sha256'
$timeoutSeconds = 240
$bootArgs = 'archisobasedir=arch archisosearchuuid=2026-10-01-14-49-03-00 console=ttyS0,115200n8'

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-FreeLoopbackPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()
    return $port
}

function Connect-LocalPort([int]$Port, [System.Diagnostics.Process]$Process, [int]$WaitSeconds = 30) {
    $until = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
    while ([DateTime]::UtcNow -lt $until) {
        if ($Process.HasExited) { return $null }
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $client.Connect([System.Net.IPAddress]::Loopback, $Port)
            return $client
        } catch {
            $client.Dispose()
            Start-Sleep -Milliseconds 200
        }
    }
    return $null
}

function Read-Available(
    [System.Net.Sockets.TcpClient]$Client,
    [System.Text.StringBuilder]$Text,
    [System.IO.MemoryStream]$Raw
) {
    if ($null -eq $Client -or -not $Client.Connected) { return }
    $stream = $Client.GetStream()
    $buffer = New-Object byte[] 8192
    while ($stream.DataAvailable) {
        $count = $stream.Read($buffer, 0, $buffer.Length)
        if ($count -le 0) { break }
        $Raw.Write($buffer, 0, $count)
        [void]$Text.Append([System.Text.Encoding]::UTF8.GetString($buffer, 0, $count))
    }
}

function Send-Serial([System.Net.Sockets.TcpClient]$Client, [string]$Text) {
    $bytes = [System.Text.Encoding]::ASCII.GetBytes($Text)
    $Client.GetStream().Write($bytes, 0, $bytes.Length)
    $Client.GetStream().Flush()
}

function Send-Monitor([System.Net.Sockets.TcpClient]$Client, [string]$Text) {
    if ($null -eq $Client -or -not $Client.Connected) { return }
    $bytes = [System.Text.Encoding]::ASCII.GetBytes($Text + "`n")
    $Client.GetStream().Write($bytes, 0, $bytes.Length)
    $Client.GetStream().Flush()
}

if ($fixture -notmatch '^[0-9a-f]{64}$') { throw 'The fixed fw_cfg fixture is malformed.' }
if ((Get-PSDrive C).Free -lt 5GB) { throw 'C: free space fell below the 5 GiB preflight threshold.' }
foreach ($inputPath in @($iso, $qemu, $kernel, $initrd)) {
    if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) {
        throw "Required diagnostic input is missing: $inputPath"
    }
}
$extractedFiles = @(Get-ChildItem -LiteralPath $extractRoot -File -Recurse)
if ($extractedFiles.Count -ne 2 -or
    -not ($extractedFiles.FullName -contains $kernel) -or
    -not ($extractedFiles.FullName -contains $initrd)) {
    throw 'The exact temporary extraction does not contain only the two expected files.'
}
$isoBefore = Get-Sha256 $iso
$kernelHash = Get-Sha256 $kernel
$initrdHash = Get-Sha256 $initrd
$qemuBefore = Get-Sha256 $qemu
if ($isoBefore -ne $expectedIso -or $kernelHash -ne $expectedKernel -or
    $initrdHash -ne $expectedInitrd -or $qemuBefore -ne $expectedQemu) {
    throw 'Refusing to launch because an input hash differs from the recorded pin.'
}
$qemuVersion = (& $qemu --version 2>&1 | Out-String).Trim()
if ($qemuVersion -notmatch 'QEMU emulator version 11\.1\.2') { throw "Unexpected QEMU version: $qemuVersion" }
$accelInfo = (& $qemu -accel help 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $accelInfo -notmatch '(?im)^whpx$') {
    throw "QEMU does not advertise WHPX: $accelInfo"
}

$monitorPort = Get-FreeLoopbackPort
$serialPort = Get-FreeLoopbackPort
$serialRawPath = Join-Path $evidence 'serial.raw.bin'
$serialTextPath = Join-Path $evidence 'serial.log'
$monitorRawPath = Join-Path $evidence 'monitor.raw.bin'
$monitorTextPath = Join-Path $evidence 'monitor.log'
$stdoutPath = Join-Path $evidence 'qemu.stdout.log'
$stderrPath = Join-Path $evidence 'qemu.stderr.log'
$exitPath = Join-Path $evidence 'qemu.exit'
$argvPath = Join-Path $evidence 'qemu.argv.json'
$runRecordPath = Join-Path $evidence 'run-record.txt'
$resultPath = Join-Path $evidence 'result.json'
$fixtureCommand = 'p=/sys/firmware/qemu_fw_cfg/by_name/opt/org.librecore/worker-image-sha256/raw; e=' + $fixture + '; n=$(wc -c < "$p"); n=$((n+0)); v=$(cat "$p"); if [ "$n" -eq 64 ] && [ "$v" = "$e" ]; then printf "%s%s bytes=%s actual=%s\n" "__LIBRECORE_FWCFG_" "PASS" "$n" "$v"; else printf "%s%s bytes=%s actual=%s\n" "__LIBRECORE_FWCFG_" "FAIL" "$n" "$v"; fi'

$qemuArgs = @(
    '-nodefaults', '-no-user-config', '-no-reboot',
    '-machine', 'pc,accel=whpx', '-cpu', 'qemu64', '-m', '2048', '-smp', '2',
    '-display', 'none', '-vga', 'std', '-nic', 'none',
    '-kernel', $kernel, '-initrd', $initrd, '-append', $bootArgs,
    '-drive', "file=$iso,format=raw,if=none,id=cd0,media=cdrom,readonly=on",
    '-device', 'ide-cd,drive=cd0',
    '-fw_cfg', "name=$fwCfgName,string=$fixture",
    '-serial', "tcp:127.0.0.1:$serialPort,server=on,wait=off",
    '-monitor', "tcp:127.0.0.1:$monitorPort,server=on,wait=off"
)
$argumentLine = ($qemuArgs | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + $_.Replace('"', '\"') + '"' } else { $_ }
}) -join ' '
$argvRecord = [ordered]@{ executable = $qemu; arguments = [string[]]$qemuArgs }
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($argvPath, (ConvertTo-Json -InputObject $argvRecord -Depth 5 -Compress) + "`n", $utf8NoBom)

$recordLines = @(
    'Purpose: one bounded native Windows WHPX fw_cfg transport diagnostic using the hash-verified upstream Arch 2026.10.01 live ISO.',
    'Scope: verifies only read-only QEMU fw_cfg delivery to the guest serial shell; this is not a worker, image, product build, boot acceptance, or hardware pass.',
    "ISO: $iso",
    "ISO SHA-256 before: $isoBefore",
    "QEMU: $qemu",
    "QEMU SHA-256 before: $qemuBefore",
    "QEMU version: $qemuVersion",
    "QEMU accelerator availability: $($accelInfo -replace "`r?`n", '; ')",
    "Temporary extraction: $extractRoot (kernel and initramfs only; verified hashes recorded below)",
    "Kernel SHA-256: $kernelHash",
    "Initramfs SHA-256: $initrdHash",
    "Guest fw_cfg name: $fwCfgName",
    "Guest fw_cfg test fixture: $fixture",
    "Timeout seconds: $timeoutSeconds",
    'Isolation: -nic none; read-only ISO CD-ROM only; no writable guest disk, host share, USB, passthrough, or firmware device.',
    "Host sockets: serial 127.0.0.1:$serialPort; HMP 127.0.0.1:$monitorPort.",
    "QEMU argument JSON: $argvPath",
    "QEMU command: `"$qemu`" $argumentLine",
    "Guest read-only check command: $fixtureCommand",
    "Start UTC: $([DateTime]::UtcNow.ToString('o'))"
)
[System.IO.File]::WriteAllText($runRecordPath, ($recordLines -join "`n") + "`n", $utf8NoBom)

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $qemu
$psi.Arguments = $argumentLine
$psi.WorkingDirectory = Split-Path -Parent $qemu
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
$psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $psi
$serialClient = $null
$monitorClient = $null
$serialText = [System.Text.StringBuilder]::new()
$monitorText = [System.Text.StringBuilder]::new()
$serialRaw = [System.IO.MemoryStream]::new()
$monitorRaw = [System.IO.MemoryStream]::new()
$stdoutTask = $null
$stderrTask = $null
$processStarted = $false
$result = 'INCOMPLETE'
$harnessError = ''
$rootLoginSent = $false
$emptyPasswordSent = $false
$serialShellPromptSeen = $false
$guestCheckSent = $false
$guestPassMarkerSeen = $false
$guestFailMarkerSeen = $false
$timedOut = $false
$startUtc = [DateTime]::UtcNow

try {
    if (-not $process.Start()) { throw 'QEMU process failed to start.' }
    $processStarted = $true
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $serialClient = Connect-LocalPort -Port $serialPort -Process $process -WaitSeconds 45
    $monitorClient = Connect-LocalPort -Port $monitorPort -Process $process -WaitSeconds 45
    if ($null -ne $monitorClient) {
        Send-Monitor $monitorClient 'info status'
        Start-Sleep -Milliseconds 300
        Read-Available $monitorClient $monitorText $monitorRaw
    }
    while (-not $process.HasExited -and ([DateTime]::UtcNow - $startUtc).TotalSeconds -lt $timeoutSeconds) {
        Read-Available $serialClient $serialText $serialRaw
        Read-Available $monitorClient $monitorText $monitorRaw
        $plain = [regex]::Replace(
            $serialText.ToString(),
            '\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\)|P[^\x1B]*\x1B\\|[()][0-2A-Z]|[78])',
            ''
        ) -replace "`r", "`n"
        if (-not $rootLoginSent -and $plain -match '(?i)(?:archiso|localhost|[a-z0-9_.-]+)\s+login:\s*$') {
            Send-Serial $serialClient "root`n"
            $rootLoginSent = $true
        } elseif ($rootLoginSent -and -not $emptyPasswordSent -and $plain -match '(?i)password:\s*$') {
            Send-Serial $serialClient "`n"
            $emptyPasswordSent = $true
        }
        if ($plain -match '(?m)(?:^|\n)root@archiso[^\r\n]{0,80}[#$]\s*$') {
            $serialShellPromptSeen = $true
        }
        if ($serialShellPromptSeen -and -not $guestCheckSent -and $null -ne $serialClient -and $null -ne $monitorClient) {
            Send-Serial $serialClient ($fixtureCommand + "`n")
            $guestCheckSent = $true
        }
        $passOutput = "__LIBRECORE_FWCFG_PASS bytes=64 actual=$fixture"
        if ($guestCheckSent -and $plain.Contains($passOutput)) {
            $guestPassMarkerSeen = $true
            $result = 'FW_CFG_TRANSPORT_PASS'
            Send-Monitor $monitorClient 'quit'
            break
        }
        if ($guestCheckSent -and $plain.Contains('__LIBRECORE_FWCFG_FAIL')) {
            $guestFailMarkerSeen = $true
            $result = 'FW_CFG_VALUE_MISMATCH_OR_MALFORMED'
            Send-Monitor $monitorClient 'quit'
            break
        }
        Start-Sleep -Milliseconds 100
    }
    if (-not $process.HasExited -and ([DateTime]::UtcNow - $startUtc).TotalSeconds -ge $timeoutSeconds) {
        $timedOut = $true
        $result = if ($serialShellPromptSeen) { 'TIMEOUT_SHELL_NO_VERIFIED_FW_CFG_MARKER' } else { 'TIMEOUT_NO_VERIFIED_SERIAL_SHELL' }
        Send-Monitor $monitorClient 'quit'
    } elseif ($process.HasExited -and -not $guestPassMarkerSeen -and -not $guestFailMarkerSeen) {
        $result = 'QEMU_EXITED_BEFORE_FW_CFG_MARKER'
    }
} catch {
    $harnessError = $_.Exception.ToString()
    $result = 'HARNESS_ERROR'
} finally {
    if ($null -ne $process -and $processStarted -and -not $process.HasExited) {
        Send-Monitor $monitorClient 'quit'
        if (-not $process.WaitForExit(2000)) {
            $process.Kill()
            [void]$process.WaitForExit(5000)
        }
    }
    if ($serialClient) { $serialClient.Dispose() }
    if ($monitorClient) { $monitorClient.Dispose() }
    if ($processStarted) {
        if (-not $process.HasExited) {
            try { $process.Kill(); [void]$process.WaitForExit(5000) } catch { $harnessError += "`nQEMU termination error: $($_.Exception.Message)" }
        }
        [System.IO.File]::WriteAllText($stdoutPath, $stdoutTask.GetAwaiter().GetResult(), $utf8NoBom)
        [System.IO.File]::WriteAllText($stderrPath, $stderrTask.GetAwaiter().GetResult(), $utf8NoBom)
        [System.IO.File]::WriteAllText($exitPath, ([string]$process.ExitCode) + "`n", [System.Text.Encoding]::ASCII)
    } else {
        [System.IO.File]::WriteAllText($stdoutPath, '', $utf8NoBom)
        [System.IO.File]::WriteAllText($stderrPath, $harnessError, $utf8NoBom)
        [System.IO.File]::WriteAllText($exitPath, "not-started`n", [System.Text.Encoding]::ASCII)
    }
    [System.IO.File]::WriteAllBytes($serialRawPath, $serialRaw.ToArray())
    [System.IO.File]::WriteAllBytes($monitorRawPath, $monitorRaw.ToArray())
    [System.IO.File]::WriteAllText($serialTextPath, $serialText.ToString(), $utf8NoBom)
    [System.IO.File]::WriteAllText($monitorTextPath, $monitorText.ToString(), $utf8NoBom)
    $serialRaw.Dispose()
    $monitorRaw.Dispose()
}

$isoAfter = Get-Sha256 $iso
$kernelAfter = Get-Sha256 $kernel
$initrdAfter = Get-Sha256 $initrd
$qemuAfter = Get-Sha256 $qemu
$qemuExit = if ($processStarted) { [string]$process.ExitCode } else { 'not-started' }
if ($isoAfter -ne $expectedIso -or $isoAfter -ne $isoBefore -or
    $kernelAfter -ne $expectedKernel -or $initrdAfter -ne $expectedInitrd -or
    $qemuAfter -ne $expectedQemu -or $qemuAfter -ne $qemuBefore) {
    $result = 'INVALID_INPUT_HASH_CHANGED'
} elseif ($guestPassMarkerSeen -and $qemuExit -ne '0') {
    $result = 'FW_CFG_MARKER_SEEN_BUT_QEMU_EXIT_NONZERO'
} elseif ($guestPassMarkerSeen) {
    $result = 'FW_CFG_TRANSPORT_PASS'
}

$summary = [ordered]@{
    result = $result
    scope = 'QEMU fw_cfg transport diagnostic only; not a worker/image/product build/boot/hardware pass'
    guest_serial_shell_seen = $serialShellPromptSeen
    guest_check_sent = $guestCheckSent
    guest_pass_marker_seen = $guestPassMarkerSeen
    guest_fail_marker_seen = $guestFailMarkerSeen
    timed_out = $timedOut
    timeout_seconds = $timeoutSeconds
    fixture = $fixture
    iso_sha256_before = $isoBefore
    iso_sha256_after = $isoAfter
    qemu_sha256_before = $qemuBefore
    qemu_sha256_after = $qemuAfter
    kernel_sha256 = $kernelAfter
    initramfs_sha256 = $initrdAfter
    qemu_version = $qemuVersion
    qemu_accelerators = $accelInfo
    qemu_exit = $qemuExit
    start_utc = $startUtc.ToString('o')
    finish_utc = [DateTime]::UtcNow.ToString('o')
    harness_error = $harnessError
}
[System.IO.File]::WriteAllText($resultPath, (ConvertTo-Json -InputObject $summary -Depth 5) + "`n", $utf8NoBom)
$recordLines += @(
    "End UTC: $($summary.finish_utc)",
    "Result: $result",
    "ISO SHA-256 after: $isoAfter",
    "QEMU SHA-256 after: $qemuAfter",
    "Kernel SHA-256 after: $kernelAfter",
    "Initramfs SHA-256 after: $initrdAfter",
    "Guest serial shell observed: $serialShellPromptSeen",
    "Guest fw_cfg check sent: $guestCheckSent",
    "Guest pass marker observed: $guestPassMarkerSeen",
    "Guest fail marker observed: $guestFailMarkerSeen",
    "QEMU exit: $qemuExit",
    "Harness error: $harnessError"
)
[System.IO.File]::WriteAllText($runRecordPath, ($recordLines -join "`n") + "`n", $utf8NoBom)
Write-Output $result
