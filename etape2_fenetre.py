# -*- coding: utf-8 -*-
"""
ÉTAPE 2 — La fenêtre flottante (état "replié", discret)
=======================================================

But : afficher une petite fenêtre :
  - sans bordure (frameless),
  - au fond transparent (on ne voit que notre pastille),
  - toujours au-dessus des autres fenêtres,
  - collée en haut au centre de l'écran principal,
  - SANS icône dans la barre des tâches.

Ici on dessine l'état "REPLIÉ" : un petit trait sombre et discret, façon
encoche (les coins du bas sont arrondis). À l'étape 3, il s'agrandira au survol
de la souris ; pour l'instant il reste replié, on valide juste le look discret.

Pour lancer :
    python etape2_fenetre.py

Pour quitter : Ctrl + C dans la console (ou la touche Échap).
"""

import sys
import signal

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QColor, QGuiApplication, QPainterPath


# --- Dimensions de l'état REPLIÉ (en pixels "logiques", Qt gère le DPI) ---
LARGEUR = 150      # largeur du petit trait
HAUTEUR = 8        # hauteur (fin et discret)
RAYON = 5          # arrondi des coins du bas

# Couleur de la pastille : presque noir (façon encoche).
COULEUR_FOND = QColor(12, 12, 12, 255)


class Pastille(QWidget):
    """La fenêtre : une petite encoche sombre, collée en haut au centre."""

    def __init__(self):
        super().__init__()

        # --- Réglages de la fenêtre ---
        # FramelessWindowHint : pas de bordure ni de barre de titre.
        # WindowStaysOnTopHint : toujours au-dessus des autres fenêtres.
        # Tool : évite l'icône dans la barre des tâches (comportement Windows).
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )

        # Fond de fenêtre transparent : on ne verra que ce qu'on dessine.
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Taille de l'état replié.
        self.resize(LARGEUR, HAUTEUR)

        # Placement en haut au centre de l'écran principal.
        self._placer_en_haut_au_centre()

        # --- Rester au-dessus même si une autre appli reprend le focus ---
        # Un timer ré-affirme régulièrement que notre fenêtre est au premier plan.
        self._timer_premier_plan = QTimer(self)
        self._timer_premier_plan.timeout.connect(self.raise_)
        self._timer_premier_plan.start(500)  # toutes les 500 ms

    def _placer_en_haut_au_centre(self):
        """
        Place la fenêtre collée en haut, centrée horizontalement, sur l'écran
        PRINCIPAL. On travaille en coordonnées "logiques" : Qt applique tout seul
        la mise à l'échelle (DPI), donc rien de spécial à calculer ici.
        """
        ecran = QGuiApplication.primaryScreen()
        geo = ecran.geometry()  # zone totale de l'écran principal

        x = geo.x() + (geo.width() - self.width()) // 2  # centré horizontalement
        y = geo.y()                                      # collé tout en haut

        self.move(x, y)

    def paintEvent(self, event):
        """
        Dessine la pastille : un rectangle dont seuls les coins du BAS sont
        arrondis (les coins du haut restent droits, collés au bord de l'écran),
        pour un effet "encoche" propre.
        """
        peintre = QPainter(self)
        peintre.setRenderHint(QPainter.Antialiasing)

        chemin = self._forme_encoche(
            QRectF(0, 0, self.width(), self.height()), RAYON
        )

        peintre.setPen(Qt.NoPen)          # pas de contour
        peintre.fillPath(chemin, COULEUR_FOND)

    @staticmethod
    def _forme_encoche(zone: QRectF, rayon: float) -> QPainterPath:
        """
        Construit un tracé rectangulaire avec SEULEMENT les deux coins du bas
        arrondis. (Les coins du haut sont droits car collés au bord de l'écran.)
        """
        x, y, w, h = zone.x(), zone.y(), zone.width(), zone.height()
        r = min(rayon, h, w / 2)  # sécurité : le rayon ne dépasse pas la taille

        chemin = QPainterPath()
        chemin.moveTo(x, y)                       # coin haut-gauche (droit)
        chemin.lineTo(x + w, y)                   # bord du haut -> coin haut-droit
        chemin.lineTo(x + w, y + h - r)           # descend le côté droit
        # coin bas-droit arrondi :
        chemin.quadTo(x + w, y + h, x + w - r, y + h)
        chemin.lineTo(x + r, y + h)               # bord du bas
        # coin bas-gauche arrondi :
        chemin.quadTo(x, y + h, x, y + h - r)
        chemin.closeSubpath()                     # remonte et ferme
        return chemin

    def keyPressEvent(self, event):
        """Permet de quitter avec la touche Échap (pratique pendant les tests)."""
        if event.key() == Qt.Key_Escape:
            QApplication.quit()


def main():
    # --- Gestion du DPI (écrans à 125 %, 150 %, etc.) ---
    # PassThrough = on autorise les facteurs d'échelle fractionnaires (ex : 1,5).
    # À régler AVANT de créer QApplication.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)

    fenetre = Pastille()
    fenetre.show()

    # --- Astuce pour que Ctrl + C (dans la console) ferme bien l'appli ---
    signal.signal(signal.SIGINT, lambda *args: app.quit())
    reveil = QTimer()
    reveil.start(200)
    reveil.timeout.connect(lambda: None)

    print("Encoche discrète affichée en haut au centre.")
    print("(Elle est fine et sombre : regarde bien le milieu du bord supérieur.)")
    print("Ferme avec Ctrl + C (ici) ou la touche Échap.")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
