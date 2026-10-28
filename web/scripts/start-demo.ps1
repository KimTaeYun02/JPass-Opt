param(
    [string]$ApiTarget,
    [ValidateRange(1, 65535)][int]$Port = 5173,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
$demoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$demoVite = Join-Path $demoRoot 'node_modules/vite/bin/vite.js'
if ($ApiTarget) {
    $targetUri = $null
    if (-not [Uri]::TryCreate($ApiTarget, [UriKind]::Absolute, [ref]$targetUri) -or
        $targetUri.Scheme -notin @('http', 'https') -or -not $targetUri.Host -or
        $targetUri.UserInfo -or $targetUri.Query -or $targetUri.Fragment -or
        $targetUri.AbsolutePath -ne '/') {
        throw 'ApiTarget must be an HTTP(S) origin, for example http://127.0.0.1:8000.'
    }
}
if ($ValidateOnly) {
    [ordered]@{
        WebRoot = $demoRoot
        ViteBin = $demoVite
        Port = $Port
        ApiTargetOverride = $ApiTarget
        DependenciesInstalled = (Test-Path -LiteralPath $demoVite)
    } | ConvertTo-Json
    return
}
if (-not (Test-Path -LiteralPath $demoVite)) {
    throw "Run npm ci in $demoRoot before starting the frontend."
}
$demoNode = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$previousApiTarget = $env:JPASS_API_TARGET
Push-Location -LiteralPath $demoRoot
try {
    if ($ApiTarget) { $env:JPASS_API_TARGET = $ApiTarget }
    & $demoNode $demoVite --host 127.0.0.1 --port $Port --strictPort
    if ($LASTEXITCODE -ne 0) { throw "Vite exited with code $LASTEXITCODE." }
} finally {
    $env:JPASS_API_TARGET = $previousApiTarget
    Pop-Location
}
