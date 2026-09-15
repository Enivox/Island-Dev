# Désinstalle Music Island (raccourci + fichiers + démarrage auto).
$ErrorActionPreference = 'SilentlyContinue'

$nom     = 'Music Island'
$dossier = Join-Path $env:LOCALAPPDATA $nom
$menu    = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$lnk     = Join-Path $menu "$nom.lnk"

# 1) Ferme l'appli si elle tourne
Get-Process -Name MusicIsland -ErrorAction SilentlyContinue | Stop-Process -Force

# 2) Retire le raccourci du menu Démarrer
if (Test-Path -LiteralPath $lnk) { Remove-Item -LiteralPath $lnk -Force }

# 3) Retire les fichiers
if (Test-Path -LiteralPath $dossier) { Remove-Item -LiteralPath $dossier -Recurse -Force }

# 4) Retire l'entrée de démarrage automatique (si elle existait)
try {
    Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'MusicIsland' -ErrorAction Stop
} catch {}

Write-Host "Music Island a ete desinstalle." -ForegroundColor Green
