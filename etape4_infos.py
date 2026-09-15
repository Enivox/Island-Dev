# -*- coding: utf-8 -*-
"""
ÉTAPE 4 — Vraies infos musique (titre, artiste, pochette) + mise à jour auto
============================================================================

Nouveautés par rapport à l'étape 3 :
  - Un THREAD de fond (classe LecteurMedia) lit en continu la session média de
    Windows (via winrt) et envoie les infos à l'interface par un SIGNAL Qt.
    -> l'interface ne se bloque jamais, l'animation reste fluide.
  - Le panneau étendu affiche : la POCHETTE (à gauche), le TITRE, l'ARTISTE et
    l'état (lecture / pause).
  - Mise à jour AUTOMATIQUE quand la musique change (nouvelle piste, pause…).
  - Cas "aucune musique" géré (texte neutre).

Les CONTRÔLES (play/pause, précédent/suivant) et le VOLUME viendront aux
étapes 5 et 6.

Pour lancer :
    python etape4_infos.py

Pour quitter : Ctrl + C dans la console (ou la touche Échap).
"""

import sys
import signal
import asyncio

from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QHBoxLayout, QVBoxLayout, QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, QTimer, QRect, QRectF, QThread, Signal,
    QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import (
    QPainter, QColor, QGuiApplication, QPainterPath, QCursor, QPixmap, QFont,
)

# --- API Windows (winrt) ---
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as GestionnaireMedia,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as EtatLecture,
)
from winrt.windows.storage.streams import DataReader


# ====================================================================== #
#  RÉGLAGES
# ====================================================================== #

# État REPLIÉ (petit trait discret)
LARGEUR_REPLIE = 150
HAUTEUR_REPLIE = 8

# État ÉTENDU (grand panneau)
LARGEUR_ETENDU = 380
HAUTEUR_ETENDU = 110

# Zone de déclenchement (là où amener la souris pour ouvrir)
TRIGGER_LARGEUR = 350
TRIGGER_HAUTEUR = 8
MARGE_SURVOL = 8  # marge pour "rester ouvert" sans clignoter

# Apparence
RAYON_MAX = 22
COULEUR_FOND = QColor(12, 12, 12, 245)
COTE_POCHETTE = 72          # taille (px) de la pochette carrée
RAYON_POCHETTE = 14         # arrondi de la pochette

# Animations
DUREE_ANIM = 280            # animation d'ouverture/fermeture (ms)
DUREE_FONDU = 160           # apparition du contenu (ms)


# ====================================================================== #
#  THREAD DE LECTURE DE LA MUSIQUE
# ====================================================================== #
class LecteurMedia(QThread):
    """
    Tourne dans un thread séparé : lit la session média de Windows en boucle et
    émet le signal `infos` (un dictionnaire, ou None) à CHAQUE changement.

    Le dictionnaire contient :
        titre, artiste, album (str)
        etat  : 'playing' | 'paused' | 'other'
        pochette : bytes (image) ou None
    """

    infos = Signal(object)  # object = on transporte un dict (ou None)

    def __init__(self):
        super().__init__()
        self._actif = True
        self._signature = object()      # état précédent (pour détecter un changement)
        self._piste_cache = None        # (titre, artiste) de la pochette en cache
        self._pochette_cache = None     # derniers octets de pochette lus

    def arreter(self):
        """Demande l'arrêt propre du thread."""
        self._actif = False

    def run(self):
        """Point d'entrée du thread : lance la boucle asyncio."""
        try:
            asyncio.run(self._boucle())
        except Exception as erreur:
            print(f"[LecteurMedia] arrêt : {erreur}")

    async def _boucle(self):
        # On récupère le gestionnaire une seule fois, puis on réinterroge la
        # session courante à chaque tour (pour capter les changements d'appli).
        gestionnaire = await GestionnaireMedia.request_async()

        while self._actif:
            try:
                infos = await self._lire(gestionnaire)
            except Exception:
                infos = None

            # "Signature" = résumé de l'état, pour n'émettre que si ça a changé.
            signature = None if infos is None else (
                infos["titre"], infos["artiste"], infos["etat"]
            )
            if signature != self._signature:
                self._signature = signature
                self.infos.emit(infos)

            # Pause d'environ 1 s, découpée pour réagir vite à un arrêt demandé.
            for _ in range(10):
                if not self._actif:
                    break
                await asyncio.sleep(0.1)

    async def _lire(self, gestionnaire):
        """Lit les infos de la session courante (ou None si rien ne joue)."""
        session = gestionnaire.get_current_session()
        if session is None:
            return None

        proprietes = await session.try_get_media_properties_async()
        etat_brut = session.get_playback_info().playback_status

        titre = proprietes.title or ""
        artiste = proprietes.artist or ""

        # La pochette est coûteuse à lire : on ne la relit QUE si la piste change.
        piste = (titre, artiste)
        if piste != self._piste_cache:
            self._pochette_cache = await self._lire_pochette(proprietes.thumbnail)
            self._piste_cache = piste

        if etat_brut == EtatLecture.PLAYING:
            etat = "playing"
        elif etat_brut == EtatLecture.PAUSED:
            etat = "paused"
        else:
            etat = "other"

        return {
            "titre": titre or "(titre inconnu)",
            "artiste": artiste,
            "album": proprietes.album_title or "",
            "etat": etat,
            "pochette": self._pochette_cache,
        }

    @staticmethod
    async def _lire_pochette(reference_vignette):
        """
        Convertit la vignette (un flux Windows) en octets d'image (bytes).
        Retourne None s'il n'y a pas de pochette.
        """
        if reference_vignette is None:
            return None
        flux = await reference_vignette.open_read_async()
        taille = flux.size
        if taille == 0:
            return None
        lecteur = DataReader(flux)
        await lecteur.load_async(taille)
        tampon = bytearray(taille)
        lecteur.read_bytes(tampon)
        return bytes(tampon)


# ====================================================================== #
#  LA FENÊTRE (Dynamic Island)
# ====================================================================== #
class Island(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Géométries repliée / étendue, + zone de déclenchement.
        self._rect_replie = self._calculer_rect(LARGEUR_REPLIE, HAUTEUR_REPLIE)
        self._rect_etendu = self._calculer_rect(LARGEUR_ETENDU, HAUTEUR_ETENDU)
        self._zone_declenchement = self._calculer_rect(TRIGGER_LARGEUR, TRIGGER_HAUTEUR)

        self._etendu = False
        self.setGeometry(self._rect_replie)

        # Contenu du panneau (pochette + textes) : construit une fois.
        self._construire_contenu()

        # Image "par défaut" quand il n'y a pas de pochette.
        self._pochette_placeholder = self._faire_placeholder(COTE_POCHETTE, RAYON_POCHETTE)
        self.lbl_pochette.setPixmap(self._pochette_placeholder)

        # --- Animation de la géométrie (ouverture/fermeture) ---
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(DUREE_ANIM)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._quand_anim_finie)

        # --- Fondu d'apparition du contenu ---
        self._opacite = QGraphicsOpacityEffect(self.contenu)
        self._opacite.setOpacity(0.0)
        self.contenu.setGraphicsEffect(self._opacite)
        self.contenu.hide()
        self._anim_fondu = QPropertyAnimation(self._opacite, b"opacity")
        self._anim_fondu.setDuration(DUREE_FONDU)

        # --- Timers ---
        self._timer_souris = QTimer(self)
        self._timer_souris.timeout.connect(self._verifier_souris)
        self._timer_souris.start(50)

        self._timer_premier_plan = QTimer(self)
        self._timer_premier_plan.timeout.connect(self.raise_)
        self._timer_premier_plan.start(500)

    # ------------------------------------------------------------------ #
    #  Construction de l'interface du panneau
    # ------------------------------------------------------------------ #
    def _construire_contenu(self):
        """Crée le conteneur (pochette + titre + artiste + état)."""
        self.contenu = QWidget(self)
        self.contenu.setGeometry(0, 0, LARGEUR_ETENDU, HAUTEUR_ETENDU)

        # Couleurs du texte (la taille est réglée via QFont plus bas).
        self.contenu.setStyleSheet(
            "QLabel#titre   { color: #FFFFFF; }"
            "QLabel#artiste { color: #BEBEBE; }"
            "QLabel#etat    { color: #8A8A8A; }"
        )

        # Pochette (à gauche).
        self.lbl_pochette = QLabel()
        self.lbl_pochette.setFixedSize(COTE_POCHETTE, COTE_POCHETTE)

        # Textes (à droite).
        self.lbl_titre = QLabel("—")
        self.lbl_titre.setObjectName("titre")
        f = QFont(); f.setPointSize(11); f.setBold(True)
        self.lbl_titre.setFont(f)

        self.lbl_artiste = QLabel("")
        self.lbl_artiste.setObjectName("artiste")
        f = QFont(); f.setPointSize(9)
        self.lbl_artiste.setFont(f)

        self.lbl_etat = QLabel("")
        self.lbl_etat.setObjectName("etat")
        f = QFont(); f.setPointSize(8)
        self.lbl_etat.setFont(f)

        # Largeur dispo pour le texte (pour couper proprement les titres longs).
        self._largeur_texte = (
            LARGEUR_ETENDU - 16 - 14 - COTE_POCHETTE - 16
        )
        for lbl in (self.lbl_titre, self.lbl_artiste, self.lbl_etat):
            lbl.setFixedWidth(self._largeur_texte)

        colonne = QVBoxLayout()
        colonne.setContentsMargins(0, 0, 0, 0)
        colonne.setSpacing(2)
        colonne.addStretch(1)
        colonne.addWidget(self.lbl_titre)
        colonne.addWidget(self.lbl_artiste)
        colonne.addWidget(self.lbl_etat)
        colonne.addStretch(1)

        ligne = QHBoxLayout(self.contenu)
        ligne.setContentsMargins(16, 16, 16, 14)
        ligne.setSpacing(14)
        ligne.addWidget(self.lbl_pochette)
        ligne.addLayout(colonne)
        ligne.addStretch(1)

    # ------------------------------------------------------------------ #
    #  Réception des infos musique (appelé dans le thread de l'interface)
    # ------------------------------------------------------------------ #
    def maj_infos(self, infos):
        """Slot connecté au signal du thread : met à jour l'affichage."""
        if infos is None:
            self.lbl_titre.setText("Aucune musique")
            self.lbl_artiste.setText("")
            self.lbl_etat.setText("")
            self.lbl_pochette.setPixmap(self._pochette_placeholder)
            return

        self._texte_elide(self.lbl_titre, infos["titre"])
        self._texte_elide(self.lbl_artiste, infos["artiste"])

        if infos["etat"] == "playing":
            self.lbl_etat.setText("▶ En lecture")
        elif infos["etat"] == "paused":
            self.lbl_etat.setText("⏸ En pause")
        else:
            self.lbl_etat.setText("")

        if infos["pochette"]:
            self.lbl_pochette.setPixmap(
                self._pixmap_arrondie(infos["pochette"], COTE_POCHETTE, RAYON_POCHETTE)
            )
        else:
            self.lbl_pochette.setPixmap(self._pochette_placeholder)

    def _texte_elide(self, label: QLabel, texte: str):
        """Affiche le texte en le coupant avec … s'il est trop long."""
        metriques = label.fontMetrics()
        label.setText(
            metriques.elidedText(texte or "", Qt.ElideRight, self._largeur_texte)
        )

    # ------------------------------------------------------------------ #
    #  Images (pochette)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pixmap_arrondie(data: bytes, cote: int, rayon: int) -> QPixmap:
        """Transforme des octets d'image en pochette carrée aux coins arrondis."""
        source = QPixmap()
        source.loadFromData(data)
        # On agrandit pour COUVRIR le carré, puis on recentre.
        source = source.scaled(
            cote, cote, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )

        resultat = QPixmap(cote, cote)
        resultat.fill(Qt.transparent)

        peintre = QPainter(resultat)
        peintre.setRenderHint(QPainter.Antialiasing)
        peintre.setRenderHint(QPainter.SmoothPixmapTransform)
        chemin = QPainterPath()
        chemin.addRoundedRect(0, 0, cote, cote, rayon, rayon)
        peintre.setClipPath(chemin)
        # Décalage pour centrer l'image dans le carré.
        dx = (cote - source.width()) / 2
        dy = (cote - source.height()) / 2
        peintre.drawPixmap(int(dx), int(dy), source)
        peintre.end()
        return resultat

    @staticmethod
    def _faire_placeholder(cote: int, rayon: int) -> QPixmap:
        """Pochette de remplacement (carré sombre avec une note de musique)."""
        pm = QPixmap(cote, cote)
        pm.fill(Qt.transparent)
        peintre = QPainter(pm)
        peintre.setRenderHint(QPainter.Antialiasing)
        chemin = QPainterPath()
        chemin.addRoundedRect(0, 0, cote, cote, rayon, rayon)
        peintre.fillPath(chemin, QColor(38, 38, 38, 255))
        peintre.setPen(QColor(150, 150, 150))
        f = QFont(); f.setPointSize(int(cote * 0.38))
        peintre.setFont(f)
        peintre.drawText(pm.rect(), Qt.AlignCenter, "♪")
        peintre.end()
        return pm

    # ------------------------------------------------------------------ #
    #  Placement / survol / animation
    # ------------------------------------------------------------------ #
    def _calculer_rect(self, largeur, hauteur) -> QRect:
        geo = QGuiApplication.primaryScreen().geometry()
        x = geo.x() + (geo.width() - largeur) // 2
        y = geo.y()
        return QRect(x, y, largeur, hauteur)

    def _verifier_souris(self):
        souris = QCursor.pos()
        if not self._etendu:
            if self._zone_declenchement.contains(souris):
                self._ouvrir()
        else:
            zone_ouverte = self._rect_etendu.adjusted(
                -MARGE_SURVOL, 0, MARGE_SURVOL, MARGE_SURVOL
            )
            if not zone_ouverte.contains(souris):
                self._fermer()

    def _ouvrir(self):
        self._etendu = True
        self._animer_vers(self._rect_etendu)

    def _fermer(self):
        self._etendu = False
        # On cache le contenu tout de suite, puis on rétracte le panneau.
        self._anim_fondu.stop()
        self._opacite.setOpacity(0.0)
        self.contenu.hide()
        self._animer_vers(self._rect_replie)

    def _animer_vers(self, rect_cible: QRect):
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(rect_cible)
        self._anim.start()

    def _quand_anim_finie(self):
        """Quand l'ouverture est terminée : on fait apparaître le contenu en fondu."""
        if self._etendu:
            self.contenu.show()
            self._anim_fondu.stop()
            self._anim_fondu.setStartValue(0.0)
            self._anim_fondu.setEndValue(1.0)
            self._anim_fondu.start()

    # ------------------------------------------------------------------ #
    #  Dessin du fond
    # ------------------------------------------------------------------ #
    def paintEvent(self, event):
        peintre = QPainter(self)
        peintre.setRenderHint(QPainter.Antialiasing)
        zone = QRectF(0, 0, self.width(), self.height())
        rayon = min(RAYON_MAX, self.height() / 2)
        chemin = self._forme_encoche(zone, rayon)
        peintre.setPen(Qt.NoPen)
        peintre.fillPath(chemin, COULEUR_FOND)

    @staticmethod
    def _forme_encoche(zone: QRectF, rayon: float) -> QPainterPath:
        x, y, w, h = zone.x(), zone.y(), zone.width(), zone.height()
        r = min(rayon, h, w / 2)
        chemin = QPainterPath()
        chemin.moveTo(x, y)
        chemin.lineTo(x + w, y)
        chemin.lineTo(x + w, y + h - r)
        chemin.quadTo(x + w, y + h, x + w - r, y + h)
        chemin.lineTo(x + r, y + h)
        chemin.quadTo(x, y + h, x, y + h - r)
        chemin.closeSubpath()
        return chemin

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            QApplication.quit()


# ====================================================================== #
#  Programme principal
# ====================================================================== #
def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)

    fenetre = Island()
    fenetre.show()

    # Thread de lecture de la musique -> connecté à l'affichage.
    lecteur = LecteurMedia()
    lecteur.infos.connect(fenetre.maj_infos)  # signal (thread) -> slot (interface)
    lecteur.start()

    # Arrêt propre du thread quand on quitte.
    app.aboutToQuit.connect(lecteur.arreter)

    signal.signal(signal.SIGINT, lambda *args: app.quit())
    reveil = QTimer()
    reveil.start(200)
    reveil.timeout.connect(lambda: None)

    print("Dynamic Island (étape 4) lancée.")
    print("👉 Lance une musique, puis survole le haut de l'écran au centre.")
    print("   Le panneau montre pochette + titre + artiste, et se met à jour tout seul.")
    print("Ferme avec Ctrl + C (ici) ou la touche Échap.")

    code = app.exec()
    lecteur.arreter()
    lecteur.wait(1500)
    sys.exit(code)


if __name__ == "__main__":
    main()
