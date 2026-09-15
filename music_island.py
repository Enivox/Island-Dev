# -*- coding: utf-8 -*-
"""
Music Island — une "Dynamic Island" musique pour Windows
========================================================

Une pastille discrète apparaît en haut au centre de l'écran dès qu'une musique
joue (n'importe quelle appli : Spotify, Deezer/YouTube dans le navigateur…).

  - MINI-ÎLE : petite pochette + visualiseur qui réagit au son réel et prend les
    couleurs de la pochette. Survoler la mini-île déploie le grand panneau.
  - GRAND PANNEAU (look Apple) : noir profond, coins concaves en haut (façon Mac),
    ombre portée, pochette, titre, artiste, timeline (avec seek) et contrôles
    précédent / lecture-pause / suivant. Volume à la molette de la souris.
  - AUTO-MASQUAGE : disparaît quand il n'y a pas de musique ou qu'une appli est en
    plein écran (jeu, vidéo…), réapparaît ensuite.
  - Icône dans la zone de notification : activer/désactiver, démarrer avec Windows,
    quitter.

Techno : PySide6 (interface), winrt (session média Windows), pycaw (volume + niveau
audio du visualiseur).

Lancer :   python music_island.py
Quitter :  via l'icône de notification (ou Ctrl + C dans la console).
"""

import os
import sys
import time
import math
import signal
import asyncio
import ctypes
from ctypes import wintypes

from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton,
    QHBoxLayout, QVBoxLayout, QGraphicsOpacityEffect,
    QSystemTrayIcon, QMenu,
)
from PySide6.QtCore import (
    Qt, QTimer, QRect, QRectF, QThread, Signal,
    QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QGuiApplication, QPainterPath, QCursor, QPixmap, QFont,
    QImage, QLinearGradient, QBrush, QIcon, QAction,
)

from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as GestionnaireMedia,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as EtatLecture,
)
from winrt.windows.storage.streams import DataReader


# ====================================================================== #
#  RÉGLAGES
# ====================================================================== #
# Tailles du PANNEAU VISIBLE
LARGEUR_REPLIE = 132
HAUTEUR_REPLIE = 34
LARGEUR_ETENDU = 430
HAUTEUR_ETENDU = 172

# Marge transparente autour du panneau (pour dessiner l'ombre portée)
MARGE_X = 16        # gauche / droite
MARGE_BAS = 18      # bas

# Déclenchement / survol
MARGE_DECLENCHEMENT = 10
MARGE_SURVOL = 8

# Apparence
RAYON_MAX = 38                                 # coins bien arrondis
COULEUR_FOND = QColor(0, 0, 0, 255)            # NOIR PROFOND, opaque
COULEUR_BORDURE = QColor(255, 255, 255, 22)    # liseré très discret (effet verre)

COTE_POCHETTE = 64
RAYON_POCHETTE = 14
COTE_POCHETTE_MINI = 24
RAYON_POCHETTE_MINI = 6

DUREE_ANIM = 300
DUREE_FONDU = 170

COULEUR_ACCENT = QColor(255, 95, 70)   # couleur "actif" (shuffle allumé), façon Apple

# Icônes MDL2 : haut-parleurs du volume + shuffle + étoile favori.
ICONE_VOLUME_MIN = chr(0xE993)
ICONE_VOLUME_MAX = chr(0xE767)
ICONE_SHUFFLE = chr(0xE8B1)
ICONE_FAV = chr(0xE734)          # étoile contour
ICONE_FAV_PLEIN = chr(0xE735)    # étoile pleine


def formater_temps(secondes: float) -> str:
    secondes = max(0, int(secondes))
    return f"{secondes // 60}:{secondes % 60:02d}"


# --- Détection "une appli est en plein écran ?" (pour masquer l'île) ---------
class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def plein_ecran_actif() -> bool:
    """Vrai si une appli est en PLEIN ÉCRAN (jeu, vidéo YouTube/Netflix, etc.)."""
    # 1) API Windows officielle : jeux plein écran exclusif / mode présentation.
    try:
        etat = ctypes.c_int(0)
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(etat))
        # 2=BUSY(plein écran/présentation), 3=D3D plein écran, 4=présentation, 7=app plein écran
        if etat.value in (2, 3, 4, 7):
            return True
    except Exception:
        pass

    # 2) Fenêtre de premier plan qui couvre TOUT l'écran (vidéo web, borderless).
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        classe = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, classe, 256)
        if classe.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False  # bureau / barre des tâches : pas un plein écran
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        hmon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        m = mi.rcMonitor
        # La fenêtre couvre-t-elle tout l'écran (barre des tâches comprise) ?
        return (
            rect.left <= m.left and rect.top <= m.top
            and rect.right >= m.right and rect.bottom >= m.bottom
            and (m.right - m.left) > 0
        )
    except Exception:
        return False


# --- Démarrage automatique avec Windows (clé de registre HKCU\...\Run) --------
_CLE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_NOM_RUN = "MusicIsland"


def _commande_demarrage() -> str:
    """Commande à lancer au démarrage : l'exe si "gelé", sinon python + script."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def demarrage_auto_actif() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLE_RUN) as cle:
            valeur, _ = winreg.QueryValueEx(cle, _NOM_RUN)
            return bool(valeur)
    except Exception:
        return False


def activer_demarrage_auto(actif: bool):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLE_RUN, 0, winreg.KEY_SET_VALUE) as cle:
            if actif:
                winreg.SetValueEx(cle, _NOM_RUN, 0, winreg.REG_SZ, _commande_demarrage())
            else:
                try:
                    winreg.DeleteValue(cle, _NOM_RUN)
                except FileNotFoundError:
                    pass
    except Exception as erreur:
        print(f"[Démarrage auto] {erreur}")


# ====================================================================== #
#  BARRE "APPLE" (timeline + volume, dessinées à la main)
# ====================================================================== #
class BarreApple(QWidget):
    valeurChangee = Signal(float)
    deplacee = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frac = 0.0
        self._drag = False
        self._survol = False
        self.setMinimumHeight(24)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_fraction(self, f):
        if self._drag:
            return
        f = max(0.0, min(1.0, f))
        if abs(f - self._frac) > 1e-4:
            self._frac = f
            self.update()

    def is_drag(self):
        return self._drag

    def _marge(self):
        return 9

    def _frac_depuis_x(self, x):
        m = self._marge()
        largeur = self.width() - 2 * m
        return max(0.0, min(1.0, (x - m) / largeur)) if largeur > 0 else 0.0

    def mousePressEvent(self, e):
        self._drag = True
        self._frac = self._frac_depuis_x(e.position().x())
        self.valeurChangee.emit(self._frac); self.update()

    def mouseMoveEvent(self, e):
        if self._drag:
            self._frac = self._frac_depuis_x(e.position().x())
            self.valeurChangee.emit(self._frac); self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.deplacee.emit(self._frac); self.update()

    def enterEvent(self, e):
        self._survol = True; self.update()

    def leaveEvent(self, e):
        self._survol = False; self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        m = self._marge()
        cy = self.height() / 2
        actif = self._survol or self._drag
        hp = 8 if actif else 6                  # à peine plus épaisse quand on la touche
        x0, x1 = m, self.width() - m
        largeur = max(1.0, x1 - x0)
        xf = x0 + largeur * self._frac

        # Piste de fond (translucide)
        p.setBrush(QColor(255, 255, 255, 45))
        p.drawRoundedRect(QRectF(x0, cy - hp / 2, largeur, hp), hp / 2, hp / 2)

        # Remplissage : BLANC quand on la touche, GRIS au repos (comme iPhone).
        remplissage = QColor(255, 255, 255, 245) if actif else QColor(255, 255, 255, 150)
        p.setBrush(remplissage)
        p.drawRoundedRect(QRectF(x0, cy - hp / 2, max(hp, xf - x0), hp), hp / 2, hp / 2)

        if self._drag:
            r = 8.0
            p.setBrush(QColor(255, 255, 255, 255))
            p.drawEllipse(QRectF(xf - r, cy - r, 2 * r, 2 * r))


# ====================================================================== #
#  BOUTONS DE TRANSPORT (dessinés à la main, pleins + coins arrondis)
# ====================================================================== #
class BoutonMedia(QPushButton):
    def __init__(self, type_icone, parent=None):
        super().__init__(parent)
        self._type = type_icone
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(52, 40)

    def set_type(self, t):
        if t != self._type:
            self._type = t; self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        couleur = QColor(170, 170, 170) if self.isDown() else QColor(255, 255, 255)
        cx, cy = self.width() / 2, self.height() / 2
        t = self._type

        stylo = QPen(couleur, 3.4)
        stylo.setJoinStyle(Qt.RoundJoin)
        stylo.setCapStyle(Qt.RoundCap)

        if t == "play":
            s = 13                                  # plus grand que rewind/forward
            tri = QPainterPath()
            tri.moveTo(cx - s * 0.80, cy - s)
            tri.lineTo(cx - s * 0.80, cy + s)
            tri.lineTo(cx + s * 1.05, cy)
            tri.closeSubpath()
            p.setPen(stylo); p.setBrush(couleur); p.drawPath(tri)
        elif t == "pause":
            # Deux barres épaisses, PROCHES, plus LONGUES (façon Apple) + coins carrés.
            bw, bh, gap = 7.0, 26, 2.8
            r = 2.0
            p.setPen(Qt.NoPen); p.setBrush(couleur)
            p.drawRoundedRect(QRectF(cx - gap - bw, cy - bh / 2, bw, bh), r, r)
            p.drawRoundedRect(QRectF(cx + gap, cy - bh / 2, bw, bh), r, r)
        elif t == "next":
            s, w = 9, 11
            p.setPen(stylo); p.setBrush(couleur)
            for xl in (cx - w, cx):
                tri = QPainterPath()
                tri.moveTo(xl, cy - s); tri.lineTo(xl, cy + s); tri.lineTo(xl + w, cy)
                tri.closeSubpath(); p.drawPath(tri)
        elif t == "prev":
            s, w = 9, 11
            p.setPen(stylo); p.setBrush(couleur)
            for xr in (cx, cx + w):
                tri = QPainterPath()
                tri.moveTo(xr, cy - s); tri.lineTo(xr, cy + s); tri.lineTo(xr - w, cy)
                tri.closeSubpath(); p.drawPath(tri)


# ====================================================================== #
#  BOUTON GLYPHE (shuffle / étoile) — icône de la police MDL2, couleur variable
# ====================================================================== #
class BoutonGlyphe(QPushButton):
    def __init__(self, glyphe, taille=13, parent=None):
        super().__init__(parent)
        self._glyphe = glyphe
        self._couleur = QColor(150, 150, 150)
        self._taille = taille
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 34)

    def config(self, glyphe=None, couleur=None):
        if glyphe is not None:
            self._glyphe = glyphe
        if couleur is not None:
            self._couleur = couleur
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(self._couleur)
        if self.isDown():
            col = col.darker(130)
        p.setPen(col)
        f = QFont("Segoe MDL2 Assets"); f.setPointSize(self._taille)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self._glyphe)


# ====================================================================== #
#  VISUALISEUR AUDIO
# ====================================================================== #
class Visualiseur(QWidget):
    """
    Barres qui réagissent au VRAI son (niveau audio du système) et prennent les
    COULEURS de la pochette (dégradé vertical).
    """
    def __init__(self, nb_barres=5, largeur_barre=3, espace=3, hauteur=18, parent=None):
        super().__init__(parent)
        self._nb = nb_barres
        self._lb = largeur_barre
        self._esp = espace
        self._phase = 0.0
        self._actif = False
        self._niveau = 0.0                    # niveau audio courant (0..1)
        self._hauteurs = [0.0] * nb_barres    # hauteur lissée de chaque barre
        self._c1 = QColor(255, 255, 255)      # couleur haut (dégradé)
        self._c2 = QColor(200, 200, 200)      # couleur bas
        self.setFixedSize(nb_barres * largeur_barre + (nb_barres - 1) * espace, hauteur)

    def set_actif(self, actif):
        self._actif = actif

    def set_niveau(self, peak):
        self._niveau = max(0.0, min(1.0, peak))

    def set_couleurs(self, c1, c2):
        self._c1, self._c2 = c1, c2
        self.update()

    def avancer(self):
        self._phase += 0.35
        # Niveau effectif : un minimum de mouvement quand ça joue, + le vrai son.
        if self._actif:
            niveau = min(1.0, 0.25 + self._niveau * 1.6)
        else:
            niveau = 0.0
        for i in range(self._nb):
            osc = 0.55 + 0.45 * math.sin(self._phase * 1.7 + i * 1.15)
            cible = niveau * osc
            # lissage (les barres montent/descendent en douceur)
            self._hauteurs[i] += (cible - self._hauteurs[i]) * 0.4
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, self._c1)
        grad.setColorAt(1.0, self._c2)
        p.setBrush(QBrush(grad))
        h_max, h_min = self.height(), 3
        for i in range(self._nb):
            h = h_min + self._hauteurs[i] * (h_max - h_min)
            x = i * (self._lb + self._esp)
            p.drawRoundedRect(QRectF(x, (h_max - h) / 2, self._lb, h), 1.5, 1.5)


# ====================================================================== #
#  VOLUME SYSTÈME (pycaw)
# ====================================================================== #
class VolumeSysteme:
    def __init__(self):
        self._endpoint = None
        self._initialiser()

    def _initialiser(self):
        try:
            from pycaw.pycaw import AudioUtilities
            self._endpoint = AudioUtilities.GetSpeakers().EndpointVolume
        except Exception as erreur:
            print(f"[Volume] indisponible : {erreur}")
            self._endpoint = None

    def lire(self):
        if self._endpoint is None:
            return None
        try:
            return int(round(self._endpoint.GetMasterVolumeLevelScalar() * 100))
        except Exception:
            self._initialiser(); return None

    def regler(self, pourcent):
        if self._endpoint is None:
            return
        try:
            self._endpoint.SetMasterVolumeLevelScalar(max(0, min(100, pourcent)) / 100.0, None)
        except Exception:
            self._initialiser()


class NiveauAudio:
    """Lit le niveau sonore instantané du système (0..1) via le 'peak meter'
    Windows -> sert à faire réagir le visualiseur au rythme de la musique."""

    def __init__(self):
        self._meter = None
        self._initialiser()

    def _initialiser(self):
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
            dev = AudioUtilities.GetSpeakers()
            iface = dev._dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
            self._meter = cast(iface, POINTER(IAudioMeterInformation))
        except Exception as erreur:
            print(f"[Niveau audio] indisponible : {erreur}")
            self._meter = None

    def peak(self):
        if self._meter is None:
            return 0.0
        try:
            return float(self._meter.GetPeakValue())
        except Exception:
            self._initialiser()
            return 0.0


# ====================================================================== #
#  THREAD DE LECTURE + CONTRÔLE
# ====================================================================== #
class LecteurMedia(QThread):
    infos = Signal(object)
    progression = Signal(float, float, bool)

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
                infos["titre"], infos["artiste"], infos["etat"], infos["shuffle"]
            )
            if signature != self._signature:
                self._signature = signature
                self.infos.emit(infos)
            self.progression.emit(*prog)
            for _ in range(5):
                if not self._actif:
                    break
                await asyncio.sleep(0.1)

    async def _lire(self, gestionnaire):
        session = gestionnaire.get_current_session()
        if session is None:
            return None, (0.0, 0.0, False)
        proprietes = await session.try_get_media_properties_async()
        pb = session.get_playback_info()
        etat_brut = pb.playback_status
        shuffle = bool(pb.is_shuffle_active) if pb.is_shuffle_active is not None else False
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
            "shuffle": shuffle,
            "pochette": self._pochette_cache,
        }
        prog = (
            ligne_temps.position.total_seconds(),
            ligne_temps.end_time.total_seconds(),
            etat == "playing",
        )
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

    def commande(self, action):
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._executer(action), self._loop)

    async def _executer(self, action):
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
            elif action == "shuffle":
                pb = session.get_playback_info()
                cur = bool(pb.is_shuffle_active) if pb.is_shuffle_active is not None else False
                await session.try_change_shuffle_active_async(not cur)
        except Exception as erreur:
            print(f"[commande {action}] {erreur}")

    def chercher(self, position_sec):
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._chercher(position_sec), self._loop)

    async def _chercher(self, position_sec):
        try:
            session = self._gestionnaire.get_current_session()
            if session is None:
                return
            await session.try_change_playback_position_async(int(position_sec * 10_000_000))
        except Exception as erreur:
            print(f"[seek] {erreur}")


# ====================================================================== #
#  LA FENÊTRE (Dynamic Island)
# ====================================================================== #
class Island(QWidget):
    def __init__(self, volume):
        super().__init__()
        self._volume = volume
        self._niveau_audio = NiveauAudio()   # pour faire réagir le visualiseur au son
        self._lecteur = None
        self._etat_courant = "other"
        self._musique_presente = False
        self._plein_ecran = False           # une appli est-elle en plein écran ?
        self._actif_utilisateur = True      # l'île est-elle activée (menu tray) ?

        self._pos_base = 0.0
        self._duree = 0.0
        self._en_lecture = False
        self._t_base = time.monotonic()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Fenêtres (panneau + marge pour l'ombre) et panneaux visibles (écran).
        self._win_replie = self._rect_centre(LARGEUR_REPLIE + 2 * MARGE_X, HAUTEUR_REPLIE + MARGE_BAS)
        self._win_etendu = self._rect_centre(LARGEUR_ETENDU + 2 * MARGE_X, HAUTEUR_ETENDU + MARGE_BAS)
        self._panneau_replie = self._rect_centre(LARGEUR_REPLIE, HAUTEUR_REPLIE)
        self._panneau_etendu = self._rect_centre(LARGEUR_ETENDU, HAUTEUR_ETENDU)
        self._zone_declenchement = self._panneau_replie.adjusted(
            -MARGE_DECLENCHEMENT, 0, MARGE_DECLENCHEMENT, MARGE_DECLENCHEMENT
        )

        self._etendu = False
        self.setGeometry(self._win_replie)

        self._construire_mini()
        self._construire_contenu()

        self._pochette_placeholder = self._faire_placeholder(COTE_POCHETTE, RAYON_POCHETTE)
        self._pochette_placeholder_mini = self._faire_placeholder(COTE_POCHETTE_MINI, RAYON_POCHETTE_MINI)
        self.lbl_pochette.setPixmap(self._pochette_placeholder)
        self.lbl_pochette_mini.setPixmap(self._pochette_placeholder_mini)

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(DUREE_ANIM)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._quand_anim_finie)

        self._op_contenu = QGraphicsOpacityEffect(self.contenu)
        self._op_contenu.setOpacity(0.0)
        self.contenu.setGraphicsEffect(self._op_contenu)
        self.contenu.hide()
        self._fondu_contenu = QPropertyAnimation(self._op_contenu, b"opacity")
        self._fondu_contenu.setDuration(DUREE_FONDU)

        self._op_mini = QGraphicsOpacityEffect(self.mini)
        self._op_mini.setOpacity(1.0)
        self.mini.setGraphicsEffect(self._op_mini)
        self._fondu_mini = QPropertyAnimation(self._op_mini, b"opacity")
        self._fondu_mini.setDuration(DUREE_FONDU)

        self._timer_souris = QTimer(self); self._timer_souris.timeout.connect(self._verifier_souris); self._timer_souris.start(50)
        self._timer_premier_plan = QTimer(self); self._timer_premier_plan.timeout.connect(self._rester_au_dessus); self._timer_premier_plan.start(500)
        self._timer_timeline = QTimer(self); self._timer_timeline.timeout.connect(self._rafraichir_timeline); self._timer_timeline.start(200)
        self._timer_viz = QTimer(self); self._timer_viz.timeout.connect(self._animer_visualiseurs); self._timer_viz.start(60)
        self._timer_plein_ecran = QTimer(self); self._timer_plein_ecran.timeout.connect(self._verifier_plein_ecran); self._timer_plein_ecran.start(800)

        self.hide()

    def definir_lecteur(self, lecteur):
        self._lecteur = lecteur

    def _animer_visualiseurs(self):
        # On lit le niveau sonore réel (0..1) et on le donne aux deux visualiseurs.
        peak = self._niveau_audio.peak() if self._musique_presente else 0.0
        self.visualiseur.set_niveau(peak)
        self.visualiseur_grand.set_niveau(peak)
        self.visualiseur.avancer()
        self.visualiseur_grand.avancer()

    # ------------------------------------------------------------------ #
    #  Mini-île
    # ------------------------------------------------------------------ #
    def _construire_mini(self):
        self.mini = QWidget(self)
        self.mini.setGeometry(MARGE_X, 0, LARGEUR_REPLIE, HAUTEUR_REPLIE)
        self.lbl_pochette_mini = QLabel()
        self.lbl_pochette_mini.setFixedSize(COTE_POCHETTE_MINI, COTE_POCHETTE_MINI)
        self.visualiseur = Visualiseur(nb_barres=4, hauteur=16)
        # Pochette + visualiseur GROUPÉS et CENTRÉS (pas collés aux bords).
        ligne = QHBoxLayout(self.mini)
        ligne.setContentsMargins(0, 0, 0, 4)
        ligne.setSpacing(0)
        ligne.addStretch(1)
        ligne.addWidget(self.lbl_pochette_mini, 0, Qt.AlignVCenter)
        ligne.addSpacing(12)
        ligne.addWidget(self.visualiseur, 0, Qt.AlignVCenter)
        ligne.addStretch(1)

    # ------------------------------------------------------------------ #
    #  Grand panneau
    # ------------------------------------------------------------------ #
    def _construire_contenu(self):
        self.contenu = QWidget(self)
        self.contenu.setGeometry(MARGE_X, 0, LARGEUR_ETENDU, HAUTEUR_ETENDU)
        self.contenu.setStyleSheet("""
            QLabel#titre    { color: #FFFFFF; }
            QLabel#artiste  { color: #B9B9B9; }
            QLabel#temps    { color: #9A9A9A; }
            QLabel#volicone { color: #C8C8C8; font-family: 'Segoe MDL2 Assets'; }
            QPushButton { background: transparent; border: none; }
        """)

        # ===== HAUT : pochette + titre/artiste + visualiseur (à droite) =====
        self.lbl_pochette = QLabel()
        self.lbl_pochette.setFixedSize(COTE_POCHETTE, COTE_POCHETTE)

        self.lbl_titre = QLabel("—")
        self.lbl_titre.setObjectName("titre")
        f = QFont(); f.setPointSize(12); f.setBold(True); self.lbl_titre.setFont(f)

        self.lbl_artiste = QLabel("")
        self.lbl_artiste.setObjectName("artiste")
        f = QFont(); f.setPointSize(9); self.lbl_artiste.setFont(f)

        self.visualiseur_grand = Visualiseur(nb_barres=5, hauteur=18)

        # Largeur dispo pour le texte du titre (entre la pochette et le visualiseur).
        self._largeur_texte = LARGEUR_ETENDU - 18 - COTE_POCHETTE - 12 - self.visualiseur_grand.width() - 18
        self.lbl_titre.setFixedWidth(self._largeur_texte)
        self.lbl_artiste.setFixedWidth(self._largeur_texte)

        col_texte = QVBoxLayout()
        col_texte.setContentsMargins(0, 0, 0, 0); col_texte.setSpacing(2)
        col_texte.addStretch(1)
        col_texte.addWidget(self.lbl_titre)
        col_texte.addWidget(self.lbl_artiste)
        col_texte.addStretch(1)

        rangee_haut = QHBoxLayout()
        rangee_haut.setContentsMargins(0, 0, 0, 0); rangee_haut.setSpacing(12)
        rangee_haut.addWidget(self.lbl_pochette, 0, Qt.AlignVCenter)
        rangee_haut.addLayout(col_texte, 1)
        rangee_haut.addWidget(self.visualiseur_grand, 0, Qt.AlignTop)

        # ===== TIMELINE pleine largeur + temps en dessous =====
        self.slider_progress = BarreApple()
        self.slider_progress.valeurChangee.connect(self._preview_progress)
        self.slider_progress.deplacee.connect(self._seek)

        self.lbl_temps = QLabel("0:00"); self.lbl_temps.setObjectName("temps")
        self.lbl_duree = QLabel("-0:00"); self.lbl_duree.setObjectName("temps")
        for lbl in (self.lbl_temps, self.lbl_duree):
            fp = QFont(); fp.setPointSize(8); lbl.setFont(fp); lbl.setFixedWidth(40)
        self.lbl_temps.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_duree.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # Temps ÉCOULÉ + barre + temps RESTANT, tout sur la MÊME ligne.
        bloc_timeline = QHBoxLayout()
        bloc_timeline.setContentsMargins(0, 0, 0, 0); bloc_timeline.setSpacing(8)
        bloc_timeline.addWidget(self.lbl_temps)
        bloc_timeline.addWidget(self.slider_progress, 1)
        bloc_timeline.addWidget(self.lbl_duree)

        # ===== BOUTONS (3, centrés) =====
        self.btn_prec = BoutonMedia("prev")
        self.btn_play = BoutonMedia("play")
        self.btn_suiv = BoutonMedia("next")
        self.btn_prec.clicked.connect(lambda: self._envoyer("prev"))
        self.btn_play.clicked.connect(self._basculer_lecture)
        self.btn_suiv.clicked.connect(lambda: self._envoyer("next"))

        self.btn_play.setFixedSize(58, 44)   # le play/pause est plus grand
        rangee_transport = QHBoxLayout()
        rangee_transport.setContentsMargins(0, 0, 0, 0); rangee_transport.setSpacing(30)
        rangee_transport.addStretch(1)
        rangee_transport.addWidget(self.btn_prec, 0, Qt.AlignVCenter)
        rangee_transport.addWidget(self.btn_play, 0, Qt.AlignVCenter)
        rangee_transport.addWidget(self.btn_suiv, 0, Qt.AlignVCenter)
        rangee_transport.addStretch(1)

        # ===== Assemblage vertical (pas de barre de volume : réglage à la molette) =====
        racine = QVBoxLayout(self.contenu)
        racine.setContentsMargins(16, 12, 16, 12); racine.setSpacing(8)
        racine.addLayout(rangee_haut)
        racine.addLayout(bloc_timeline)
        racine.addLayout(rangee_transport)

    # ------------------------------------------------------------------ #
    #  Commandes
    # ------------------------------------------------------------------ #
    def _envoyer(self, action):
        if self._lecteur is not None:
            self._lecteur.commande(action)

    def _basculer_lecture(self):
        self._etat_courant = "paused" if self._etat_courant == "playing" else "playing"
        self._maj_bouton_play()
        actif = self._etat_courant == "playing"
        self.visualiseur.set_actif(actif); self.visualiseur_grand.set_actif(actif)
        self._envoyer("toggle")

    def _maj_bouton_play(self):
        self.btn_play.set_type("pause" if self._etat_courant == "playing" else "play")

    @staticmethod
    def _couleurs_pochette(data):
        """Extrait 2 couleurs de la pochette pour colorer le visualiseur (dégradé)."""
        img = QImage()
        img.loadFromData(data)
        if img.isNull():
            return QColor(235, 235, 235), QColor(175, 175, 175)
        img = img.scaled(10, 10, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        meilleur, meilleur_score = None, -1.0
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                score = c.saturationF() * (0.4 + 0.6 * c.valueF())  # favorise le vif
                if score > meilleur_score:
                    meilleur_score, meilleur = score, c
        if meilleur is None:
            return QColor(235, 235, 235), QColor(175, 175, 175)
        h, s, v, _ = meilleur.getHsvF()
        if s < 0.12 or h < 0:                      # pochette grise / N&B
            return QColor(235, 235, 235), QColor(170, 170, 170)
        # On rehausse pour que les barres restent bien visibles.
        vif = QColor()
        vif.setHsvF(h, min(max(s, 0.5), 0.95), max(v, 0.85), 1.0)
        return vif.lighter(120), vif

    # ------------------------------------------------------------------ #
    #  Infos + auto-masquage
    # ------------------------------------------------------------------ #
    def maj_infos(self, infos):
        if infos is None:
            self._musique_presente = False
            self.visualiseur.set_actif(False); self.visualiseur_grand.set_actif(False)
            self._maj_visibilite()
            return
        self._musique_presente = True
        self._maj_visibilite()
        self._texte_elide(self.lbl_titre, infos["titre"])
        self._texte_elide(self.lbl_artiste, infos["artiste"])
        self._etat_courant = infos["etat"]
        self._maj_bouton_play()
        actif = infos["etat"] == "playing"
        self.visualiseur.set_actif(actif); self.visualiseur_grand.set_actif(actif)
        if infos["pochette"]:
            self.lbl_pochette.setPixmap(self._pixmap_arrondie(infos["pochette"], COTE_POCHETTE, RAYON_POCHETTE))
            self.lbl_pochette_mini.setPixmap(self._pixmap_arrondie(infos["pochette"], COTE_POCHETTE_MINI, RAYON_POCHETTE_MINI))
            c1, c2 = self._couleurs_pochette(infos["pochette"])
        else:
            self.lbl_pochette.setPixmap(self._pochette_placeholder)
            self.lbl_pochette_mini.setPixmap(self._pochette_placeholder_mini)
            c1, c2 = QColor(235, 235, 235), QColor(175, 175, 175)
        # Le visualiseur prend les couleurs de la pochette (dégradé).
        self.visualiseur.set_couleurs(c1, c2)
        self.visualiseur_grand.set_couleurs(c1, c2)

    def _texte_elide(self, label, texte):
        m = label.fontMetrics()
        label.setText(m.elidedText(texte or "", Qt.ElideRight, self._largeur_texte))

    def set_actif(self, actif):
        """Active/désactive l'île depuis le menu de la zone de notification."""
        self._actif_utilisateur = actif
        self._maj_visibilite()

    def _maj_visibilite(self):
        """Visible seulement si : activée par l'utilisateur ET musique ET pas de plein écran."""
        doit_montrer = (
            self._actif_utilisateur
            and self._musique_presente
            and not self._plein_ecran
        )
        if doit_montrer and not self.isVisible():
            self._montrer_ile()
        elif not doit_montrer and self.isVisible():
            self._cacher_ile()

    def _verifier_plein_ecran(self):
        pe = plein_ecran_actif()
        if pe != self._plein_ecran:
            self._plein_ecran = pe
            self._maj_visibilite()

    def _montrer_ile(self):
        if self.isVisible():
            return
        self._etendu = False
        self.setGeometry(self._win_replie)
        self.contenu.hide(); self._op_contenu.setOpacity(0.0)
        self.mini.show(); self._op_mini.setOpacity(1.0)
        self.show(); self.raise_()

    def _cacher_ile(self):
        self._anim.stop(); self._etendu = False
        self.contenu.hide(); self.mini.hide(); self.hide()

    # ------------------------------------------------------------------ #
    #  Timeline
    # ------------------------------------------------------------------ #
    def maj_progression(self, position_s, duree_s, en_lecture):
        self._duree = duree_s
        if self.slider_progress.is_drag():
            return
        self._pos_base = position_s
        self._en_lecture = en_lecture
        self._t_base = time.monotonic()
        self._rafraichir_timeline()

    def _rafraichir_timeline(self):
        if not self._etendu or self.slider_progress.is_drag():
            return
        if self._duree <= 0:
            self.slider_progress.set_fraction(0.0)
            self.lbl_temps.setText("0:00"); self.lbl_duree.setText("-0:00")
            return
        ecoule = (time.monotonic() - self._t_base) if self._en_lecture else 0.0
        position = max(0.0, min(self._pos_base + ecoule, self._duree))
        self.slider_progress.set_fraction(position / self._duree)
        self.lbl_temps.setText(formater_temps(position))
        self.lbl_duree.setText("-" + formater_temps(self._duree - position))

    def _preview_progress(self, frac):
        if self._duree > 0:
            position = frac * self._duree
            self.lbl_temps.setText(formater_temps(position))
            self.lbl_duree.setText("-" + formater_temps(self._duree - position))

    def _seek(self, frac):
        if self._duree > 0 and self._lecteur is not None:
            self._lecteur.chercher(frac * self._duree)

    # ------------------------------------------------------------------ #
    #  Volume (à la molette, au-dessus de l'île)
    # ------------------------------------------------------------------ #
    def wheelEvent(self, event):
        v = self._volume.lire()
        if v is None:
            return
        pas = 3 if event.angleDelta().y() > 0 else -3
        self._volume.regler(max(0, min(100, v + pas)))
        event.accept()

    # ------------------------------------------------------------------ #
    #  Images
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pixmap_arrondie(data, cote, rayon):
        source = QPixmap(); source.loadFromData(data)
        source = source.scaled(cote, cote, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        resultat = QPixmap(cote, cote); resultat.fill(Qt.transparent)
        p = QPainter(resultat)
        p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform)
        chemin = QPainterPath(); chemin.addRoundedRect(0, 0, cote, cote, rayon, rayon)
        p.setClipPath(chemin)
        p.drawPixmap(int((cote - source.width()) / 2), int((cote - source.height()) / 2), source)
        p.end()
        return resultat

    @staticmethod
    def _faire_placeholder(cote, rayon):
        pm = QPixmap(cote, cote); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        chemin = QPainterPath(); chemin.addRoundedRect(0, 0, cote, cote, rayon, rayon)
        p.fillPath(chemin, QColor(30, 30, 30, 255))
        p.setPen(QColor(150, 150, 150))
        f = QFont(); f.setPointSize(max(6, int(cote * 0.38))); p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, "♪")
        p.end()
        return pm

    # ------------------------------------------------------------------ #
    #  Placement / survol / animation
    # ------------------------------------------------------------------ #
    def _rect_centre(self, largeur, hauteur):
        geo = QGuiApplication.primaryScreen().geometry()
        return QRect(geo.x() + (geo.width() - largeur) // 2, geo.y(), largeur, hauteur)

    def _rester_au_dessus(self):
        if self.isVisible():
            self.raise_()

    def _verifier_souris(self):
        if not self.isVisible():
            return
        souris = QCursor.pos()
        if not self._etendu:
            if self._zone_declenchement.contains(souris):
                self._ouvrir()
        else:
            zone = self._panneau_etendu.adjusted(-MARGE_SURVOL, 0, MARGE_SURVOL, MARGE_SURVOL)
            if not zone.contains(souris) and not self._un_slider_actif():
                self._fermer()

    def _un_slider_actif(self):
        return self.slider_progress.is_drag()

    def _ouvrir(self):
        self._etendu = True
        self._fondu_mini.stop(); self._op_mini.setOpacity(0.0); self.mini.hide()
        self._animer_vers(self._win_etendu)

    def _fermer(self):
        self._etendu = False
        self._fondu_contenu.stop(); self._op_contenu.setOpacity(0.0); self.contenu.hide()
        self._animer_vers(self._win_replie)

    def _animer_vers(self, rect_cible):
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(rect_cible)
        self._anim.start()

    def _quand_anim_finie(self):
        if self._etendu:
            self.contenu.show()
            self._fondu_contenu.stop()
            self._fondu_contenu.setStartValue(0.0); self._fondu_contenu.setEndValue(1.0)
            self._fondu_contenu.start()
        elif self._musique_presente:
            self.mini.show()
            self._fondu_mini.stop()
            self._fondu_mini.setStartValue(0.0); self._fondu_mini.setEndValue(1.0)
            self._fondu_mini.start()

    # ------------------------------------------------------------------ #
    #  Fond : OMBRE PORTÉE + panneau noir
    # ------------------------------------------------------------------ #
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        pw = self.width() - 2 * MARGE_X
        ph = self.height() - MARGE_BAS
        L = float(MARGE_X)
        R = float(MARGE_X + pw)
        B = float(ph)
        rb = min(RAYON_MAX, ph / 2)     # arrondi des coins du bas (convexe)
        cc = float(MARGE_X)             # arrondi des coins du haut (concave)

        chemin = self._forme_ile(L, R, B, rb, cc)
        # L'ombre suit UNIQUEMENT les côtés + le bas (pas le haut) : ses extrémités
        # partent au-dessus de l'écran, donc aucun débordement sur les coins hauts.
        ombre = self._forme_ombre(L, R, B, rb)
        self._dessiner_ombre(p, ombre)

        # Panneau noir + fin liseré (effet verre).
        p.setBrush(COULEUR_FOND)
        p.setPen(QPen(COULEUR_BORDURE, 1))
        p.drawPath(chemin)

    @staticmethod
    def _dessiner_ombre(p, chemin):
        p.setBrush(Qt.NoBrush)
        couches = 12
        for i in range(couches):
            largeur = (couches - i) * 2.0   # extérieur (large, faible) -> bord
            stylo = QPen(QColor(0, 0, 0, 8), largeur)
            stylo.setJoinStyle(Qt.RoundJoin)
            stylo.setCapStyle(Qt.FlatCap)
            p.setPen(stylo)
            p.drawPath(chemin)

    @staticmethod
    def _forme_ombre(L, R, B, rb):
        """Contour OUVERT : côté gauche + bas + côté droit (sans le haut).
        Les extrémités montent au-dessus de l'écran (y négatif) pour que les
        bouts du trait soient hors écran -> pas d'ombre sur les coins du haut."""
        rb = min(rb, (R - L) / 2, B)
        c = QPainterPath()
        c.moveTo(L, -40)
        c.lineTo(L, B - rb)
        c.quadTo(L, B, L + rb, B)
        c.lineTo(R - rb, B)
        c.quadTo(R, B, R, B - rb)
        c.lineTo(R, -40)
        return c

    @staticmethod
    def _forme_ile(L, R, B, rb, cc):
        """
        Forme de l'île : coins du BAS arrondis (convexes) et coins du HAUT
        CONCAVES (l'île semble naître du bord supérieur de l'écran, façon Mac).
        """
        rb = min(rb, (R - L) / 2, B)
        cc = min(cc, (R - L) / 2)
        c = QPainterPath()
        c.moveTo(L - cc, 0)               # départ tout en haut à gauche (élargi)
        c.quadTo(L, 0, L, cc)             # coin haut-gauche CONCAVE
        c.lineTo(L, B - rb)               # côté gauche
        c.quadTo(L, B, L + rb, B)         # coin bas-gauche arrondi
        c.lineTo(R - rb, B)               # bas
        c.quadTo(R, B, R, B - rb)         # coin bas-droit arrondi
        c.lineTo(R, cc)                   # côté droit
        c.quadTo(R, 0, R + cc, 0)         # coin haut-droit CONCAVE
        c.closeSubpath()                  # bord du haut (retour à gauche)
        return c

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            QApplication.quit()


# ====================================================================== #
#  Icône de la zone de notification (bas à droite)
# ====================================================================== #
def _faire_icone_tray() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    # petite "pilule" blanche (l'île)
    p.setBrush(QColor(255, 255, 255))
    p.drawRoundedRect(QRectF(6, 23, 52, 18), 9, 9)
    # 3 barres sombres (mini visualiseur)
    p.setBrush(QColor(20, 20, 20))
    for i, hh in enumerate((7, 12, 8)):
        x = 24 + i * 6
        p.drawRoundedRect(QRectF(x, 32 - hh / 2, 3, hh), 1.5, 1.5)
    p.end()
    return QIcon(pm)


# ====================================================================== #
#  Programme principal
# ====================================================================== #
def _securiser_sorties():
    """Évite les plantages de print() : UTF-8, et sortie "muette" si l'exe est
    en mode fenêtré (sans console, sys.stdout/err valent None)."""
    class _Muet:
        def write(self, *a):
            pass
        def flush(self):
            pass
    if sys.stdout is None:
        sys.stdout = _Muet()
    if sys.stderr is None:
        sys.stderr = _Muet()
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _securiser_sorties()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    # L'appli vit dans la zone de notification : elle ne se ferme pas quand
    # l'île se cache (musique arrêtée, plein écran, ou désactivée).
    app.setQuitOnLastWindowClosed(False)

    volume = VolumeSysteme()
    fenetre = Island(volume)

    lecteur = LecteurMedia()
    lecteur.infos.connect(fenetre.maj_infos)
    lecteur.progression.connect(fenetre.maj_progression)
    fenetre.definir_lecteur(lecteur)
    lecteur.start()

    # --- Icône + menu dans la zone de notification (bas à droite) ---
    tray = QSystemTrayIcon(_faire_icone_tray(), app)
    tray.setToolTip("Music Island")

    menu = QMenu()
    act_actif = QAction("Île active", menu)
    act_actif.setCheckable(True)
    act_actif.setChecked(True)
    act_actif.toggled.connect(fenetre.set_actif)
    menu.addAction(act_actif)

    act_demarrage = QAction("Démarrer avec Windows", menu)
    act_demarrage.setCheckable(True)
    act_demarrage.setChecked(demarrage_auto_actif())
    act_demarrage.toggled.connect(activer_demarrage_auto)
    menu.addAction(act_demarrage)

    menu.addSeparator()
    act_quit = QAction("Quitter", menu)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_quit)
    tray.setContextMenu(menu)

    # Clic gauche sur l'icône = activer/désactiver rapidement.
    def _clic_tray(raison):
        if raison == QSystemTrayIcon.Trigger:
            act_actif.toggle()
    tray.activated.connect(_clic_tray)
    tray.show()

    app.aboutToQuit.connect(lecteur.arreter)
    signal.signal(signal.SIGINT, lambda *a: app.quit())
    reveil = QTimer(); reveil.start(200); reveil.timeout.connect(lambda: None)

    print("Music Island lancée (icône en bas à droite).")
    print("👉 Clic droit sur l'icône : activer/désactiver ou quitter.")
    print("Ferme avec Ctrl + C (ici) ou via l'icône.")

    code = app.exec()
    lecteur.arreter(); lecteur.wait(1500)
    sys.exit(code)


if __name__ == "__main__":
    main()
