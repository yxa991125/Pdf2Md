# Windows PowerShell 5.1+ entry point; no Windows Python required.
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$cliArgs = New-Object 'System.Collections.Generic.List[string]'
$distro = $null
$python = '-'
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
    $wslArgs = @()
    if ($distro) { $wslArgs += @('--distribution', $distro) }
    $bridge = Join-Path $PSScriptRoot 'wsl_entry.py'
    if ($bridge.StartsWith('\\') -or $PWD.Path.StartsWith('\\')) { throw 'UNC paths are not supported' }
    $bridgePath = & wsl.exe @wslArgs --exec wslpath -a -u $bridge
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $forwarded = @{}
    foreach ($name in @('PDF2MD_CONFIG', 'PDF2MD_MODELS', 'PDF2MD_BWRAP',
        'PDF2MD_PARSER_PYTHON', 'PDF2MD_WSL_PYTHON', 'CUDA_VISIBLE_DEVICES')) {
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
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
