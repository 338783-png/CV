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
  - les requêtes qui modifient quelque chose doivent venir de notre propre site ;
  - mot de passe oublié : un lien à usage unique, valable 30 minutes, est envoyé par e-mail ;
    seule son empreinte est gardée en base, et la réponse est la même que le compte existe ou non.

Stockage : un fichier SQLite (dossier donnees/, à sauvegarder et à garder hors de Git).
"""
import hashlib
import json
import os
import re
import secrets
import smtplib
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

RACINE = Path(__file__).parent
DOSSIER = Path(os.environ.get("DOSSIER_DONNEES") or RACINE / "donnees")
BASE = DOSSIER / "comptes.sqlite3"

MDP_MIN, MDP_MAX = 8, 200
DONNEES_MAX = 2_500_000          # taille maximale d'un document enregistré (photo comprise), en octets
DOCUMENTS_MAX = 10               # nombre de documents (un CV et sa lettre) par compte
NOM_MAX = 60
SESSION_JOURS = 30
LIEN_MINUTES = 30                # durée de validité d'un lien « mot de passe oublié »

# Envoi des e-mails (mot de passe oublié). À définir chez l'hébergeur :
#   SMTP_HOTE, SMTP_PORT (587 par défaut), SMTP_UTILISATEUR, SMTP_MOT_DE_PASSE,
#   EMAIL_EXPEDITEUR (adresse affichée comme expéditeur),
#   ADRESSE_SITE (ex. https://cv.express) : l'adresse mise dans le lien envoyé.
# Sans SMTP_HOTE (en développement), le lien est écrit dans le journal du serveur au lieu d'être envoyé.
SMTP_HOTE = os.environ.get("SMTP_HOTE", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT") or 587)
SMTP_UTILISATEUR = os.environ.get("SMTP_UTILISATEUR", "")
SMTP_MOT_DE_PASSE = os.environ.get("SMTP_MOT_DE_PASSE", "")
EMAIL_EXPEDITEUR = os.environ.get("EMAIL_EXPEDITEUR", "") or SMTP_UTILISATEUR
ADRESSE_SITE = os.environ.get("ADRESSE_SITE", "").rstrip("/")
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
        # Un compte peut garder plusieurs documents (un CV et sa lettre), par exemple un par offre.
        b.execute("""CREATE TABLE IF NOT EXISTS documents (
            id         INTEGER PRIMARY KEY,
            compte_id  INTEGER NOT NULL REFERENCES comptes(id) ON DELETE CASCADE,
            nom        TEXT NOT NULL,
            contenu    TEXT NOT NULL,
            modifie_le INTEGER NOT NULL)""")
        b.execute("CREATE INDEX IF NOT EXISTS documents_du_compte ON documents (compte_id, modifie_le)")
        # Liens « mot de passe oublié » : on ne garde que l'empreinte du jeton, jamais le jeton.
        b.execute("""CREATE TABLE IF NOT EXISTS reinitialisations (
            compte_id INTEGER PRIMARY KEY REFERENCES comptes(id) ON DELETE CASCADE,
            empreinte TEXT NOT NULL,
            expire_le INTEGER NOT NULL)""")
        # Ancienne table (un seul CV par compte) : son contenu devient le premier document.
        if b.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'donnees'").fetchone():
            b.execute("""INSERT INTO documents (compte_id, nom, contenu, modifie_le)
                         SELECT compte_id, 'Mon CV', contenu, modifie_le FROM donnees""")
            b.execute("DROP TABLE donnees")
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
LIMITES = {"email": 8, "ip": 20, "inscription": 10, "oubli_email": 3, "oubli_ip": 6, "jeton_ip": 10}
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
        sauvegarde = b.execute("SELECT MAX(modifie_le) FROM documents WHERE compte_id = ?", (compte[0],)).fetchone()
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


# --------------------------------------------------------------------------
# Mot de passe oublié
# --------------------------------------------------------------------------
_TEXTES_OUBLI = {
    "fr": ("CV.express : choisis un nouveau mot de passe",
           "Bonjour,\n\nTu as demandé à changer le mot de passe de ton compte CV.express.\n"
           "Ouvre ce lien pour en choisir un nouveau (valable {minutes} minutes, utilisable une seule fois) :\n\n{lien}\n\n"
           "Si tu n'es pas à l'origine de cette demande, ignore ce message : ton mot de passe ne change pas.\n\nCV.express\n"),
    "en": ("CV.express: choose a new password",
           "Hello,\n\nYou asked to change the password of your CV.express account.\n"
           "Open this link to choose a new one (valid for {minutes} minutes, usable only once):\n\n{lien}\n\n"
           "If you did not make this request, ignore this message: your password does not change.\n\nCV.express\n"),
    "it": ("CV.express: scegli una nuova password",
           "Ciao,\n\nHai chiesto di cambiare la password del tuo account CV.express.\n"
           "Apri questo link per sceglierne una nuova (valido {minutes} minuti, utilizzabile una sola volta):\n\n{lien}\n\n"
           "Se non hai fatto tu questa richiesta, ignora questo messaggio: la tua password non cambia.\n\nCV.express\n"),
}


def _empreinte_jeton(jeton):
    return hashlib.sha256(jeton.encode("utf-8")).hexdigest()


def _envoyer_lien(email, lien, langue, journal):
    """Envoie l'e-mail (dans un fil à part : la réponse au visiteur ne dépend pas de sa durée)."""
    sujet, corps = _TEXTES_OUBLI.get(langue, _TEXTES_OUBLI["fr"])
    if not SMTP_HOTE:
        journal.warning("SMTP non configuré : e-mail non envoyé. Lien de réinitialisation : %s", lien)
        return
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = sujet, EMAIL_EXPEDITEUR, email
    message.set_content(corps.format(lien=lien, minutes=LIEN_MINUTES))
    try:
        if SMTP_PORT == 465:
            serveur = smtplib.SMTP_SSL(SMTP_HOTE, SMTP_PORT, timeout=20)
        else:
            serveur = smtplib.SMTP(SMTP_HOTE, SMTP_PORT, timeout=20)
            serveur.starttls()                      # connexion chiffrée obligatoire
        with serveur:
            if SMTP_UTILISATEUR:
                serveur.login(SMTP_UTILISATEUR, SMTP_MOT_DE_PASSE)
            serveur.send_message(message)
    except (OSError, smtplib.SMTPException) as e:
        journal.error("Envoi de l'e-mail de réinitialisation impossible : %s", type(e).__name__)


@comptes.post("/mot-de-passe-oublie")
def mot_de_passe_oublie():
    """Envoie un lien de réinitialisation. La réponse est TOUJOURS la même, que l'adresse
    ait un compte ou non : on ne révèle pas qui est inscrit."""
    corps = _corps()
    email = corps.get("email")
    email = email.strip().lower()[:120] if isinstance(email, str) else ""
    langue = corps.get("langue") if corps.get("langue") in _TEXTES_OUBLI else "fr"
    cles = (("oubli_ip", request.remote_addr), ("oubli_email", email))
    if _bloque(*cles):
        return jsonify(erreur="Trop de demandes. Réessaie dans un quart d'heure."), 429
    if not _EMAIL_OK.match(email):
        return jsonify(erreur="Adresse e-mail invalide."), 400
    _noter(*cles)
    with _base() as b:
        ligne = b.execute("SELECT id FROM comptes WHERE email = ?", (email,)).fetchone()
        if ligne:
            jeton = secrets.token_urlsafe(32)
            # Un seul lien valable par compte : le nouveau remplace le précédent.
            b.execute("""INSERT INTO reinitialisations (compte_id, empreinte, expire_le) VALUES (?, ?, ?)
                         ON CONFLICT(compte_id) DO UPDATE SET empreinte = excluded.empreinte,
                                                              expire_le = excluded.expire_le""",
                      (ligne[0], _empreinte_jeton(jeton), int(time.time()) + LIEN_MINUTES * 60))
            # L'adresse du site vient de la configuration (ADRESSE_SITE), pas de la requête :
            # sinon un attaquant pourrait faire envoyer un lien pointant vers son propre site.
            racine = ADRESSE_SITE or request.url_root.rstrip("/")
            prefixe = {"en": "/en", "it": "/it"}.get(langue, "")
            lien = f"{racine}{prefixe}/compte?jeton={jeton}"
            threading.Thread(target=_envoyer_lien, args=(email, lien, langue, current_app.logger), daemon=True).start()
    return jsonify(envoye=True)


@comptes.post("/nouveau-mot-de-passe")
def nouveau_mot_de_passe():
    """Deuxième étape : le jeton reçu par e-mail + le nouveau mot de passe."""
    corps = _corps()
    jeton, nouveau = corps.get("jeton"), corps.get("mot_de_passe")
    limite = ("jeton_ip", request.remote_addr)
    if _bloque(limite):
        return jsonify(erreur="Trop de tentatives. Réessaie dans un quart d'heure."), 429
    invalide = jsonify(erreur="Ce lien n'est plus valable. Demande un nouveau lien."), 400
    if not isinstance(jeton, str) or not 20 <= len(jeton) <= 200:
        _noter(limite)
        return invalide
    with _base() as b:
        ligne = b.execute("""SELECT c.id, c.email FROM reinitialisations r JOIN comptes c ON c.id = r.compte_id
                             WHERE r.empreinte = ? AND r.expire_le >= ?""",
                          (_empreinte_jeton(jeton), int(time.time()))).fetchone()
    if not ligne:
        _noter(limite)
        return invalide
    if not isinstance(nouveau, str) or not MDP_MIN <= len(nouveau) <= MDP_MAX:
        return jsonify(erreur=f"Le mot de passe doit contenir au moins {MDP_MIN} caractères."), 400
    if nouveau.lower() in (ligne[1], ligne[1].split("@")[0]):
        return jsonify(erreur="Choisis un mot de passe différent de ton adresse e-mail."), 400
    session_jeton = secrets.token_hex(16)        # toutes les sessions ouvertes cessent d'être valables
    with _base() as b:
        # Le lien ne sert qu'une fois : on le supprime dans la même opération que le changement.
        utilises = b.execute("DELETE FROM reinitialisations WHERE compte_id = ? AND empreinte = ?",
                             (ligne[0], _empreinte_jeton(jeton))).rowcount
        if not utilises:
            return invalide
        b.execute("UPDATE comptes SET empreinte = ?, jeton = ?, vu_le = ? WHERE id = ?",
                  (generate_password_hash(nouveau), session_jeton, int(time.time()), ligne[0]))
    _oublier(("email", ligne[1]))                # les anciens échecs de connexion ne bloquent plus
    _ouvrir_session(ligne[0], session_jeton)
    return jsonify(connecte=True, email=ligne[1])


@comptes.post("/deconnexion")
def deconnexion():
    session.clear()
    return jsonify(connecte=False)


# --------------------------------------------------------------------------
# Documents du compte. Toutes les requêtes portent « compte_id = celui de la session » :
# un compte ne peut ni lire, ni modifier, ni supprimer le document d'un autre.
# --------------------------------------------------------------------------
def _numero(valeur):
    """Numéro de document reçu dans l'adresse ou le corps : entier positif, sinon None."""
    try:
        numero = int(valeur)
    except (TypeError, ValueError):
        return None
    return numero if 0 < numero < 2 ** 62 else None


def _contenu(donnees):
    """dict -> texte JSON à enregistrer ; lève ValueError si invalide ou trop gros."""
    if not isinstance(donnees, dict):
        raise ValueError("Données invalides.")
    contenu = json.dumps(donnees, ensure_ascii=False, separators=(",", ":"))
    if len(contenu.encode("utf-8")) > DONNEES_MAX:
        raise OverflowError("Données trop volumineuses pour être enregistrées dans ton compte.")
    return contenu


def _nom(valeur, defaut):
    nom = " ".join(valeur.split())[:NOM_MAX] if isinstance(valeur, str) else ""
    return nom or defaut


def _creer(b, compte_id, nom, contenu):
    """Ajoute un document ; None si le compte en a déjà le maximum."""
    nombre = b.execute("SELECT COUNT(*) FROM documents WHERE compte_id = ?", (compte_id,)).fetchone()[0]
    if nombre >= DOCUMENTS_MAX:
        return None
    maintenant = int(time.time())
    curseur = b.execute("INSERT INTO documents (compte_id, nom, contenu, modifie_le) VALUES (?, ?, ?, ?)",
                        (compte_id, _nom(nom, f"Document {nombre + 1}"), contenu, maintenant))
    return curseur.lastrowid, maintenant


def _trop_de_documents():
    return jsonify(erreur=f"Tu as déjà {DOCUMENTS_MAX} documents dans ton compte. Supprimes-en un pour en ajouter."), 409


@comptes.get("/donnees")
def lire_donnees():
    """Un document : celui demandé (?doc=…), sinon le plus récemment modifié."""
    compte = compte_connecte()
    if not compte:
        return _non_connecte()
    demande = request.args.get("doc")
    with _base() as b:
        if demande is not None:
            ligne = b.execute("SELECT id, nom, contenu, modifie_le FROM documents WHERE id = ? AND compte_id = ?",
                              (_numero(demande), compte[0])).fetchone()
            if not ligne:
                return jsonify(erreur="Ce document n'existe pas ou n'existe plus."), 404
        else:
            ligne = b.execute("""SELECT id, nom, contenu, modifie_le FROM documents WHERE compte_id = ?
                                 ORDER BY modifie_le DESC, id DESC LIMIT 1""", (compte[0],)).fetchone()
    if not ligne:
        return jsonify(donnees=None, modifie_le=None, doc=None)
    return jsonify(doc=ligne[0], nom=ligne[1], donnees=json.loads(ligne[2]), modifie_le=ligne[3])


@comptes.put("/donnees")
def ecrire_donnees():
    """Enregistre un document : celui indiqué (?doc=…) ; sans numéro, le plus récent,
    ou un nouveau si le compte n'en a pas ou si « nouveau » est demandé."""
    compte = compte_connecte()
    if not compte:
        return _non_connecte()
    corps = _corps()
    try:
        contenu = _contenu(corps.get("donnees"))
    except OverflowError as e:
        return jsonify(erreur=str(e)), 413
    except ValueError as e:
        return jsonify(erreur=str(e)), 400
    demande = request.args.get("doc")
    maintenant = int(time.time())
    with _base() as b:
        if demande is not None:
            numero = _numero(demande)
        elif corps.get("nouveau") is True:
            numero = None
        else:
            ligne = b.execute("""SELECT id FROM documents WHERE compte_id = ?
                                 ORDER BY modifie_le DESC, id DESC LIMIT 1""", (compte[0],)).fetchone()
            numero = ligne[0] if ligne else None
        if numero is None and demande is None:
            cree = _creer(b, compte[0], corps.get("nom"), contenu)
            if cree is None:
                return _trop_de_documents()
            return jsonify(doc=cree[0], modifie_le=cree[1]), 201
        modifies = b.execute("UPDATE documents SET contenu = ?, modifie_le = ? WHERE id = ? AND compte_id = ?",
                             (contenu, maintenant, numero, compte[0])).rowcount
    if not modifies:
        return jsonify(erreur="Ce document n'existe pas ou n'existe plus."), 404
    return jsonify(doc=numero, modifie_le=maintenant)


@comptes.get("/documents")
def lister_documents():
    """Tous les documents du compte, du plus récent au plus ancien. La photo, lourde,
    n'est pas renvoyée ici (seulement son existence) : elle l'est avec le document lui-même."""
    compte = compte_connecte()
    if not compte:
        return _non_connecte()
    with _base() as b:
        lignes = b.execute("""SELECT id, nom, contenu, modifie_le FROM documents WHERE compte_id = ?
                              ORDER BY modifie_le DESC, id DESC""", (compte[0],)).fetchall()
    documents = []
    for numero, nom, contenu, modifie_le in lignes:
        donnees = json.loads(contenu)
        cv = donnees.get("cv")
        if isinstance(cv, dict) and cv.get("photo"):
            donnees["cv"] = {**cv, "photo": "", "a_une_photo": True}
        documents.append({"doc": numero, "nom": nom, "modifie_le": modifie_le, "donnees": donnees})
    return jsonify(documents=documents, maximum=DOCUMENTS_MAX)


@comptes.post("/documents")
def creer_document():
    """Nouveau document : vide, ou copie d'un document existant (« copie_de »)."""
    compte = compte_connecte()
    if not compte:
        return _non_connecte()
    corps = _corps()
    with _base() as b:
        contenu, nom = "{}", corps.get("nom")
        if corps.get("copie_de") is not None:
            source = b.execute("SELECT nom, contenu FROM documents WHERE id = ? AND compte_id = ?",
                               (_numero(corps.get("copie_de")), compte[0])).fetchone()
            if not source:
                return jsonify(erreur="Ce document n'existe pas ou n'existe plus."), 404
            contenu, nom = source[1], nom or f"{source[0]} (copie)"
        cree = _creer(b, compte[0], nom, contenu)
    if cree is None:
        return _trop_de_documents()
    return jsonify(doc=cree[0], modifie_le=cree[1]), 201


@comptes.patch("/documents/<int:numero>")
def renommer_document(numero):
    compte = compte_connecte()
    if not compte:
        return _non_connecte()
    nom = _nom(_corps().get("nom"), "")
    if not nom:
        return jsonify(erreur="Donne un nom à ce document."), 400
    with _base() as b:
        modifies = b.execute("UPDATE documents SET nom = ? WHERE id = ? AND compte_id = ?",
                             (nom, numero, compte[0])).rowcount
    if not modifies:
        return jsonify(erreur="Ce document n'existe pas ou n'existe plus."), 404
    return jsonify(doc=numero, nom=nom)


@comptes.delete("/documents/<int:numero>")
def supprimer_document(numero):
    compte = compte_connecte()
    if not compte:
        return _non_connecte()
    with _base() as b:
        supprimes = b.execute("DELETE FROM documents WHERE id = ? AND compte_id = ?", (numero, compte[0])).rowcount
    if not supprimes:
        return jsonify(erreur="Ce document n'existe pas ou n'existe plus."), 404
    return jsonify(supprime=numero)


@comptes.delete("")
def supprimer():
    """Supprime le compte ET tous ses documents. Le mot de passe est redemandé."""
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
        b.execute("DELETE FROM comptes WHERE id = ?", (compte[0],))   # les documents suivent (CASCADE)
    _oublier(("email", compte[1]))
    session.clear()
    current_app.logger.info("Compte supprimé.")       # sans l'adresse : rien de personnel dans les journaux
    return jsonify(connecte=False)
