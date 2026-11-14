"""
Ligne de commande : cv.json  ->  sortie/cv_<modele>.pdf

Lancer :  python generer.py [classique|europeen|africain] [fr|en|it]
Toute la logique est dans moteur.py (partagée avec le site web, app.py).
"""
import json
import sys
from pathlib import Path

import moteur

RACINE = Path(__file__).parent


def main():
    modele = sys.argv[1] if len(sys.argv) > 1 else "classique"
    langue = sys.argv[2] if len(sys.argv) > 2 else "fr"
    cv = json.loads((RACINE / "cv.json").read_text(encoding="utf-8"))
    try:
        pdf = moteur.generer_cv(cv, {"modele": modele, "langue": langue})
    except (moteur.ErreurDonnees, moteur.ErreurCompilation) as e:
        sys.exit(str(e))
    sortie = RACINE / "sortie" / f"cv_{modele}.pdf"
    sortie.parent.mkdir(exist_ok=True)
    sortie.write_bytes(pdf)
    print(f"OK -> {sortie}")


if __name__ == "__main__":
    main()
