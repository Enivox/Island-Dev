# ============================================================
#  Installateur simple de Music Island (pas besoin d'être admin)
#  - copie l'exe dans %LOCALAPPDATA%\Music Island
#  - crée un raccourci dans le menu Démarrer  -> trouvable dans la recherche
# ============================================================
$ErrorActionPreference = 'Stop'

$nom      = 'Music Island'
$dossier  = Join-Path $env:LOCALAPPDATA $nom
$exeSrc   = Join-Path $PSScriptRoot 'MusicIsland.exe'
$exeDst   = Join-Path $dossier 'MusicIsland.exe'

if (-not (Test-Path -LiteralPath $exeSrc)) {
    Write-Host "ERREUR : MusicIsland.exe est introuvable a cote de ce script." -ForegroundColor Red
    Write-Host "Place installer.ps1 (ou installer.bat) dans le meme dossier que MusicIsland.exe."
    return
}

# 1) Copie de l'exe dans un emplacement stable
New-Item -ItemType Directory -Force -Path $dossier | Out-Null
Copy-Item -LiteralPath $exeSrc -Destination $exeDst -Force
Write-Host "Copie de l'application dans : $dossier"

# 2) Raccourci dans le menu Demarrer (=> apparait dans la recherche Windows)
$menu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$lnk  = Join-Path $menu "$nom.lnk"
$ws = New-Object -ComObject WScript.Shell
$raccourci = $ws.CreateShortcut($lnk)
$raccourci.TargetPath       = $exeDst
$raccourci.WorkingDirectory = $dossier
$raccourci.IconLocation     = $exeDst
$raccourci.Description       = 'Music Island - Dynamic Island musique'
$raccourci.Save()
Write-Host "Raccourci cree dans le menu Demarrer."

Write-Host ""
Write-Host "Installation terminee ! Cherche 'Music Island' dans le menu Demarrer." -ForegroundColor Green
Write-Host "(Pour desinstaller : lance desinstaller.ps1)"
