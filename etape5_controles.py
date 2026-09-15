# -*- coding: utf-8 -*-
"""
ÉTAPE 5 — Les contrôles : précédent / play-pause / suivant
==========================================================

Nouveautés par rapport à l'étape 4 :
  - Trois boutons cliquables dans le panneau : ⏮ précédent, ▶/⏸ lecture-pause,
    ⏭ suivant.
  - Ils pilotent la VRAIE musique via winrt :
        try_skip_previous_async / try_toggle_play_pause_async / try_skip_next_async
  - Le bouton play/pause change d'icône selon l'état, avec un retour visuel
    IMMÉDIAT au clic (l'affichage se resynchronise ensuite avec la réalité).

Astuce technique importante :
  Les contrôles sont ASYNCHRONES et doivent tourner sur la boucle asyncio du
  THREAD de lecture (pas sur l'interface). Quand on clique un bouton (côté
  interface), on envoie donc la commande au thread avec
  `asyncio.run_coroutine_threadsafe(...)`, qui exécute la coroutine côté thread.

Le VOLUME et la TIMELINE arrivent à l'étape 6.

Pour lancer :
    python etape5_controles.py

Pour quitter : Ctrl + C dans la console (ou la touche Échap).
"""

import sys
import signal
import asyncio

from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton,
    QHBoxLayout, QVBoxLayout, QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, QTimer, QRect, QRectF, QThread, Signal,
    QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import (
    QPainter, QColor, QGuiApplication, QPainterPath, QCursor, QPixmap, QFont,
)

from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as GestionnaireMedia,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as EtatLecture,
)
from winrt.windows.storage.streams import DataReader


# ====================================================================== #
#  RÉGLAGES
# ====================================================================== #
LARGEUR_REPLIE = 150
HAUTEUR_REPLIE = 8

LARGEUR_ETENDU = 400
HAUTEUR_ETENDU = 122

TRIGGER_LARGEUR = 350
TRIGGER_HAUTEUR = 8
MARGE_SURVOL = 8

RAYON_MAX = 24
COULEUR_FOND = QColor(12, 12, 12, 245)
COTE_POCHETTE = 76
RAYON_POCHETTE = 14

DUREE_ANIM = 280
DUREE_FONDU = 160

# Icônes de la police "Segoe MDL2 Assets" (présente sur Windows 10/11) :
# des glyphes média monochromes bien nets.
ICONE_PRECEDENT = ""
ICONE_LECTURE = ""    # ▶
ICONE_PAUSE = ""      # ⏸
ICONE_SUIVANT = ""


# ====================================================================== #
#  THREAD DE LECTURE + CONTRÔLE DE LA MUSIQUE
# ====================================================================== #
class LecteurMedia(QThread):
    infos = Signal(object)  # dict (ou None)

    def __init__(self):
        super().__init__()
        self._actif = True
        self._signature = object()
        self._piste_cache = None
        self._pochette_cache = None
        # Références partagées avec l'interface pour envoyer des commandes.
        self._loop = None
        self._gestionnaire = None

    def arreter(self):
        self._actif = False

    def run(self):
        try:
            asyncio.run(self._boucle())
        except Exception as erreur:
            print(f"[LecteurMedia] arrêt : {erreur}")

    async def _boucle(self):
        # On mémorise la boucle asyncio et le gestionnaire pour pouvoir recevoir
        # des commandes depuis l'interface (autre thread).
        self._loop = asyncio.get_running_loop()
        self._gestionnaire = await GestionnaireMedia.request_async()

        while self._actif:
            try:
                infos = await self._lire(self._gestionnaire)
            except Exception:
                infos = None

            signature = None if infos is None else (
                infos["titre"], infos["artiste"], infos["etat"]
            )
            if signature != self._signature:
                self._signature = signature
                self.infos.emit(infos)

            # ~0,5 s entre deux lectures (réactif sans être coûteux).
            for _ in range(5):
                if not self._actif:
                    break
                await asyncio.sleep(0.1)

    async def _lire(self, gestionnaire):
        session = gestionnaire.get_current_session()
        if session is None:
            return None

        proprietes = await session.try_get_media_properties_async()
        etat_brut = session.get_playback_info().playback_status

        titre = proprietes.title or ""
        artiste = proprietes.artist or ""

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

    # ---- Commandes envoyées depuis l'interface -------------------------
    def commande(self, action: str):
        """
        Appelé depuis l'INTERFACE (autre thread). On planifie l'exécution de la
        coroutine sur la boucle asyncio du thread de lecture, de façon sûre.
        action = 'toggle' | 'next' | 'prev'
        """
        loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._executer(action), loop)

    async def _executer(self, action: str):
        try:
            session = self._gestionnaire.get_current_session()
            if session is None:
                return
            if action == "toggle":
                await session.try_toggle_play_pause_async()
            elif action == "next":
                await session.try_skip_next_async()
            elif action == "prev":
                await session.try_skip_previous_async()
        except Exception as erreur:
            print(f"[commande {action}] {erreur}")


# ====================================================================== #
#  LA FENÊTRE (Dynamic Island)
# ====================================================================== #
class Island(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._rect_replie = self._calculer_rect(LARGEUR_REPLIE, HAUTEUR_REPLIE)
        self._rect_etendu = self._calculer_rect(LARGEUR_ETENDU, HAUTEUR_ETENDU)
        self._zone_declenchement = self._calculer_rect(TRIGGER_LARGEUR, TRIGGER_HAUTEUR)

        self._etendu = False
        self.setGeometry(self._rect_replie)

        self._lecteur = None            # sera défini via definir_lecteur()
        self._etat_courant = "other"    # 'playing' | 'paused' | 'other'

        self._construire_contenu()

        self._pochette_placeholder = self._faire_placeholder(COTE_POCHETTE, RAYON_POCHETTE)
        self.lbl_pochette.setPixmap(self._pochette_placeholder)

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(DUREE_ANIM)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._quand_anim_finie)

        self._opacite = QGraphicsOpacityEffect(self.contenu)
        self._opacite.setOpacity(0.0)
        self.contenu.setGraphicsEffect(self._opacite)
        self.contenu.hide()
        self._anim_fondu = QPropertyAnimation(self._opacite, b"opacity")
        self._anim_fondu.setDuration(DUREE_FONDU)

        self._timer_souris = QTimer(self)
        self._timer_souris.timeout.connect(self._verifier_souris)
        self._timer_souris.start(50)

        self._timer_premier_plan = QTimer(self)
        self._timer_premier_plan.timeout.connect(self.raise_)
        self._timer_premier_plan.start(500)

    def definir_lecteur(self, lecteur: LecteurMedia):
        """Donne au panneau la référence du thread, pour envoyer les commandes."""
        self._lecteur = lecteur

    # ------------------------------------------------------------------ #
    #  Interface
    # ------------------------------------------------------------------ #
    def _construire_contenu(self):
        self.contenu = QWidget(self)
        self.contenu.setGeometry(0, 0, LARGEUR_ETENDU, HAUTEUR_ETENDU)

        self.contenu.setStyleSheet(
            "QLabel#titre   { color: #FFFFFF; }"
            "QLabel#artiste { color: #BEBEBE; }"
            "QPushButton {"
            "   background: transparent; border: none; color: #E8E8E8;"
            "   font-family: 'Segoe MDL2 Assets'; padding: 0px;"
            "}"
            "QPushButton:hover  { color: #FFFFFF; }"
            "QPushButton:pressed{ color: #9A9A9A; }"
        )

        # Pochette
        self.lbl_pochette = QLabel()
        self.lbl_pochette.setFixedSize(COTE_POCHETTE, COTE_POCHETTE)

        # Titre / artiste
        self.lbl_titre = QLabel("—")
        self.lbl_titre.setObjectName("titre")
        f = QFont(); f.setPointSize(11); f.setBold(True)
        self.lbl_titre.setFont(f)

        self.lbl_artiste = QLabel("")
        self.lbl_artiste.setObjectName("artiste")
        f = QFont(); f.setPointSize(9)
        self.lbl_artiste.setFont(f)

        self._largeur_texte = LARGEUR_ETENDU - 16 - 14 - COTE_POCHETTE - 16
        self.lbl_titre.setFixedWidth(self._largeur_texte)
        self.lbl_artiste.setFixedWidth(self._largeur_texte)

        # Boutons de contrôle
        self.btn_prec = self._faire_bouton(ICONE_PRECEDENT, 30)
        self.btn_play = self._faire_bouton(ICONE_LECTURE, 34)
        self.btn_suiv = self._faire_bouton(ICONE_SUIVANT, 30)

        self.btn_prec.clicked.connect(lambda: self._envoyer("prev"))
        self.btn_play.clicked.connect(self._basculer_lecture)
        self.btn_suiv.clicked.connect(lambda: self._envoyer("next"))

        rangee_boutons = QHBoxLayout()
        rangee_boutons.setContentsMargins(0, 4, 0, 0)
        rangee_boutons.setSpacing(10)
        rangee_boutons.addWidget(self.btn_prec)
        rangee_boutons.addWidget(self.btn_play)
        rangee_boutons.addWidget(self.btn_suiv)
        rangee_boutons.addStretch(1)

        colonne = QVBoxLayout()
        colonne.setContentsMargins(0, 0, 0, 0)
        colonne.setSpacing(2)
        colonne.addStretch(1)
        colonne.addWidget(self.lbl_titre)
        colonne.addWidget(self.lbl_artiste)
        colonne.addLayout(rangee_boutons)
        colonne.addStretch(1)

        ligne = QHBoxLayout(self.contenu)
        ligne.setContentsMargins(16, 16, 16, 14)
        ligne.setSpacing(14)
        ligne.addWidget(self.lbl_pochette)
        ligne.addLayout(colonne)

    def _faire_bouton(self, glyphe: str, taille_police: int) -> QPushButton:
        b = QPushButton(glyphe)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(34, 30)
        f = QFont("Segoe MDL2 Assets"); f.setPointSize(taille_police // 2 + 6)
        b.setFont(f)
        return b

    # ------------------------------------------------------------------ #
    #  Commandes
    # ------------------------------------------------------------------ #
    def _envoyer(self, action: str):
        if self._lecteur is not None:
            self._lecteur.commande(action)

    def _basculer_lecture(self):
        # Retour visuel immédiat : on inverse l'icône tout de suite.
        if self._etat_courant == "playing":
            self._etat_courant = "paused"
        else:
            self._etat_courant = "playing"
        self._maj_bouton_play()
        self._envoyer("toggle")

    def _maj_bouton_play(self):
        # Si ça joue -> on affiche l'icône PAUSE (l'action possible), et inversement.
        self.btn_play.setText(
            ICONE_PAUSE if self._etat_courant == "playing" else ICONE_LECTURE
        )

    # ------------------------------------------------------------------ #
    #  Réception des infos musique
    # ------------------------------------------------------------------ #
    def maj_infos(self, infos):
        if infos is None:
            self.lbl_titre.setText("Aucune musique")
            self.lbl_artiste.setText("")
            self._etat_courant = "other"
            self._maj_bouton_play()
            self.lbl_pochette.setPixmap(self._pochette_placeholder)
            return

        self._texte_elide(self.lbl_titre, infos["titre"])
        self._texte_elide(self.lbl_artiste, infos["artiste"])

        self._etat_courant = infos["etat"]
        self._maj_bouton_play()

        if infos["pochette"]:
            self.lbl_pochette.setPixmap(
                self._pixmap_arrondie(infos["pochette"], COTE_POCHETTE, RAYON_POCHETTE)
            )
        else:
            self.lbl_pochette.setPixmap(self._pochette_placeholder)

    def _texte_elide(self, label: QLabel, texte: str):
        metriques = label.fontMetrics()
        label.setText(
            metriques.elidedText(texte or "", Qt.ElideRight, self._largeur_texte)
        )

    # ------------------------------------------------------------------ #
    #  Images
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pixmap_arrondie(data: bytes, cote: int, rayon: int) -> QPixmap:
        source = QPixmap()
        source.loadFromData(data)
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
        dx = (cote - source.width()) / 2
        dy = (cote - source.height()) / 2
        peintre.drawPixmap(int(dx), int(dy), source)
        peintre.end()
        return resultat

    @staticmethod
    def _faire_placeholder(cote: int, rayon: int) -> QPixmap:
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
        if self._etendu:
            self.contenu.show()
            self._anim_fondu.stop()
            self._anim_fondu.setStartValue(0.0)
            self._anim_fondu.setEndValue(1.0)
            self._anim_fondu.start()

    # ------------------------------------------------------------------ #
    #  Fond
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

    lecteur = LecteurMedia()
    lecteur.infos.connect(fenetre.maj_infos)
    fenetre.definir_lecteur(lecteur)
    lecteur.start()

    app.aboutToQuit.connect(lecteur.arreter)

    signal.signal(signal.SIGINT, lambda *args: app.quit())
    reveil = QTimer()
    reveil.start(200)
    reveil.timeout.connect(lambda: None)

    print("Dynamic Island (étape 5) lancée.")
    print("👉 Survole le haut au centre, puis clique ⏮  ▶/⏸  ⏭.")
    print("Ferme avec Ctrl + C (ici) ou la touche Échap.")

    code = app.exec()
    lecteur.arreter()
    lecteur.wait(1500)
    sys.exit(code)


if __name__ == "__main__":
    main()
