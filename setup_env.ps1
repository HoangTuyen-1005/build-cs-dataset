# ==============================================================================
# Pipeline Environment Setup Script (Windows PowerShell)
# ==============================================================================

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " [1/2] Installing dependencies from requirements.txt..." -ForegroundColor Yellow
Write-Host "==================================================" -ForegroundColor Cyan
pip install -r requirements.txt

Write-Host "`n==================================================" -ForegroundColor Cyan
Write-Host " [2/2] Pre-downloading FireRedVAD pretrained weights..." -ForegroundColor Yellow
Write-Host "==================================================" -ForegroundColor Cyan
python -c "import os; from huggingface_hub import snapshot_download; snapshot_download(repo_id='FireRedTeam/FireRedVAD', local_dir='pretrained_models/FireRedVAD')"

Write-Host "`n==================================================" -ForegroundColor Green
Write-Host " Environment setup completed successfully!" -ForegroundColor Green
Write-Host " (Note: Make sure FFmpeg is installed and added to PATH)" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
