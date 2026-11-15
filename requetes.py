"""
Lecture sûre du JSON envoyé par les visiteurs.

json.loads est récursif : un JSON imbriqué sur des dizaines de milliers de niveaux
(« [[[[… ») fait planter le décodage (RecursionError, donc erreur 500). On mesure donc
d'abord la profondeur, sans récursion, et on refuse au-delà d'une limite très large
pour un CV (les vraies données ne dépassent pas 4 ou 5 niveaux).
"""
import json

from flask import request

PROFONDEUR_MAX = 20


def profondeur(texte):
    """Profondeur maximale d'imbrication [ ] { } d'un texte JSON, chaînes ignorées."""
    niveau = maximum = 0
    dans_chaine = echappe = False
    for c in texte:
        if dans_chaine:
            if echappe:
                echappe = False
            elif c == "\\":
                echappe = True
            elif c == '"':
                dans_chaine = False
        elif c == '"':
            dans_chaine = True
        elif c in "[{":
            niveau += 1
            if niveau > maximum:
                maximum = niveau
                if maximum > PROFONDEUR_MAX:
                    return maximum              # inutile d'aller plus loin
        elif c in "]}":
            niveau -= 1
    return maximum


def lire_json():
    """Le corps JSON de la requête, ou None s'il est absent, invalide ou trop imbriqué."""
    try:
        texte = request.get_data(as_text=True)
    except UnicodeDecodeError:
        return None
    if not texte or profondeur(texte) > PROFONDEUR_MAX:
        return None
    try:
        return json.loads(texte)
    except (ValueError, RecursionError):
        return None


def trop_imbrique(valeur):
    """Vrai si une structure Python (dict / list) dépasse PROFONDEUR_MAX niveaux."""
    pile = [(valeur, 1)]
    while pile:
        element, niveau = pile.pop()
        if isinstance(element, dict):
            element = element.values()
        elif not isinstance(element, list):
            continue
        if niveau > PROFONDEUR_MAX:
            return True
        pile.extend((enfant, niveau + 1) for enfant in element)
    return False
