$ErrorActionPreference = 'Stop'

$evidence = Join-Path $PSScriptRoot 'attempt-2'
if (Test-Path -LiteralPath $evidence) {
    throw "Refusing to overwrite existing diagnostic artifacts: $evidence"
}
New-Item -ItemType Directory -Path $evidence | Out-Null
$iso = 'C:\Users\Alex\Downloads\LibreCore-VMSetup-20261015\archlinux-2026.10.01-x86_64.iso'
$qemu = 'C:\Users\Alex\msys64\ucrt64\bin\qemu-system-x86_64.exe'
$kernel = Join-Path $env:TEMP 'lc-whpx-archiso-20261025\arch\boot\x86_64\vmlinuz-linux'
$initrd = Join-Path $env:TEMP 'lc-whpx-archiso-20261025\arch\boot\x86_64\initramfs-linux.img'
$expectedIso = '684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5'
$expectedKernel = '8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901'
$expectedInitrd = '35b4362207fcabc66535444aea0b97f3508128b32e97e66aa5f56487007bfe52'
$timeoutSeconds = 600

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

function Read-Available([System.Net.Sockets.TcpClient]$Client, [System.Text.StringBuilder]$Buffer) {
    if ($null -eq $Client -or -not $Client.Connected) { return }
    $stream = $Client.GetStream()
    $bytes = New-Object byte[] 8192
    while ($stream.DataAvailable) {
        $count = $stream.Read($bytes, 0, $bytes.Length)
        if ($count -le 0) { break }
        [void]$Buffer.Append([System.Text.Encoding]::UTF8.GetString($bytes, 0, $count))
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

foreach ($inputPath in @($iso, $qemu, $kernel, $initrd)) {
    if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) {
        throw "Required input is missing: $inputPath"
    }
}

$isoBefore = Get-Sha256 $iso
$kernelHash = Get-Sha256 $kernel
$initrdHash = Get-Sha256 $initrd
if ($isoBefore -ne $expectedIso -or $kernelHash -ne $expectedKernel -or $initrdHash -ne $expectedInitrd) {
    throw 'Refusing diagnostic because an input hash differs from the previously verified value.'
}

$qemuVersion = (& $qemu --version 2>&1 | Out-String).Trim()
if ($qemuVersion -notmatch 'QEMU emulator version 11\.1\.2') {
    throw "Unexpected QEMU binary/version: $qemuVersion"
}

$monitorPort = Get-FreeLoopbackPort
$serialPort = Get-FreeLoopbackPort
$display = Join-Path $evidence 'display.bmp'
$serialLog = Join-Path $evidence 'serial.log'
$monitorLog = Join-Path $evidence 'monitor.log'
$stdoutLog = Join-Path $evidence 'qemu.stdout.log'
$stderrLog = Join-Path $evidence 'qemu.stderr.log'
$runRecord = Join-Path $evidence 'run-record.txt'
$resultFile = Join-Path $evidence 'result.json'
$exitFile = Join-Path $evidence 'qemu.exit'

$qemuArgs = @(
    '-nodefaults',
    '-machine', 'pc,accel=whpx',
    '-cpu', 'qemu64',
    '-m', '2048',
    '-smp', '2',
    '-display', 'none',
    '-vga', 'std',
    '-nic', 'none',
    '-kernel', $kernel,
    '-initrd', $initrd,
    '-append', 'archisobasedir=arch archisosearchuuid=2026-10-01-14-49-03-00 console=ttyS0,115200n8',
    '-drive', "file=$iso,format=raw,if=none,id=cd0,media=cdrom,readonly=on",
    '-device', 'ide-cd,drive=cd0',
    '-serial', "tcp:127.0.0.1:$serialPort,server=on,wait=off",
    '-monitor', "tcp:127.0.0.1:$monitorPort,server=on,wait=off"
)

$argumentLine = ($qemuArgs | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + $_.Replace('"', '\"') + '"' } else { $_ }
}) -join ' '
$recordLines = @(
    'Purpose: bounded native Windows WHPX diagnostic of the hash-verified Arch 2026.10.01 live ISO using its exact kernel, initramfs, and BIOS boot UUID.',
    'This is not the LibreCore product ISO, worker image, firmware build, hardware test, or release acceptance.',
    "QEMU: $qemuVersion",
    "ISO SHA-256 before: $isoBefore",
    "Kernel SHA-256: $kernelHash",
    "Initramfs SHA-256: $initrdHash",
    'Guest network: none (-nic none); no guest disk, USB, writable share, host-device passthrough, or firmware device.',
    'The ISO is an explicit read-only CD-ROM. The only host-bound sockets are loopback HMP monitor and serial for this diagnostic.',
    "Timeout seconds: $timeoutSeconds",
    "Command: `"$qemu`" $argumentLine",
    "Start UTC: $([DateTime]::UtcNow.ToString('o'))"
)
Set-Content -LiteralPath $runRecord -Value $recordLines -Encoding UTF8

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $qemu
$psi.Arguments = $argumentLine
$psi.WorkingDirectory = Split-Path -Parent $qemu
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $psi

$serialClient = $null
$monitorClient = $null
$serialBuffer = [System.Text.StringBuilder]::new()
$monitorBuffer = [System.Text.StringBuilder]::new()
$result = 'INCOMPLETE'
$guestChecksSent = $false
$guestChecksObserved = $false
$serialShellPromptSeen = $false
$rootLoginSent = $false
$emptyPasswordSent = $false
$startUtc = [DateTime]::UtcNow
$timedOut = $false

try {
    if (-not $process.Start()) { throw 'QEMU process failed to start.' }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $serialClient = Connect-LocalPort -Port $serialPort -Process $process -WaitSeconds 45
    $monitorClient = Connect-LocalPort -Port $monitorPort -Process $process -WaitSeconds 45

    if ($null -ne $monitorClient) {
        Send-Monitor $monitorClient 'info status'
        Start-Sleep -Milliseconds 500
        Read-Available $monitorClient $monitorBuffer
        Send-Monitor $monitorClient "screendump $display"
        Start-Sleep -Milliseconds 500
        Read-Available $monitorClient $monitorBuffer
    }

    if ($null -ne $serialClient) {
        while (-not $process.HasExited -and ([DateTime]::UtcNow - $startUtc).TotalSeconds -lt $timeoutSeconds) {
            Read-Available $serialClient $serialBuffer
            Read-Available $monitorClient $monitorBuffer
            $plain = [regex]::Replace(
                $serialBuffer.ToString(),
                '\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\)|P[^\x1B]*\x1B\\|[()][0-2A-Z]|[78])',
                ''
            )
            $plain = $plain -replace "`r", "`n"

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

            if (-not $guestChecksSent -and $serialShellPromptSeen) {
                $checks = "id`ncat /proc/cmdline`nsystemctl is-system-running`nsystemctl --failed --no-pager`nprintf '__LIBRECORE_QEMU_READONLY_CHECKS_DONE__\n'`n"
                Send-Serial $serialClient $checks
                $guestChecksSent = $true
            }

            if ($guestChecksSent -and $plain.Contains('__LIBRECORE_QEMU_READONLY_CHECKS_DONE__')) {
                $guestChecksObserved = $true
                $result = 'LIVE_ISO_SERIAL_SHELL_AND_READONLY_CHECKS_PASS'
                Send-Monitor $monitorClient 'quit'
                break
            }

            Start-Sleep -Milliseconds 100
        }
    }

    if (-not $process.HasExited -and ([DateTime]::UtcNow - $startUtc).TotalSeconds -ge $timeoutSeconds) {
        $timedOut = $true
        Send-Monitor $monitorClient 'quit'
        Start-Sleep -Seconds 2
        if (-not $process.HasExited) { $process.Kill() }
    } elseif (-not $process.HasExited -and $result -eq 'INCOMPLETE') {
        Send-Monitor $monitorClient 'quit'
        Start-Sleep -Seconds 2
        if (-not $process.HasExited) { $process.Kill() }
    }
} catch {
    $recordLines += "Harness exception: $($_.Exception.Message)"
    Set-Content -LiteralPath $runRecord -Value $recordLines -Encoding UTF8
    if ($process -and -not $process.HasExited) { $process.Kill() }
    throw
} finally {
    if ($serialClient) { $serialClient.Dispose() }
    if ($monitorClient) { $monitorClient.Dispose() }
    if ($process -and $process.StartTime) {
        if (-not $process.HasExited) { $process.Kill() }
        $process.WaitForExit()
        Set-Content -LiteralPath $stdoutLog -Value $stdoutTask.GetAwaiter().GetResult() -Encoding UTF8
        Set-Content -LiteralPath $stderrLog -Value $stderrTask.GetAwaiter().GetResult() -Encoding UTF8
        Set-Content -LiteralPath $exitFile -Value ([string]$process.ExitCode) -Encoding ASCII
    }
    Set-Content -LiteralPath $serialLog -Value $serialBuffer.ToString() -Encoding UTF8
    Set-Content -LiteralPath $monitorLog -Value $monitorBuffer.ToString() -Encoding UTF8
}

$isoAfter = Get-Sha256 $iso
if ($isoAfter -ne $isoBefore) {
    $result = 'INVALID_INPUT_ISO_HASH_CHANGED'
}
if ($result -ne 'LIVE_ISO_SERIAL_SHELL_AND_READONLY_CHECKS_PASS' -and
    $null -eq $serialClient -and $null -eq $monitorClient -and
    $stderrTask.GetAwaiter().GetResult() -match '(?i)(WHPX: Failed to initialize|failed to initialize WHPX|WHPX accelerator.{0,30}(unavailable|unsupported)|WHPX is not available)') {
    $result = 'WHPX_ACCELERATOR_UNAVAILABLE_BEFORE_GUEST'
}
if ($timedOut -and $result -eq 'INCOMPLETE') {
    if ($guestChecksSent) {
        $result = 'TIMEOUT_READONLY_OUTPUT_INCOMPLETE'
    } elseif ($serialShellPromptSeen) {
        $result = 'TIMEOUT_SHELL_NO_READONLY_COMMANDS'
    } else {
        $result = 'TIMEOUT_NO_VERIFIED_SERIAL_SHELL'
    }
}

$summary = [ordered]@{
    result = $result
    guest_readonly_checks_sent = $guestChecksSent
    guest_readonly_checks_observed = $guestChecksObserved
    serial_shell_prompt_seen = $serialShellPromptSeen
    timed_out = $timedOut
    timeout_seconds = $timeoutSeconds
    iso_sha256_before = $isoBefore
    iso_sha256_after = $isoAfter
    kernel_sha256 = $kernelHash
    initramfs_sha256 = $initrdHash
    qemu_version = $qemuVersion
    qemu_exit = if (Test-Path -LiteralPath $exitFile) { (Get-Content -LiteralPath $exitFile -Raw).Trim() } else { 'not-started' }
    finished_utc = [DateTime]::UtcNow.ToString('o')
}
$summary | ConvertTo-Json | Set-Content -LiteralPath $resultFile -Encoding UTF8
Add-Content -LiteralPath $runRecord -Value @(
    "End UTC: $([DateTime]::UtcNow.ToString('o'))",
    "Result: $result",
    "ISO SHA-256 after: $isoAfter",
    "Guest read-only checks sent: $guestChecksSent",
    "Guest read-only checks observed: $guestChecksObserved",
    "QEMU exit: $($summary.qemu_exit)"
)
Write-Output $result
