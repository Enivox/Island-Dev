# Music Island 🎵

Une **« Dynamic Island »** pour **Windows**, dédiée à la musique — inspirée de l'encoche
iPhone/Mac et de [DynamicWin](https://github.com/FlorianButz/DynamicWin).

Une petite pastille discrète apparaît **en haut au centre de l'écran** dès qu'une musique
joue (n'importe quelle appli : Spotify, Deezer/YouTube dans le navigateur, etc.). Au
**survol**, elle s'agrandit en un panneau façon Apple avec pochette, titre, artiste,
timeline et contrôles.

## Fonctionnalités

- 🎧 **Marche avec tout** : lit la « session média » globale de Windows
  (`GlobalSystemMediaTransportControlsSessionManager`).
- ✨ **Mini-île + visualiseur** : petite pastille avec des barres qui **réagissent au son
  réel** et prennent les **couleurs de la pochette**.
- 🖱️ **Survol animé** : s'agrandit/rétracte en douceur.
- ⏯️ **Contrôles** : précédent / lecture-pause / suivant.
- ⏱️ **Timeline** : position + temps restant, **glisser pour se déplacer** dans le morceau.
- 🔊 **Volume** à la **molette** de la souris au-dessus de l'île.
- 🎨 **Look Apple** : noir profond, coins concaves en haut (façon Mac), ombre portée,
  boutons pleins arrondis.
- 🫥 **Auto-masquage** : disparaît quand il n'y a **pas de musique** ou quand une appli est
  en **plein écran** (jeu, vidéo YouTube/Netflix…).
- 🔔 **Icône dans la zone de notification** : activer/désactiver l'île, démarrer avec
  Windows, quitter.

## Stack technique

| Besoin | Outil |
| --- | --- |
| Interface (fenêtre sans bordure, transparente, animée) | **PySide6** |
| Infos & contrôles musique | **winrt** (`Windows.Media.Control`) |
| Volume + niveau audio (visualiseur) | **pycaw** |
| Génération du `.exe` | **PyInstaller** |

> ⚠️ Le paquet `winsdk` n'existe pas pour Python 3.14. On utilise son successeur officiel
> **`winrt`** (projet PyWinRT) : même API Windows, seul le préfixe d'import change.

## Installation (pour lancer depuis les sources)

```bash
python -m pip install -r requirements.txt
```

## Lancer

```bash
python etape7_finitions.py
```

L'icône apparaît en bas à droite (parfois dans le **débordement `^`**). **Clic droit**
dessus pour le menu ; **clic gauche** pour activer/désactiver rapidement.

## Générer le `.exe`

L'icône `icon.ico` et le fichier `MusicIsland.spec` sont fournis :

```bash
pyinstaller MusicIsland.spec
```

Le résultat : `dist/MusicIsland.exe` (un seul fichier, ~60 Mo, sans console).

## Étapes du développement

Le projet a été construit par étapes (fichiers `etape1` → `etape7`), du simple script
console jusqu'à l'application finale (`etape7_finitions.py`).

1. Script console : lire la session média.
2. Fenêtre flottante transparente, toujours au-dessus.
3. Extension/rétraction animée au survol.
4. Vraies infos (titre, artiste, pochette) + mise à jour auto.
5. Contrôles lecture/pause, précédent, suivant.
6. Slider de volume + timeline.
7. Finitions (look Apple), auto-masquage, icône de notification, démarrage auto, `.exe`.
