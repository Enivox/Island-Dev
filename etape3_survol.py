# -*- coding: utf-8 -*-
"""
ÉTAPE 3 — Extension / rétraction animée au survol
=================================================

Principe :
  - État REPLIÉ : petite encoche fine et discrète, collée en haut au centre.
  - Quand la souris entre dans une "zone de déclenchement" en haut au centre
    (~350 px de large, ~8 px de haut, collée au bord supérieur), l'encoche
    s'AGRANDIT en douceur en un grand panneau (animation de taille).
  - Quand la souris quitte la zone (et n'est plus au-dessus du panneau), il se
    RÉTRACTE.

Le panneau est encore VIDE ici (fond sombre arrondi). Les vraies infos musique
(titre, artiste, pochette) et les contrôles viendront aux étapes 4, 5, 6.

Technique :
  - Un QTimer (~50 ms) lit la position GLOBALE du curseur (QCursor.pos()).
  - L'animation de la fenêtre se fait avec QPropertyAnimation sur sa "geometry"
    (position + taille) -> transition fluide.

Pour lancer :
    python etape3_survol.py

Pour quitter : Ctrl + C dans la console (ou la touche Échap).
"""

import sys
import signal

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import (
    Qt, QTimer, QRect, QRectF, QPoint,
    QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import QPainter, QColor, QGuiApplication, QPainterPath, QCursor


# --- État REPLIÉ (petit trait discret) ---
LARGEUR_REPLIE = 150
HAUTEUR_REPLIE = 8

# --- État ÉTENDU (grand panneau) ---
LARGEUR_ETENDU = 380
HAUTEUR_ETENDU = 110

# --- Zone de déclenchement (là où il faut amener la souris pour ouvrir) ---
# Un rectangle centré en haut : large et fin, collé au bord supérieur.
TRIGGER_LARGEUR = 350
TRIGGER_HAUTEUR = 8

# Marge autour du panneau étendu pour "rester ouvert" sans clignoter quand la
# souris longe les bords.
MARGE_SURVOL = 8

# Apparence
RAYON_MAX = 22                       # arrondi max des coins du bas (état étendu)
COULEUR_FOND = QColor(12, 12, 12, 245)

# Animation
DUREE_ANIM = 280                     # durée de l'animation en millisecondes


class Island(QWidget):
    """La Dynamic Island : encoche repliée qui s'étend au survol."""

    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        # On calcule une fois pour toutes les deux géométries (replié / étendu),
        # centrées en haut de l'écran principal.
        self._rect_replie = self._calculer_rect(LARGEUR_REPLIE, HAUTEUR_REPLIE)
        self._rect_etendu = self._calculer_rect(LARGEUR_ETENDU, HAUTEUR_ETENDU)

        # Zone de déclenchement (en coordonnées globales de l'écran).
        self._zone_declenchement = self._calculer_rect(
            TRIGGER_LARGEUR, TRIGGER_HAUTEUR
        )

        # État courant : au départ, replié.
        self._etendu = False
        self.setGeometry(self._rect_replie)

        # --- Animation de la géométrie (taille + position) ---
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(DUREE_ANIM)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)  # démarre vite, ralentit

        # --- Timer qui surveille la position de la souris (~50 ms) ---
        self._timer_souris = QTimer(self)
        self._timer_souris.timeout.connect(self._verifier_souris)
        self._timer_souris.start(50)

        # --- Timer pour rester au-dessus des autres fenêtres ---
        self._timer_premier_plan = QTimer(self)
        self._timer_premier_plan.timeout.connect(self.raise_)
        self._timer_premier_plan.start(500)

    # ------------------------------------------------------------------ #
    #  Calcul des positions
    # ------------------------------------------------------------------ #
    def _calculer_rect(self, largeur, hauteur) -> QRect:
        """Rectangle centré horizontalement et collé en haut de l'écran principal."""
        geo = QGuiApplication.primaryScreen().geometry()
        x = geo.x() + (geo.width() - largeur) // 2
        y = geo.y()
        return QRect(x, y, largeur, hauteur)

    # ------------------------------------------------------------------ #
    #  Logique de survol
    # ------------------------------------------------------------------ #
    def _verifier_souris(self):
        """Appelé ~20 fois/seconde : décide d'ouvrir ou de fermer selon la souris."""
        souris = QCursor.pos()  # position globale du curseur (coordonnées écran)

        if not self._etendu:
            # Replié -> on ouvre si la souris entre dans la zone de déclenchement.
            if self._zone_declenchement.contains(souris):
                self._ouvrir()
        else:
            # Étendu -> on ferme si la souris sort de la zone du panneau (+ marge).
            zone_ouverte = self._rect_etendu.adjusted(
                -MARGE_SURVOL, 0, MARGE_SURVOL, MARGE_SURVOL
            )
            if not zone_ouverte.contains(souris):
                self._fermer()

    def _ouvrir(self):
        """Lance l'animation vers l'état étendu."""
        self._etendu = True
        self._animer_vers(self._rect_etendu)

    def _fermer(self):
        """Lance l'animation vers l'état replié."""
        self._etendu = False
        self._animer_vers(self._rect_replie)

    def _animer_vers(self, rect_cible: QRect):
        """Anime la géométrie de la fenêtre depuis sa position actuelle vers rect_cible."""
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(rect_cible)
        self._anim.start()

    # ------------------------------------------------------------------ #
    #  Dessin
    # ------------------------------------------------------------------ #
    def paintEvent(self, event):
        """Dessine le panneau : fond sombre, coins du bas arrondis (façon encoche)."""
        peintre = QPainter(self)
        peintre.setRenderHint(QPainter.Antialiasing)

        zone = QRectF(0, 0, self.width(), self.height())
        # Le rayon s'adapte à la hauteur : petit quand replié, grand quand étendu.
        rayon = min(RAYON_MAX, self.height() / 2)

        chemin = self._forme_encoche(zone, rayon)
        peintre.setPen(Qt.NoPen)
        peintre.fillPath(chemin, COULEUR_FOND)

    @staticmethod
    def _forme_encoche(zone: QRectF, rayon: float) -> QPainterPath:
        """Rectangle avec seulement les deux coins du BAS arrondis."""
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


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)

    fenetre = Island()
    fenetre.show()

    signal.signal(signal.SIGINT, lambda *args: app.quit())
    reveil = QTimer()
    reveil.start(200)
    reveil.timeout.connect(lambda: None)

    print("Dynamic Island lancée.")
    print("👉 Monte ta souris tout en haut au centre de l'écran : ça s'agrandit.")
    print("   Éloigne la souris : ça se rétracte.")
    print("Ferme avec Ctrl + C (ici) ou la touche Échap.")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
