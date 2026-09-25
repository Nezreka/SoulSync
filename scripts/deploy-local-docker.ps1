param(
    [string]$Tag = "soulsync-local:latest",
    [string]$Service = "soulsync"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Building $Tag from $root"
docker build --pull -t $Tag .

$override = @"
services:
  $Service:
    image: $Tag
"@

$overridePath = Join-Path $root "docker-compose.local-image.yml"
$override | Set-Content -Encoding UTF8 $overridePath

Write-Host "Redeploying compose service '$Service' with $Tag"
docker compose -f docker-compose.yml -f docker-compose.local-image.yml up -d --no-deps --force-recreate $Service

Write-Host "Waiting for container status..."
docker compose -f docker-compose.yml -f docker-compose.local-image.yml ps

Write-Host "Recent logs:"
docker compose -f docker-compose.yml -f docker-compose.local-image.yml logs --tail 80 $Service
