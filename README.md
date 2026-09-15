# Music Island 🎵

Une **« Dynamic Island »** pour **Windows**, dédiée à la musique — inspirée de l'encoche
iPhone/Mac et de [DynamicWin](https://github.com/FlorianButz/DynamicWin).

Une petite pastille discrète apparaît **en haut au centre de l'écran** dès qu'une musique
joue (n'importe quelle appli : Spotify, Deezer/YouTube dans le navigateur, etc.). Au
**survol**, elle s'agrandit en un panneau façon Apple.

## Aperçu

**Repliée** (mini-île avec visualiseur) :

![Île repliée](images/apercu_ferme.png)

**Déployée** (au survol) :

![Île déployée](images/apercu_ouvert.png)

## Fonctionnalités

- 🎧 **Marche avec tout** : lit la « session média » globale de Windows
  (`GlobalSystemMediaTransportControlsSessionManager`).
- ✨ **Mini-île + visualiseur** : petite pastille avec des barres qui **réagissent au son
  réel** et prennent les **couleurs de la pochette**.
- 🖱️ **Survol animé** : s'agrandit / rétracte en douceur.
- ⏯️ **Contrôles** : précédent / lecture-pause / suivant.
- ⏱️ **Timeline** : position + temps restant, **glisser pour se déplacer** dans le morceau.
- 🔊 **Volume** à la **molette** de la souris au-dessus de l'île.
- 🎨 **Look Apple** : noir profond, coins concaves en haut (façon Mac), ombre portée,
  boutons pleins arrondis.
- 🫥 **Auto-masquage** : disparaît quand il n'y a **pas de musique** ou qu'une appli est en
  **plein écran** (jeu, vidéo YouTube/Netflix…).
- 🔔 **Icône dans la zone de notification** : activer/désactiver l'île, démarrer avec
  Windows, quitter.

## Utilisation

### Le plus simple : l'exécutable

Télécharge **`MusicIsland.exe`** et double-clique dessus. Une icône apparaît en bas à
droite (parfois dans le **débordement `^`**) : **clic droit** pour le menu, **clic gauche**
pour activer/désactiver.

### Depuis les sources

```bash
python -m pip install -r requirements.txt
python music_island.py
```

## Générer l'exécutable soi-même

L'icône `icon.ico` et le fichier `MusicIsland.spec` sont fournis :

```bash
pyinstaller MusicIsland.spec
```

Résultat : `dist/MusicIsland.exe` (un seul fichier, ~60 Mo, sans console).

## Stack technique

| Besoin | Outil |
| --- | --- |
| Interface (fenêtre sans bordure, transparente, animée) | **PySide6** |
| Infos & contrôles musique | **winrt** (`Windows.Media.Control`) |
| Volume + niveau audio (visualiseur) | **pycaw** |
| Génération du `.exe` | **PyInstaller** |

> ⚠️ Le paquet `winsdk` n'existe pas pour Python 3.14. On utilise son successeur officiel
> **`winrt`** (projet PyWinRT) : même API Windows, seul le préfixe d'import change.
