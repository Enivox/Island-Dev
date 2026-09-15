# -*- coding: utf-8 -*-
"""
ÉTAPE 1 — Script console minimal
=================================

But : vérifier qu'on arrive bien à lire la "session média" globale de Windows
(le même mécanisme qui alimente le mini-lecteur du menu volume de Windows).

Ce script affiche, en boucle, le titre / l'artiste / l'album / l'état de lecture
du morceau en cours, quelle que soit l'appli qui joue (Spotify, YouTube dans le
navigateur, Deezer, etc.).

Techno : winrt (successeur de winsdk) — module Windows.Media.Control.

Pour lancer :
    python etape1_console_media.py

Pour quitter : Ctrl + C
"""

import asyncio
import sys

# La console Windows n'est pas toujours en UTF-8 : on la force pour que les
# accents (é, è…) et les symboles (▶ ⏸ ⏹) s'affichent correctement.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass  # si ça échoue, on continue quand même

# --- API Windows pour la session média globale ---
# NOTE : on importe depuis "winrt" (et pas "winsdk"), car winsdk n'existe pas
# pour Python 3.14. C'est exactement la même API Windows, juste renommée.
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as GestionnaireMedia,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as EtatLecture,
)


# On convertit l'énumération "état de lecture" de Windows en texte lisible (FR).
ETAT_EN_FRANCAIS = {
    EtatLecture.CLOSED: "Fermé",
    EtatLecture.OPENED: "Ouvert",
    EtatLecture.CHANGING: "Changement…",
    EtatLecture.STOPPED: "⏹ Arrêté",
    EtatLecture.PLAYING: "▶ En lecture",
    EtatLecture.PAUSED: "⏸ En pause",
}


async def lire_infos_media():
    """
    Récupère les infos du morceau en cours.

    Retourne un dictionnaire {titre, artiste, album, etat} si une session média
    existe, ou None s'il n'y a rien en cours (aucune appli ne joue de musique).
    """
    # 1) On demande à Windows le "gestionnaire" de sessions média.
    #    request_async() est une opération asynchrone -> on attend son résultat.
    gestionnaire = await GestionnaireMedia.request_async()

    # 2) On récupère la session actuellement "au premier plan" pour le média.
    #    Peut être None si rien ne joue.
    session = gestionnaire.get_current_session()
    if session is None:
        return None

    # 3) Les propriétés du média (titre, artiste, album…) sont aussi asynchrones.
    proprietes = await session.try_get_media_properties_async()

    # 4) L'état de lecture (en cours / en pause…) est synchrone.
    infos_lecture = session.get_playback_info()
    etat = infos_lecture.playback_status  # une valeur de l'énumération EtatLecture

    return {
        "titre": proprietes.title or "(titre inconnu)",
        "artiste": proprietes.artist or "(artiste inconnu)",
        "album": proprietes.album_title or "",
        "etat": ETAT_EN_FRANCAIS.get(etat, str(etat)),
    }


def afficher(infos):
    """Affiche joliment les infos dans la console."""
    print("-" * 50)
    if infos is None:
        print("Aucune musique en cours (aucune session média détectée).")
    else:
        print(f"  Titre   : {infos['titre']}")
        print(f"  Artiste : {infos['artiste']}")
        if infos["album"]:
            print(f"  Album   : {infos['album']}")
        print(f"  État    : {infos['etat']}")
    print("-" * 50)


async def main():
    """
    Boucle principale : on relit les infos toutes les secondes et on n'affiche
    que lorsque quelque chose a changé (pour ne pas inonder la console).
    """
    print("=== ÉTAPE 1 : lecture de la session média Windows ===")
    print("Lance une musique quelque part (Spotify, YouTube…), puis regarde ici.")
    print("Change de morceau ou mets en pause pour voir la mise à jour.")
    print("Ctrl + C pour quitter.\n")

    derniere_signature = object()  # valeur bidon pour forcer le 1er affichage

    while True:
        try:
            infos = await lire_infos_media()
        except Exception as erreur:
            # On attrape toute erreur pour que la boucle ne s'arrête pas.
            print(f"[Erreur lors de la lecture] {erreur}")
            infos = None

        # "Signature" = résumé de l'état actuel, pour détecter un changement.
        signature = None if infos is None else (
            infos["titre"], infos["artiste"], infos["etat"]
        )

        if signature != derniere_signature:
            afficher(infos)
            derniere_signature = signature

        # Petite pause avant la prochaine vérification (~1 seconde).
        await asyncio.sleep(1.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nArrêt demandé. À bientôt !")
