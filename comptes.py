"""
Comptes utilisateurs (facultatifs) : inscription, connexion, et sauvegarde du CV sur le
serveur pour le retrouver depuis un autre appareil.

Règles de sécurité appliquées ici :
  - le mot de passe n'est jamais enregistré : seulement son empreinte (scrypt, salée) ;
  - chaque requête sur les données passe par l'identifiant de la session : un compte
    ne peut ni lire ni modifier les données d'un autre ;
  - les échecs de connexion sont limités (par adresse IP et par adresse e-mail) ;
  - le cookie de session est signé, invisible pour JavaScript (HttpOnly) et n'est pas
    envoyé par les autres sites (SameSite) ;
  - les requêtes qui modifient quelque chose doivent venir de notre propre site.

Stockage : un fichier SQLite (dossier donnees/, à sauvegarder et à garder hors de Git).
"""
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

RACINE = Path(__file__).parent
DOSSIER = Path(os.environ.get("DOSSIER_DONNEES") or RACINE / "donnees")
BASE = DOSSIER / "comptes.sqlite3"

MDP_MIN, MDP_MAX = 8, 200
DONNEES_MAX = 2_500_000          # taille maximale du CV enregistré (photo comprise), en octets
SESSION_JOURS = 30
_EMAIL_OK = re.compile(r"^[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")

comptes = Blueprint("comptes", __name__, url_prefix="/api/compte")


# --------------------------------------------------------------------------
# Base de données
# --------------------------------------------------------------------------
def _base():
    """Une connexion par requête : simple et sûr avec plusieurs fils d'exécution."""
    connexion = sqlite3.connect(BASE, timeout=10)
    connexion.execute("PRAGMA foreign_keys = ON")
    return connexion


def initialiser(app):
    """À appeler une fois au démarrage : crée le dossier, la base et la clé de session."""
    DOSSIER.mkdir(parents=True, exist_ok=True)
    os.chmod(DOSSIER, 0o700)          # lisible uniquement par l'utilisateur du serveur
    with _base() as b:
        b.execute("PRAGMA journal_mode = WAL")
        b.execute("""CREATE TABLE IF NOT EXISTS comptes (
            id        INTEGER PRIMARY KEY,
            email     TEXT NOT NULL UNIQUE,
            empreinte TEXT NOT NULL,
            jeton     TEXT NOT NULL,           -- change à chaque suppression/changement : invalide les sessions
            cree_le   INTEGER NOT NULL,
            vu_le     INTEGER NOT NULL)""")
        b.execute("""CREATE TABLE IF NOT EXISTS donnees (
            compte_id  INTEGER PRIMARY KEY REFERENCES comptes(id) ON DELETE CASCADE,
            contenu    TEXT NOT NULL,
            modifie_le INTEGER NOT NULL)""")
    os.chmod(BASE, 0o600)

    # Clé qui signe les cookies de session. En production : variable CLE_SECRETE.
    # Sinon on en fabrique une, gardée dans donnees/ pour survivre aux redémarrages.
    cle = os.environ.get("CLE_SECRETE", "")
    if len(cle) < 32:
        fichier = DOSSIER / "cle_secrete"
        if not fichier.exists():
            fichier.touch(mode=0o600)
            fichier.write_text(secrets.token_hex(32))
        cle = fichier.read_text().strip()
    app.secret_key = cle
    app.config.update(
        SESSION_COOKIE_NAME="session_cv",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Cookie envoyé seulement en HTTPS, sauf en développement sur localhost.
        SESSION_COOKIE_SECURE=os.environ.get("DERRIERE_PROXY") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_JOURS),
    )
    app.register_blueprint(comptes)


# --------------------------------------------------------------------------
# Protections
# --------------------------------------------------------------------------
# Mots de passe essayés au hasard : on compte les ÉCHECS sur un quart d'heure, à la fois
# par adresse e-mail visée (8 au plus) et par adresse IP (20 au plus : derrière une même
# adresse, cybercafé ou école, il y a plusieurs personnes). Les réussites ne comptent pas.
# Les créations de compte sont limitées de la même façon (10 par adresse IP).
FENETRE_S = 15 * 60
LIMITES = {"email": 8, "ip": 20, "inscription": 10}
_echecs = defaultdict(deque)
_verrou = threading.Lock()


def _bloque(*cles):
    """Vrai si l'une des clés (type, valeur) a atteint sa limite d'échecs récents."""
    maintenant = time.monotonic()
    with _verrou:
        for cle in cles:
            file = _echecs.get(cle)
            while file and maintenant - file[0] > FENETRE_S:
                file.popleft()
            if file and len(file) >= LIMITES[cle[0]]:
                return True
    return False


def _noter(*cles):
    """Enregistre un échec pour chacune des clés."""
    maintenant = time.monotonic()
    with _verrou:
        for cle in cles:
            _echecs[cle].append(maintenant)
        if len(_echecs) > 20_000:      # ménage
            for cle in [c for c, f in _echecs.items() if not f or maintenant - f[-1] > FENETRE_S]:
                del _echecs[cle]


def _oublier(*cles):
    with _verrou:
        for cle in cles:
            _echecs.pop(cle, None)


@comptes.before_request
def _meme_site():
    """Refuse les requêtes de modification envoyées depuis un autre site (CSRF) :
    elles doivent être en JSON et, si le navigateur indique leur origine, venir d'ici."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    origine = request.headers.get("Origin")
    if origine and urlsplit(origine).netloc != request.host:
        return jsonify(erreur="Requête refusée."), 403
    if not request.is_json:
        return jsonify(erreur="Requête invalide."), 400
    return None


# Empreinte bidon : quand l'adresse est inconnue, on vérifie quand même un mot de passe,
# pour que la réponse ne soit pas plus rapide (ce qui révélerait quels comptes existent).
_EMPREINTE_BIDON = generate_password_hash(secrets.token_hex(16))


def _corps():
    corps = request.get_json(silent=True)
    return corps if isinstance(corps, dict) else {}


def _identifiants():
    """(email normalisé, mot de passe) lus dans la requête ; chaînes vides si absents."""
    corps = _corps()
    email, mdp = corps.get("email"), corps.get("mot_de_passe")
    email = email.strip().lower() if isinstance(email, str) else ""
    return email[:120], mdp if isinstance(mdp, str) else ""


def compte_connecte():
    """(id, email) du compte de la session en cours, ou None.
    La session contient un jeton comparé à celui de la base : supprimer le compte
    déconnecte donc aussitôt tous les appareils."""
    cid, jeton = session.get("compte"), session.get("jeton")
    if not isinstance(cid, int) or not isinstance(jeton, str):
        return None
    with _base() as b:
        ligne = b.execute("SELECT id, email, jeton FROM comptes WHERE id = ?", (cid,)).fetchone()
    if not ligne or not secrets.compare_digest(ligne[2], jeton):
        session.clear()
        return None
    return ligne[0], ligne[1]


def _ouvrir_session(cid, jeton):
    session.clear()                 # nouvelle session : pas de reprise d'un ancien cookie
    session.permanent = True
    session["compte"], session["jeton"] = cid, jeton


def _non_connecte():
    return jsonify(erreur="Connecte-toi pour continuer."), 401


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@comptes.get("")
def etat():
    compte = compte_connecte()
    if not compte:
        return jsonify(connecte=False, email=None)
    # Pour la page « Mon espace » : dates du compte et de la dernière sauvegarde du CV.
    with _base() as b:
        cree_le, vu_le = b.execute("SELECT cree_le, vu_le FROM comptes WHERE id = ?", (compte[0],)).fetchone()
        sauvegarde = b.execute("SELECT modifie_le FROM donnees WHERE compte_id = ?", (compte[0],)).fetchone()
    return jsonify(connecte=True, email=compte[1], cree_le=cree_le, vu_le=vu_le,
                   modifie_le=sauvegarde[0] if sauvegarde else None)


@comptes.post("/inscription")
def inscription():
    email, mdp = _identifiants()
    limite = ("inscription", request.remote_addr)
    if _bloque(limite):
        return jsonify(erreur="Trop de comptes créés depuis cette connexion. Réessaie dans un quart d'heure."), 429
    if not _EMAIL_OK.match(email):
        return jsonify(erreur="Adresse e-mail invalide."), 400
    if not MDP_MIN <= len(mdp) <= MDP_MAX:
        return jsonify(erreur=f"Le mot de passe doit contenir au moins {MDP_MIN} caractères."), 400
    if mdp.lower() in (email, email.split("@")[0]):
        return jsonify(erreur="Choisis un mot de passe différent de ton adresse e-mail."), 400
    jeton, maintenant = secrets.token_hex(16), int(time.time())
    try:
        with _base() as b:
            curseur = b.execute(
                "INSERT INTO comptes (email, empreinte, jeton, cree_le, vu_le) VALUES (?, ?, ?, ?, ?)",
                (email, generate_password_hash(mdp), jeton, maintenant, maintenant))
    except sqlite3.IntegrityError:
        _noter(limite)
        return jsonify(erreur="Un compte existe déjà avec cette adresse. Connecte-toi."), 409
    _noter(limite)
    _ouvrir_session(curseur.lastrowid, jeton)
    return jsonify(connecte=True, email=email), 201


@comptes.post("/connexion")
def connexion():
    email, mdp = _identifiants()
    cles = (("ip", request.remote_addr), ("email", email))
    if _bloque(*cles):
        return jsonify(erreur="Trop de tentatives. Réessaie dans un quart d'heure."), 429
    with _base() as b:
        ligne = b.execute("SELECT id, empreinte, jeton FROM comptes WHERE email = ?", (email,)).fetchone()
    correct = check_password_hash(ligne[1] if ligne else _EMPREINTE_BIDON, mdp[:MDP_MAX])
    if not ligne or not correct:
        # Même message dans les deux cas : on ne dit pas si l'adresse a un compte.
        _noter(*cles)
        return jsonify(erreur="Adresse e-mail ou mot de passe incorrect."), 401
    _oublier(("email", email))
    with _base() as b:
        b.execute("UPDATE comptes SET vu_le = ? WHERE id = ?", (int(time.time()), ligne[0]))
    _ouvrir_session(ligne[0], ligne[2])
    return jsonify(connecte=True, email=email)


@comptes.post("/mot-de-passe")
def changer_mot_de_passe():
    """Change le mot de passe (l'ancien est redemandé) et déconnecte les autres appareils."""
    compte = compte_connecte()
    if not compte:
        return _non_connecte()
    cles = (("ip", request.remote_addr), ("email", compte[1]))
    if _bloque(*cles):
        return jsonify(erreur="Trop de tentatives. Réessaie dans un quart d'heure."), 429
    corps = _corps()
    ancien, nouveau = corps.get("ancien"), corps.get("nouveau")
    with _base() as b:
        ligne = b.execute("SELECT empreinte FROM comptes WHERE id = ?", (compte[0],)).fetchone()
    if not ligne or not isinstance(ancien, str) or not check_password_hash(ligne[0], ancien[:MDP_MAX]):
        _noter(*cles)
        return jsonify(erreur="Mot de passe actuel incorrect."), 401
    if not isinstance(nouveau, str) or not MDP_MIN <= len(nouveau) <= MDP_MAX:
        return jsonify(erreur=f"Le nouveau mot de passe doit contenir au moins {MDP_MIN} caractères."), 400
    if nouveau.lower() in (compte[1], compte[1].split("@")[0]):
        return jsonify(erreur="Choisis un mot de passe différent de ton adresse e-mail."), 400
    if nouveau == ancien:
        return jsonify(erreur="Le nouveau mot de passe doit être différent de l'ancien."), 400
    # Nouveau jeton : les sessions ouvertes sur d'autres appareils cessent d'être valables.
    jeton = secrets.token_hex(16)
    with _base() as b:
        b.execute("UPDATE comptes SET empreinte = ?, jeton = ? WHERE id = ?",
                  (generate_password_hash(nouveau), jeton, compte[0]))
    _ouvrir_session(compte[0], jeton)       # cet appareil-ci reste connecté
    return jsonify(connecte=True)


@comptes.post("/deconnexion")
def deconnexion():
    session.clear()
    return jsonify(connecte=False)


@comptes.get("/donnees")
def lire_donnees():
    compte = compte_connecte()
    if not compte:
        return _non_connecte()
    with _base() as b:
        ligne = b.execute("SELECT contenu, modifie_le FROM donnees WHERE compte_id = ?", (compte[0],)).fetchone()
    if not ligne:
        return jsonify(donnees=None, modifie_le=None)
    return jsonify(donnees=json.loads(ligne[0]), modifie_le=ligne[1])


@comptes.put("/donnees")
def ecrire_donnees():
    compte = compte_connecte()
    if not compte:
        return _non_connecte()
    donnees = _corps().get("donnees")
    if not isinstance(donnees, dict):
        return jsonify(erreur="Données invalides."), 400
    contenu = json.dumps(donnees, ensure_ascii=False, separators=(",", ":"))
    if len(contenu.encode("utf-8")) > DONNEES_MAX:
        return jsonify(erreur="Données trop volumineuses pour être enregistrées dans ton compte."), 413
    maintenant = int(time.time())
    with _base() as b:
        b.execute("""INSERT INTO donnees (compte_id, contenu, modifie_le) VALUES (?, ?, ?)
                     ON CONFLICT(compte_id) DO UPDATE SET contenu = excluded.contenu,
                                                          modifie_le = excluded.modifie_le""",
                  (compte[0], contenu, maintenant))
    return jsonify(modifie_le=maintenant)


@comptes.delete("")
def supprimer():
    """Supprime le compte ET le CV enregistré. Le mot de passe est redemandé."""
    compte = compte_connecte()
    if not compte:
        return _non_connecte()
    cles = (("ip", request.remote_addr), ("email", compte[1]))
    if _bloque(*cles):
        return jsonify(erreur="Trop de tentatives. Réessaie dans un quart d'heure."), 429
    mdp = _corps().get("mot_de_passe")
    with _base() as b:
        ligne = b.execute("SELECT empreinte FROM comptes WHERE id = ?", (compte[0],)).fetchone()
    if not ligne or not isinstance(mdp, str) or not check_password_hash(ligne[0], mdp[:MDP_MAX]):
        _noter(*cles)
        return jsonify(erreur="Mot de passe incorrect."), 401
    with _base() as b:
        b.execute("DELETE FROM comptes WHERE id = ?", (compte[0],))   # les données suivent (CASCADE)
    _oublier(("email", compte[1]))
    session.clear()
    current_app.logger.info("Compte supprimé.")       # sans l'adresse : rien de personnel dans les journaux
    return jsonify(connecte=False)
