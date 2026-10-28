# Windows bridge. Prefer pdf2md.cmd; direct .ps1 execution follows the caller's policy.
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$cliArgs = New-Object 'System.Collections.Generic.List[string]'
$distro = $null
$python = '-'
$setupRequest = $false
$parsingOptions = $true
function Stop-Bridge([string]$message, [int]$code = 1) {
    if ($setupRequest) {
        @{status='NEEDS_ACTION'; missing=@($message); next_step='See docs/CONFIGURATION.md: Windows And WSL2'; documents_sent_to_network=$false} |
            ConvertTo-Json -Compress | Write-Output
        exit 4
    }
    [Console]::Error.WriteLine($message)
    exit $code
}
try {
    for ($i = 0; $i -lt $args.Count; $i++) {
        $value = [string]$args[$i]
        if ($value -match '^--wsl-(distro|python)(=(.*))?$') {
            $kind = $Matches[1]
            if ($Matches[2]) { $setting = $Matches[3] }
            else {
                $i++
                if ($i -ge $args.Count) { throw "Missing value for $value" }
                $setting = [string]$args[$i]
            }
            if (-not $setting) { throw "Empty value for $value" }
            if ($kind -eq 'distro') { $distro = $setting } else { $python = $setting }
        } else { $cliArgs.Add($value) }
    }
    if ($cliArgs.Count -eq 0) { $cliArgs.Add('--help') }
    $parsingOptions = $false
    $setupRequest = $cliArgs[0] -eq 'setup'
    if (-not (Get-Command wsl.exe -CommandType Application -ErrorAction SilentlyContinue)) {
        Stop-Bridge 'WSL2 is required. Install WSL2 and a Linux distribution, then follow docs/CONFIGURATION.md. Windows Python is not used.'
    }
    $wslArgs = @()
    if ($distro) { $wslArgs += @('--distribution', $distro) }
    $bridge = Join-Path $PSScriptRoot 'wsl_entry.py'
    if ($bridge.StartsWith('\\') -or $PWD.Path.StartsWith('\\')) { throw 'UNC paths are not supported' }
    $bridgePath = & wsl.exe @wslArgs --exec wslpath -a -u $bridge
    if ($LASTEXITCODE -ne 0) {
        Stop-Bridge 'Cannot start the selected WSL distribution. Check wsl.exe --list --verbose and docs/CONFIGURATION.md; use --wsl-distro NAME to select one.' $LASTEXITCODE
    }
    if ($setupRequest) {
        $pythonProbe = & wsl.exe @wslArgs --exec python3 -B -c 'import sys' 2>&1
        if ($LASTEXITCODE -ne 0) {
            Stop-Bridge 'Python 3 must be available inside the selected WSL distribution to start setup. Prepare Python 3.12 with venv support; see docs/CONFIGURATION.md. Windows Python is not used.'
        }
    }
    $forwarded = @{}
    foreach ($name in @('PDF2MD_CONFIG', 'PDF2MD_MODELS', 'PDF2MD_BWRAP',
        'PDF2MD_PARSER_PYTHON', 'PDF2MD_CPU_PYTHON', 'PDF2MD_WSL_PYTHON', 'CUDA_VISIBLE_DEVICES')) {
        $setting = [Environment]::GetEnvironmentVariable($name)
        if ($null -ne $setting) { $forwarded[$name] = $setting }
    }
    $encoded = [Convert]::ToBase64String($utf8.GetBytes(($forwarded | ConvertTo-Json -Compress)))
    if ($cliArgs.Contains('--help')) {
        Write-Host 'Windows options: --wsl-distro NAME; --wsl-python /path/to/bin/python'
    }
    & wsl.exe @wslArgs --exec python3 -B $bridgePath.Trim() $PWD.Path $python $encoded @cliArgs
    exit $LASTEXITCODE
} catch {
    if ($parsingOptions) {
        [Console]::Error.WriteLine($_.Exception.Message)
        exit 2
    }
    Stop-Bridge $_.Exception.Message
}
