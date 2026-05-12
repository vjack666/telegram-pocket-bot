param(
    [Parameter(Mandatory = $true)]
    [string]$PackageZip,
    [string]$TargetDir = "$HOME\pocket-option-cloud",
    [string]$PythonExe = "python",
    [switch]$SkipVenv,
    [switch]$RestoreChromeProfile,
    [string]$ChromeUserDataDir = "$env:LOCALAPPDATA\Google\Chrome\User Data",
    [string]$ChromeProfileName = "Default"
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[INSTALL] $Message" -ForegroundColor Cyan
}

$pkgAbs = (Resolve-Path $PackageZip).Path
$targetAbs = [System.IO.Path]::GetFullPath($TargetDir)
$tempExtract = Join-Path ([System.IO.Path]::GetTempPath()) ("pocket_option_install_" + [Guid]::NewGuid().ToString("N"))

Write-Step "Package: $pkgAbs"
Write-Step "Target : $targetAbs"

if (-not (Test-Path -LiteralPath $pkgAbs)) {
    throw "Package not found: $pkgAbs"
}

New-Item -ItemType Directory -Path $tempExtract -Force | Out-Null
New-Item -ItemType Directory -Path $targetAbs -Force | Out-Null

Write-Step "Extracting package..."
Expand-Archive -LiteralPath $pkgAbs -DestinationPath $tempExtract -Force

$projectSrc = Join-Path $tempExtract "project"
if (-not (Test-Path -LiteralPath $projectSrc)) {
    throw "Invalid package format. Missing 'project' folder."
}

Write-Step "Copying project files..."
Copy-Item -Path (Join-Path $projectSrc "*") -Destination $targetAbs -Recurse -Force

$mainPy = Join-Path $targetAbs "main.py"
$requirementsTxt = Join-Path $targetAbs "requirements.txt"
if (-not (Test-Path -LiteralPath $mainPy) -or -not (Test-Path -LiteralPath $requirementsTxt)) {
    throw "Project files were not copied correctly to $targetAbs"
}

# Optional Chrome restore
if ($RestoreChromeProfile) {
    Write-Step "Restoring Chrome profile data..."
    $chromeBackupRoot = Join-Path $tempExtract "external\chrome-user-data"
    if (-not (Test-Path -LiteralPath $chromeBackupRoot)) {
        Write-Warning "No Chrome backup found inside package."
    }
    else {
        $localStateSrc = Join-Path $chromeBackupRoot "Local State"
        $profileSrc = Join-Path $chromeBackupRoot $ChromeProfileName

        if (Test-Path -LiteralPath $localStateSrc) {
            New-Item -ItemType Directory -Path $ChromeUserDataDir -Force | Out-Null
            Copy-Item -LiteralPath $localStateSrc -Destination (Join-Path $ChromeUserDataDir "Local State") -Force
        }
        else {
            Write-Warning "Local State not found in package."
        }

        if (Test-Path -LiteralPath $profileSrc) {
            Copy-Item -LiteralPath $profileSrc -Destination (Join-Path $ChromeUserDataDir $ChromeProfileName) -Recurse -Force
        }
        else {
            Write-Warning "Chrome profile '$ChromeProfileName' not found in package."
        }
    }
}

if (-not $SkipVenv) {
    Write-Step "Creating virtual environment..."
    Push-Location $targetAbs
    try {
        & $PythonExe -m venv .venv
        $venvPython = Join-Path $targetAbs ".venv\Scripts\python.exe"

        Write-Step "Installing Python dependencies..."
        & $venvPython -m pip install --upgrade pip
        & $venvPython -m pip install -r (Join-Path $targetAbs "requirements.txt")

        Write-Step "Installing Playwright Chromium..."
        & $venvPython -m playwright install chromium
    }
    finally {
        Pop-Location
    }
}

Write-Step "Cleaning temp files..."
Remove-Item -LiteralPath $tempExtract -Recurse -Force

Write-Host ""
Write-Host "Installation completed." -ForegroundColor Green
Write-Host "Project path: $targetAbs" -ForegroundColor Green
Write-Host ""
Write-Host "If restoring Chrome profile, close Chrome before first use." -ForegroundColor Yellow
