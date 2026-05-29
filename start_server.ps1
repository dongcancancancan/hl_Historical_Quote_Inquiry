# PowerShell script to start the server
Write-Host "=========================================="
Write-Host "Starting Historical Quote Inquiry System..."
Write-Host "=========================================="

# Check if virtual environment exists
if (-not (Test-Path "venv\Scripts\Activate.ps1")) {
    Write-Host "[Error] Virtual environment not found. Please run 'python -m venv venv' and install requirements first." -ForegroundColor Red
    exit 1
}

# Kill old uvicorn processes
Write-Host "[Info] Cleaning up old processes..."
$pids = (Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
if ($pids) {
    foreach ($pid_ in $pids) {
        Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "[Info] Starting FastAPI server on http://127.0.0.1:8000"
Write-Host "[Info] Press Ctrl+C to stop the server."

# Start server within virtual environment
& .\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
