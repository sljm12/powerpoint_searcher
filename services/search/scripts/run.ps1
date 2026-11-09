$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
uv run --env-file .env --locked python -m search_service
