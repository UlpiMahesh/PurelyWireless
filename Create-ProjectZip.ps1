
# Create a clean ZIP of the PurelyWireless desktop project

$ProjectRoot = (Get-Location).Path
$ProjectName = Split-Path $ProjectRoot -Leaf
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

# Save ZIP beside the project, not inside it
$ZipPath = Join-Path (Split-Path $ProjectRoot -Parent) "${ProjectName}_Review_${Timestamp}.zip"

# Directories to exclude
$ExcludedDirectories = @(
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "coverage",
    "htmlcov",
    "BrowserData",
    "Database",
    "Backups",
    "Temp",
    "Logs",
    "storage",
    "Storage",
    "Files",
    "Input",
    "Output",
    "outputs",
    "reports"
)

# Sensitive or unnecessary files to exclude
$ExcludedFiles = @(
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    "marketlogins.xlsx",
    "credentials.xlsx",
    "secrets.json",
    "token.json",
    "purelywireless.db"
)

$ExcludedExtensions = @(
    ".pyc",
    ".pyo",
    ".log",
    ".sqlite",
    ".sqlite3",
    ".db"
)

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

if (-not (Test-Path $ProjectRoot)) {
    throw "Project folder not found."
}

if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

$Archive = [System.IO.Compression.ZipFile]::Open(
    $ZipPath,
    [System.IO.Compression.ZipArchiveMode]::Create
)

$Included = 0
$Skipped = 0

try {
    Get-ChildItem -LiteralPath $ProjectRoot -File -Recurse -Force |
        ForEach-Object {

            $File = $_
            $RelativePath = $File.FullName.Substring(
                $ProjectRoot.Length
            ).TrimStart('\', '/')

            $Parts = $RelativePath -split '[\\/]'
            $FileName = $File.Name
            $Extension = $File.Extension.ToLower()

            # Skip excluded directories anywhere in the path
            $SkipDirectory = $false

            foreach ($Part in $Parts) {
                if ($ExcludedDirectories -contains $Part) {
                    $SkipDirectory = $true
                    break
                }
            }

            # Skip credentials, secrets and runtime files
            $SkipFile = (
                $ExcludedFiles -contains $FileName -or
                $ExcludedExtensions -contains $Extension -or
                $FileName -match '^(id_rsa|id_ed25519)(\.|$)' -or
                $FileName -match '\.(pem|key|p12|pfx)$' -or
                $FileName -match '^(credentials|secrets|token)([-_.].*)?\.(json|txt|yaml|yml)$'
            )

            if ($SkipDirectory -or $SkipFile) {
                $Skipped++
                return
            }

            # Add file using its relative project path
            $EntryName = $RelativePath.Replace('\', '/')

            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $Archive,
                $File.FullName,
                $EntryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null

            $Included++
        }
}
finally {
    $Archive.Dispose()
}

Write-Host ""
Write-Host "Project ZIP created successfully." -ForegroundColor Green
Write-Host "Included files : $Included"
Write-Host "Skipped files  : $Skipped"
Write-Host "ZIP location   : $ZipPath"
