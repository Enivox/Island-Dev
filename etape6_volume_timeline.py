# -*- coding: utf-8 -*-
"""
ÉTAPE 6 — Slider de volume + Timeline (barre de progression)
============================================================

Nouveautés par rapport à l'étape 5 :
  - TIMELINE : une barre de progression qui avance en temps réel, avec le temps
    écoulé et la durée totale. On peut la GLISSER pour se déplacer dans le
    morceau (si la source le permet).
  - VOLUME : un slider qui lit et règle le volume principal de Windows (pycaw).
    Il reflète aussi les changements de volume faits ailleurs.

Rappels techniques :
  - La musique (infos + position) est lue dans le THREAD de fond (winrt).
    -> deux signaux : `infos` (titre/artiste/pochette/état, quand ça change) et
       `progression` (position/durée, à chaque tour ~0,5 s).
  - Pour que la barre soit FLUIDE entre deux lectures, l'interface "interpole"
    la position avec une petite horloge locale.
  - Le VOLUME (pycaw = COM) est géré côté interface (appels courts et rapides).

Pour lancer :
    python etape6_volume_timeline.py

Pour quitter : Ctrl + C dans la console (ou la touche Échap).
"""

import sys
import time
import signal
import asyncio

from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QSlider,
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

LARGEUR_ETENDU = 460
HAUTEUR_ETENDU = 150

TRIGGER_LARGEUR = 350
TRIGGER_HAUTEUR = 8
MARGE_SURVOL = 8

RAYON_MAX = 26
COULEUR_FOND = QColor(12, 12, 12, 245)
COTE_POCHETTE = 78
RAYON_POCHETTE = 14

DUREE_ANIM = 280
DUREE_FONDU = 160

# Glyphes "Segoe MDL2 Assets"
ICONE_PRECEDENT = ""
ICONE_LECTURE = ""
ICONE_PAUSE = ""
ICONE_SUIVANT = ""
ICONE_VOLUME = ""


def formater_temps(secondes: float) -> str:
    """Transforme un nombre de secondes en 'm:ss'."""
    secondes = max(0, int(secondes))
    return f"{secondes // 60}:{secondes % 60:02d}"


# ====================================================================== #
#  VOLUME SYSTÈME (pycaw)
# ====================================================================== #
class VolumeSysteme:
    """Petit utilitaire pour lire/régler le volume principal de Windows."""

    def __init__(self):
        self._endpoint = None
        self._initialiser()

    def _initialiser(self):
        try:
            from pycaw.pycaw import AudioUtilities
            haut_parleurs = AudioUtilities.GetSpeakers()
            # Nouvelle API pycaw : l'objet expose directement EndpointVolume.
            self._endpoint = haut_parleurs.EndpointVolume
        except Exception as erreur:
            print(f"[Volume] indisponible : {erreur}")
            self._endpoint = None

    def lire(self):
        """Retourne le volume en % (0-100), ou None si indisponible."""
        if self._endpoint is None:
            return None
        try:
            return int(round(self._endpoint.GetMasterVolumeLevelScalar() * 100))
        except Exception:
            self._initialiser()
            return None

    def regler(self, pourcent: int):
        """Règle le volume principal (0-100 %)."""
        if self._endpoint is None:
            return
        try:
            valeur = max(0, min(100, pourcent)) / 100.0
            self._endpoint.SetMasterVolumeLevelScalar(valeur, None)
        except Exception:
            self._initialiser()


# ====================================================================== #
#  THREAD DE LECTURE + CONTRÔLE
# ====================================================================== #
class LecteurMedia(QThread):
    infos = Signal(object)                    # dict (ou None) : titre/artiste/…
    progression = Signal(float, float, bool)  # position_s, duree_s, en_lecture

    def __init__(self):
        super().__init__()
        self._actif = True
        self._signature = object()
        self._piste_cache = None
        self._pochette_cache = None
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
        self._loop = asyncio.get_running_loop()
        self._gestionnaire = await GestionnaireMedia.request_async()

        while self._actif:
            try:
                infos, prog = await self._lire(self._gestionnaire)
            except Exception:
                infos, prog = None, (0.0, 0.0, False)

            signature = None if infos is None else (
                infos["titre"], infos["artiste"], infos["etat"]
            )
            if signature != self._signature:
                self._signature = signature
                self.infos.emit(infos)

            # La progression est émise à chaque tour (pour faire avancer la barre).
            self.progression.emit(*prog)

            for _ in range(5):  # ~0,5 s
                if not self._actif:
                    break
                await asyncio.sleep(0.1)

    async def _lire(self, gestionnaire):
        session = gestionnaire.get_current_session()
        if session is None:
            return None, (0.0, 0.0, False)

        proprietes = await session.try_get_media_properties_async()
        etat_brut = session.get_playback_info().playback_status
        ligne_temps = session.get_timeline_properties()

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

        infos = {
            "titre": titre or "(titre inconnu)",
            "artiste": artiste,
            "album": proprietes.album_title or "",
            "etat": etat,
            "pochette": self._pochette_cache,
        }

        position = ligne_temps.position.total_seconds()
        duree = ligne_temps.end_time.total_seconds()
        prog = (position, duree, etat == "playing")
        return infos, prog

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

    # ---- Commandes -----------------------------------------------------
    def commande(self, action: str):
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

    def chercher(self, position_sec: float):
        """Se déplacer dans le morceau (glisser la timeline)."""
        loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._chercher(position_sec), loop)

    async def _chercher(self, position_sec: float):
        try:
            session = self._gestionnaire.get_current_session()
            if session is None:
                return
            ticks = int(position_sec * 10_000_000)  # 1 tick = 100 ns
            await session.try_change_playback_position_async(ticks)
        except Exception as erreur:
            print(f"[seek] {erreur}")


# ====================================================================== #
#  LA FENÊTRE (Dynamic Island)
# ====================================================================== #
class Island(QWidget):
    def __init__(self, volume: VolumeSysteme):
        super().__init__()

        self._volume = volume
        self._lecteur = None
        self._etat_courant = "other"

        # Interpolation de la timeline
        self._pos_base = 0.0
        self._duree = 0.0
        self._en_lecture = False
        self._t_base = time.monotonic()
        self._drag_progress = False
        self._maj_prog_programmatique = False
        self._maj_vol_programmatique = False

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._rect_replie = self._calculer_rect(LARGEUR_REPLIE, HAUTEUR_REPLIE)
        self._rect_etendu = self._calculer_rect(LARGEUR_ETENDU, HAUTEUR_ETENDU)
        self._zone_declenchement = self._calculer_rect(TRIGGER_LARGEUR, TRIGGER_HAUTEUR)

        self._etendu = False
        self.setGeometry(self._rect_replie)

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

        # Timers
        self._timer_souris = QTimer(self)
        self._timer_souris.timeout.connect(self._verifier_souris)
        self._timer_souris.start(50)

        self._timer_premier_plan = QTimer(self)
        self._timer_premier_plan.timeout.connect(self.raise_)
        self._timer_premier_plan.start(500)

        self._timer_timeline = QTimer(self)   # fait avancer la barre en douceur
        self._timer_timeline.timeout.connect(self._rafraichir_timeline)
        self._timer_timeline.start(200)

        self._timer_volume = QTimer(self)      # reflète le volume système
        self._timer_volume.timeout.connect(self._rafraichir_volume)
        self._timer_volume.start(700)

        # Valeur initiale du volume
        self._rafraichir_volume()

    def definir_lecteur(self, lecteur: LecteurMedia):
        self._lecteur = lecteur

    # ------------------------------------------------------------------ #
    #  Interface
    # ------------------------------------------------------------------ #
    def _construire_contenu(self):
        self.contenu = QWidget(self)
        self.contenu.setGeometry(0, 0, LARGEUR_ETENDU, HAUTEUR_ETENDU)

        self.contenu.setStyleSheet("""
            QLabel#titre   { color: #FFFFFF; }
            QLabel#artiste { color: #BEBEBE; }
            QLabel#temps   { color: #9A9A9A; }
            QLabel#volicone{ color: #BEBEBE; font-family: 'Segoe MDL2 Assets'; }
            QPushButton {
                background: transparent; border: none; color: #E8E8E8;
                font-family: 'Segoe MDL2 Assets'; padding: 0px;
            }
            QPushButton:hover  { color: #FFFFFF; }
            QPushButton:pressed{ color: #9A9A9A; }
            QSlider::groove:horizontal { height: 4px; background: #3A3A3A; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #E4E4E4; border-radius: 2px; }
            QSlider::add-page:horizontal { background: #3A3A3A; border-radius: 2px; }
            QSlider::handle:horizontal {
                width: 11px; height: 11px; margin: -4px 0; border-radius: 6px; background: #FFFFFF;
            }
            QSlider:disabled::sub-page:horizontal { background: #555555; }
        """)

        # --- Pochette ---
        self.lbl_pochette = QLabel()
        self.lbl_pochette.setFixedSize(COTE_POCHETTE, COTE_POCHETTE)

        # --- Titre / artiste ---
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

        # --- Timeline ---
        self.lbl_temps = QLabel("0:00"); self.lbl_temps.setObjectName("temps")
        self.lbl_duree = QLabel("0:00"); self.lbl_duree.setObjectName("temps")
        for lbl in (self.lbl_temps, self.lbl_duree):
            fp = QFont(); fp.setPointSize(8); lbl.setFont(fp)
            lbl.setFixedWidth(34)
        self.lbl_temps.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_duree.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.slider_progress = QSlider(Qt.Horizontal)
        self.slider_progress.setRange(0, 1000)
        self.slider_progress.setValue(0)
        self.slider_progress.sliderPressed.connect(self._debut_drag_progress)
        self.slider_progress.sliderReleased.connect(self._fin_drag_progress)
        self.slider_progress.valueChanged.connect(self._preview_progress)

        rangee_timeline = QHBoxLayout()
        rangee_timeline.setContentsMargins(0, 0, 0, 0)
        rangee_timeline.setSpacing(8)
        rangee_timeline.addWidget(self.lbl_temps)
        rangee_timeline.addWidget(self.slider_progress, 1)
        rangee_timeline.addWidget(self.lbl_duree)

        # --- Boutons + volume ---
        self.btn_prec = self._faire_bouton(ICONE_PRECEDENT, 12)
        self.btn_play = self._faire_bouton(ICONE_LECTURE, 15)
        self.btn_suiv = self._faire_bouton(ICONE_SUIVANT, 12)
        self.btn_prec.clicked.connect(lambda: self._envoyer("prev"))
        self.btn_play.clicked.connect(self._basculer_lecture)
        self.btn_suiv.clicked.connect(lambda: self._envoyer("next"))

        self.lbl_vol_icone = QLabel(ICONE_VOLUME)
        self.lbl_vol_icone.setObjectName("volicone")
        fv = QFont("Segoe MDL2 Assets"); fv.setPointSize(10)
        self.lbl_vol_icone.setFont(fv)

        self.slider_volume = QSlider(Qt.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setFixedWidth(96)
        self.slider_volume.valueChanged.connect(self._volume_change)

        rangee_bas = QHBoxLayout()
        rangee_bas.setContentsMargins(0, 0, 0, 0)
        rangee_bas.setSpacing(10)
        rangee_bas.addWidget(self.btn_prec)
        rangee_bas.addWidget(self.btn_play)
        rangee_bas.addWidget(self.btn_suiv)
        rangee_bas.addStretch(1)
        rangee_bas.addWidget(self.lbl_vol_icone)
        rangee_bas.addWidget(self.slider_volume)

        # --- Colonne de droite ---
        colonne = QVBoxLayout()
        colonne.setContentsMargins(0, 0, 0, 0)
        colonne.setSpacing(4)
        colonne.addWidget(self.lbl_titre)
        colonne.addWidget(self.lbl_artiste)
        colonne.addLayout(rangee_timeline)
        colonne.addLayout(rangee_bas)

        # --- Ligne principale ---
        ligne = QHBoxLayout(self.contenu)
        ligne.setContentsMargins(16, 16, 16, 16)
        ligne.setSpacing(14)
        ligne.addWidget(self.lbl_pochette, 0, Qt.AlignTop)
        ligne.addLayout(colonne)

    def _faire_bouton(self, glyphe: str, taille_police: int) -> QPushButton:
        b = QPushButton(glyphe)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(34, 30)
        f = QFont("Segoe MDL2 Assets"); f.setPointSize(taille_police)
        b.setFont(f)
        return b

    # ------------------------------------------------------------------ #
    #  Commandes lecture
    # ------------------------------------------------------------------ #
    def _envoyer(self, action: str):
        if self._lecteur is not None:
            self._lecteur.commande(action)

    def _basculer_lecture(self):
        if self._etat_courant == "playing":
            self._etat_courant = "paused"
        else:
            self._etat_courant = "playing"
        self._maj_bouton_play()
        self._envoyer("toggle")

    def _maj_bouton_play(self):
        self.btn_play.setText(
            ICONE_PAUSE if self._etat_courant == "playing" else ICONE_LECTURE
        )

    # ------------------------------------------------------------------ #
    #  Réception infos
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
    #  Timeline
    # ------------------------------------------------------------------ #
    def maj_progression(self, position_s, duree_s, en_lecture):
        """Reçoit la position lue par le thread (~toutes les 0,5 s)."""
        self._duree = duree_s
        if self._drag_progress:
            return  # on ne bouge pas la barre pendant que l'utilisateur la tient
        self._pos_base = position_s
        self._en_lecture = en_lecture
        self._t_base = time.monotonic()
        self._rafraichir_timeline()

    def _rafraichir_timeline(self):
        """Recalcule la position affichée (interpolation) et met à jour la barre."""
        if self._drag_progress:
            return
        if self._duree <= 0:
            self._regler_progress(0)
            self.lbl_temps.setText("0:00")
            self.lbl_duree.setText("0:00")
            self.slider_progress.setEnabled(False)
            return

        self.slider_progress.setEnabled(True)
        ecoule = (time.monotonic() - self._t_base) if self._en_lecture else 0.0
        position = max(0.0, min(self._pos_base + ecoule, self._duree))

        self._regler_progress(int(position / self._duree * 1000))
        self.lbl_temps.setText(formater_temps(position))
        self.lbl_duree.setText(formater_temps(self._duree))

    def _regler_progress(self, valeur_0_1000):
        """Change la valeur de la barre SANS déclencher un 'seek' (drapeau)."""
        self._maj_prog_programmatique = True
        self.slider_progress.setValue(valeur_0_1000)
        self._maj_prog_programmatique = False

    def _debut_drag_progress(self):
        self._drag_progress = True

    def _fin_drag_progress(self):
        self._drag_progress = False
        if self._duree > 0 and self._lecteur is not None:
            fraction = self.slider_progress.value() / 1000.0
            self._lecteur.chercher(fraction * self._duree)

    def _preview_progress(self, valeur):
        # Pendant le glissement, on met à jour le libellé du temps en aperçu.
        if self._drag_progress and self._duree > 0:
            self.lbl_temps.setText(formater_temps(valeur / 1000.0 * self._duree))

    # ------------------------------------------------------------------ #
    #  Volume
    # ------------------------------------------------------------------ #
    def _volume_change(self, valeur):
        if self._maj_vol_programmatique:
            return  # changement venant du code, pas de l'utilisateur
        self._volume.regler(valeur)

    def _rafraichir_volume(self):
        if self.slider_volume.isSliderDown():
            return  # l'utilisateur est en train de régler : on ne l'écrase pas
        v = self._volume.lire()
        if v is not None and v != self.slider_volume.value():
            self._maj_vol_programmatique = True
            self.slider_volume.setValue(v)
            self._maj_vol_programmatique = False

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
            # On reste ouvert aussi tant qu'on tient un slider (drag hors zone).
            if not zone_ouverte.contains(souris) and not self._un_slider_actif():
                self._fermer()

    def _un_slider_actif(self):
        return self.slider_progress.isSliderDown() or self.slider_volume.isSliderDown()

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

    volume = VolumeSysteme()

    fenetre = Island(volume)
    fenetre.show()

    lecteur = LecteurMedia()
    lecteur.infos.connect(fenetre.maj_infos)
    lecteur.progression.connect(fenetre.maj_progression)
    fenetre.definir_lecteur(lecteur)
    lecteur.start()

    app.aboutToQuit.connect(lecteur.arreter)

    signal.signal(signal.SIGINT, lambda *args: app.quit())
    reveil = QTimer()
    reveil.start(200)
    reveil.timeout.connect(lambda: None)

    print("Dynamic Island (étape 6) lancée.")
    print("👉 Survole le haut au centre : pochette, titre, timeline, contrôles, volume.")
    print("Ferme avec Ctrl + C (ici) ou la touche Échap.")

    code = app.exec()
    lecteur.arreter()
    lecteur.wait(1500)
    sys.exit(code)


if __name__ == "__main__":
    main()
