# cleanup_hackathonpro.ps1
# Run this from inside C:\Users\haron\Desktop\HackathonPro (or double-click it there).
# It does NOT permanently delete anything -- it moves hackathon/demo-only files
# into a _to_delete\ subfolder so you can review and empty it yourself when ready.

$root = $PSScriptRoot
if (-not $root) { $root = Get-Location }
$trash = Join-Path $root "_to_delete"
New-Item -ItemType Directory -Force -Path $trash | Out-Null

$items = @(
    "SUBMISSION.md",
    "DEMO_SCRIPT.md",
    "output_surveillance.mp4",
    "test_surveillance.mp4",
    "cctv_logs.db",
    "snapshots",
    "__pycache__",
    ".venv",
    ".codex",
    "docs\FEATURES_CHECKLIST.md",
    "backend\__pycache__"
)

foreach ($item in $items) {
    $src = Join-Path $root $item
    if (Test-Path $src) {
        $destName = ($item -replace '[\\/]', '_')
        $dest = Join-Path $trash $destName
        Write-Host "Moving $item -> _to_delete\$destName"
        Move-Item -Force -Path $src -Destination $dest
    } else {
        Write-Host "Skipping $item (not found)"
    }
}

Write-Host ""
Write-Host "Done. Review _to_delete\, then delete that folder yourself once you're happy."
Write-Host "Recreate your virtual environment afterwards with:"
Write-Host "  python -m venv .venv"
Write-Host "  .venv\Scripts\activate"
Write-Host "  pip install -r requirements.txt"
