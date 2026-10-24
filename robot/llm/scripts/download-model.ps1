# Descarga modelos GGUF de Qwen3 (oficiales en HuggingFace) a .\models\
#
# Uso (Windows, desde la carpeta llm/):
#   .\scripts\download-model.ps1                 # Qwen3-4B Q4_K_M (~2,5 GB)
#   .\scripts\download-model.ps1 0.6B Q8_0       # Draft p/ decodificación especulativa
#                                                # (0.6B oficial solo existe en Q8_0)
#   $env:HF_ENDPOINT = "https://hf-mirror.com"; .\scripts\download-model.ps1   # Espejo (China)
param(
    [string]$Size = "4B",
    [string]$Quant = "Q4_K_M"
)
$ErrorActionPreference = "Stop"

$endpoint = if ($env:HF_ENDPOINT) { $env:HF_ENDPOINT } else { "https://huggingface.co" }
$root = Split-Path $PSScriptRoot -Parent
$dir = Join-Path $root "models"
New-Item -ItemType Directory -Force $dir | Out-Null

$file = "Qwen3-$Size-$Quant.gguf"
$url = "$($endpoint.TrimEnd('/'))/Qwen/Qwen3-$Size-GGUF/resolve/main/$file"
$dest = Join-Path $dir $file

if (Test-Path $dest) {
    # Compara con el tamaño remoto: si está completo, salta; si no, reanuda (curl -C -)
    $local = (Get-Item $dest).Length
    $remote = [int64]0
    $head = (curl.exe -sIL $url) -join "`n"
    $m = [regex]::Matches($head, '(?i)content-length:\s*(\d+)')
    if ($m.Count -gt 0) { $remote = [int64]$m[$m.Count - 1].Groups[1].Value }
    if ($remote -gt 0 -and $local -ge $remote) {
        Write-Host "Ya existe y está completo: $dest"
        exit 0
    }
    Write-Host "Archivo incompleto ($local de $remote bytes), reanudando descarga..."
}

Write-Host "Descargando $file"
Write-Host "  URL:  $url"
Write-Host "  Dest: $dest"
curl.exe -L --fail --retry 3 --retry-delay 2 -C - --progress-bar -o $dest $url
if ($LASTEXITCODE -ne 0) { throw "curl falló con código $LASTEXITCODE" }

Get-Item $dest | Select-Object Name, @{n = "SizeMB"; e = { [math]::Round($_.Length / 1MB, 1) }}
Write-Host "OK. Ajusta MODEL_PATH=/models/$file en tu .env si no es el modelo por defecto."
Write-Host "Nota: el modelo de embeddings (potion-multilingual-128M, ~500 MB) se descarga"
Write-Host "solo la primera vez que arranca el contenedor, hacia .\data\fastembed."
