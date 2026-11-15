"""Sécurité des comptes : inscription vérifiée, non-divulgation, blocages, liens, configuration."""
import os
import stat
import time

import pytest

import app as application
import comptes
from conftest import jeton_de

API = "/api/compte"


def ip(adresse):
    return {"REMOTE_ADDR": adresse}


def nombre_de_comptes():
    with comptes._base() as b:
        return b.execute("SELECT COUNT(*) FROM comptes").fetchone()[0]


# ---------------------------------------------------------------------------
# Inscription : l'adresse e-mail est toujours vérifiée
# ---------------------------------------------------------------------------
def test_inscription_ne_cree_le_compte_qu_a_l_ouverture_du_lien(client, liens):
    r = client.post(f"{API}/inscription", json={"email": " Awa@Exemple.org ", "langue": "it"})
    assert r.status_code == 202 and r.get_json() == {"envoye": True}
    assert nombre_de_comptes() == 0                      # rien tant que le lien n'est pas ouvert
    assert client.get(API).get_json()["connecte"] is False
    adresse, genre, langue, lien = liens[-1]
    assert (adresse, genre, langue) == ("awa@exemple.org", "invitation", "it")
    assert "/it/compte?jeton=" in lien

    r = client.post(f"{API}/nouveau-mot-de-passe", json={"jeton": jeton_de(lien), "mot_de_passe": "Mot-de-passe-42"})
    assert r.status_code == 200
    assert r.get_json() == {"connecte": True, "email": "awa@exemple.org", "nouveau_compte": True}
    assert nombre_de_comptes() == 1
    assert client.get(API).get_json()["email"] == "awa@exemple.org"


def test_un_mot_de_passe_envoye_a_l_inscription_est_ignore(client, liens, nouveau_client):
    """On ne peut plus créer un compte au nom d'une adresse sans lire sa boîte."""
    r = client.post(f"{API}/inscription", json={"email": "victime@exemple.org", "mot_de_passe": "Pirate-passe-9"})
    assert r.status_code == 202 and nombre_de_comptes() == 0
    r = nouveau_client().post(f"{API}/connexion", json={"email": "victime@exemple.org", "mot_de_passe": "Pirate-passe-9"})
    assert r.status_code == 401


def test_le_lien_d_inscription_ne_sert_qu_une_fois(client, liens, nouveau_client):
    client.post(f"{API}/inscription", json={"email": "awa@exemple.org"})
    jeton = jeton_de(liens[-1][3])
    assert client.post(f"{API}/nouveau-mot-de-passe", json={"jeton": jeton, "mot_de_passe": "Mot-de-passe-42"}).status_code == 200
    r = nouveau_client().post(f"{API}/nouveau-mot-de-passe", json={"jeton": jeton, "mot_de_passe": "Autre-passe-77"})
    assert r.status_code == 400
    assert nouveau_client().post(f"{API}/connexion", json={"email": "awa@exemple.org", "mot_de_passe": "Mot-de-passe-42"}).status_code == 200


def test_une_nouvelle_demande_remplace_le_lien_precedent(client, liens):
    client.post(f"{API}/inscription", json={"email": "awa@exemple.org"})
    ancien = jeton_de(liens[-1][3])
    client.post(f"{API}/inscription", json={"email": "awa@exemple.org"})
    nouveau = jeton_de(liens[-1][3])
    assert ancien != nouveau
    assert client.post(f"{API}/nouveau-mot-de-passe", json={"jeton": ancien, "mot_de_passe": "Mot-de-passe-42"}).status_code == 400
    assert client.post(f"{API}/nouveau-mot-de-passe", json={"jeton": nouveau, "mot_de_passe": "Mot-de-passe-42"}).status_code == 200


def test_lien_d_inscription_expire(client, liens):
    client.post(f"{API}/inscription", json={"email": "awa@exemple.org"})
    with comptes._base() as b:
        b.execute("UPDATE invitations SET expire_le = ?", (int(time.time()) - 1,))
    r = client.post(f"{API}/nouveau-mot-de-passe", json={"jeton": jeton_de(liens[-1][3]), "mot_de_passe": "Mot-de-passe-42"})
    assert r.status_code == 400 and nombre_de_comptes() == 0


def test_seule_l_empreinte_du_jeton_est_en_base(client, liens):
    client.post(f"{API}/inscription", json={"email": "awa@exemple.org"})
    jeton = jeton_de(liens[-1][3])
    with comptes._base() as b:
        (empreinte,) = b.execute("SELECT empreinte FROM invitations").fetchone()
    assert jeton not in empreinte and len(empreinte) == 64


@pytest.mark.parametrize("mot_de_passe", ["court", "awa@exemple.org", "awa", "", None, ["x"]])
def test_mot_de_passe_faible_refuse_sans_consommer_le_lien(client, liens, mot_de_passe):
    client.post(f"{API}/inscription", json={"email": "awa@exemple.org"})
    jeton = jeton_de(liens[-1][3])
    assert client.post(f"{API}/nouveau-mot-de-passe", json={"jeton": jeton, "mot_de_passe": mot_de_passe}).status_code == 400
    assert client.post(f"{API}/nouveau-mot-de-passe", json={"jeton": jeton, "mot_de_passe": "Mot-de-passe-42"}).status_code == 200


# ---------------------------------------------------------------------------
# Le site ne révèle pas qui est inscrit
# ---------------------------------------------------------------------------
def test_inscription_meme_reponse_que_l_adresse_ait_un_compte_ou_non(inscrire, liens, nouveau_client):
    inscrire("deja@exemple.org")
    r1 = nouveau_client().post(f"{API}/inscription", json={"email": "deja@exemple.org"})
    r2 = nouveau_client().post(f"{API}/inscription", json={"email": "nouvelle@exemple.org"})
    assert (r1.status_code, r1.get_json()) == (r2.status_code, r2.get_json()) == (202, {"envoye": True})
    # La différence n'apparaît que dans la boîte du titulaire de l'adresse.
    assert [(e, genre) for e, genre, _, _ in liens[-2:]] == [("deja@exemple.org", "deja"), ("nouvelle@exemple.org", "invitation")]


def test_inscription_sur_un_compte_existant_ne_le_modifie_pas(inscrire, liens, nouveau_client):
    titulaire = inscrire("deja@exemple.org", "Mot-de-passe-42")
    nouveau_client().post(f"{API}/inscription", json={"email": "deja@exemple.org"})
    assert titulaire.get(API).get_json()["connecte"] is True                 # toujours connecté
    r = nouveau_client().post(f"{API}/connexion", json={"email": "deja@exemple.org", "mot_de_passe": "Mot-de-passe-42"})
    assert r.status_code == 200                                               # mot de passe inchangé
    assert nombre_de_comptes() == 1


def test_mot_de_passe_oublie_meme_reponse_et_aucun_mail_pour_une_adresse_inconnue(inscrire, liens, nouveau_client):
    inscrire("deja@exemple.org")
    avant = len(liens)
    r1 = nouveau_client().post(f"{API}/mot-de-passe-oublie", json={"email": "deja@exemple.org"})
    r2 = nouveau_client().post(f"{API}/mot-de-passe-oublie", json={"email": "inconnue@exemple.org"})
    assert (r1.status_code, r1.get_json()) == (r2.status_code, r2.get_json()) == (200, {"envoye": True})
    assert [(e, genre) for e, genre, _, _ in liens[avant:]] == [("deja@exemple.org", "oubli")]


def test_connexion_meme_message_compte_connu_ou_inconnu(inscrire, nouveau_client):
    inscrire("deja@exemple.org")
    r1 = nouveau_client().post(f"{API}/connexion", json={"email": "deja@exemple.org", "mot_de_passe": "mauvais"})
    r2 = nouveau_client().post(f"{API}/connexion", json={"email": "inconnue@exemple.org", "mot_de_passe": "mauvais"})
    assert (r1.status_code, r1.get_json()) == (r2.status_code, r2.get_json())
    assert r1.status_code == 401


# ---------------------------------------------------------------------------
# Un attaquant ne peut pas bloquer le compte de quelqu'un d'autre
# ---------------------------------------------------------------------------
def test_les_echecs_d_un_attaquant_ne_bloquent_pas_le_titulaire(inscrire, nouveau_client):
    inscrire("victime@exemple.org", "Mot-de-passe-42", ip="10.0.0.1")
    pirate = nouveau_client()
    codes = [pirate.post(f"{API}/connexion", json={"email": "victime@exemple.org", "mot_de_passe": f"essai{i}"},
                         environ_base=ip("6.6.6.6")).status_code for i in range(10)]
    assert codes[:8] == [401] * 8 and codes[8:] == [429, 429]            # l'attaquant, lui, est arrêté
    # Même avec le BON mot de passe, l'adresse de l'attaquant reste bloquée…
    r = pirate.post(f"{API}/connexion", json={"email": "victime@exemple.org", "mot_de_passe": "Mot-de-passe-42"},
                    environ_base=ip("6.6.6.6"))
    assert r.status_code == 429
    # … mais le titulaire se connecte normalement depuis chez lui.
    r = nouveau_client().post(f"{API}/connexion", json={"email": "victime@exemple.org", "mot_de_passe": "Mot-de-passe-42"},
                              environ_base=ip("10.0.0.1"))
    assert r.status_code == 200


def test_blocage_global_tres_haut_et_leve_par_le_lien(inscrire, liens, nouveau_client, monkeypatch):
    monkeypatch.setitem(comptes.LIMITES, "email", 12)        # 100 en réalité : réduit pour un test rapide
    inscrire("victime@exemple.org", "Mot-de-passe-42")
    for n in range(12):                                      # attaque répartie sur beaucoup d'adresses IP
        nouveau_client().post(f"{API}/connexion", json={"email": "victime@exemple.org", "mot_de_passe": "x"},
                              environ_base=ip(f"7.7.7.{n}"))
    chez_lui = ip("10.0.0.1")
    r = nouveau_client().post(f"{API}/connexion", json={"email": "victime@exemple.org", "mot_de_passe": "Mot-de-passe-42"},
                              environ_base=chez_lui)
    assert r.status_code == 429 and "Mot de passe oublié" in r.get_json()["erreur"]
    # Le lien « mot de passe oublié » reste disponible et lève le blocage.
    c = nouveau_client()
    assert c.post(f"{API}/mot-de-passe-oublie", json={"email": "victime@exemple.org"}, environ_base=chez_lui).status_code == 200
    r = c.post(f"{API}/nouveau-mot-de-passe", json={"jeton": jeton_de(liens[-1][3]), "mot_de_passe": "Nouveau-passe-77"},
               environ_base=chez_lui)
    assert r.status_code == 200 and r.get_json()["nouveau_compte"] is False
    r = nouveau_client().post(f"{API}/connexion", json={"email": "victime@exemple.org", "mot_de_passe": "Nouveau-passe-77"},
                              environ_base=chez_lui)
    assert r.status_code == 200


def test_on_ne_peut_pas_inonder_une_boite_de_mails(liens, nouveau_client):
    codes = [nouveau_client().post(f"{API}/inscription", json={"email": "cible@exemple.org"},
                                   environ_base=ip(f"8.8.8.{n}")).status_code for n in range(5)]
    assert codes == [202, 202, 202, 429, 429]                # 3 e-mails au plus par quart d'heure et par adresse
    assert len(liens) == 3


def test_essais_de_jetons_au_hasard_bloques(client):
    codes = [client.post(f"{API}/nouveau-mot-de-passe", json={"jeton": "k" * 43, "mot_de_passe": "Mot-de-passe-42"}).status_code
             for _ in range(12)]
    assert codes[:10] == [400] * 10 and codes[10:] == [429, 429]


# ---------------------------------------------------------------------------
# Mot de passe oublié
# ---------------------------------------------------------------------------
def test_mot_de_passe_oublie_deconnecte_les_autres_appareils(inscrire, liens, nouveau_client):
    ancien_appareil = inscrire("awa@exemple.org", "Ancien-passe-11")
    ancien_appareil.put(f"{API}/donnees", json={"donnees": {"cv": {"nom": "Awa"}}})
    c = nouveau_client()
    c.post(f"{API}/mot-de-passe-oublie", json={"email": "awa@exemple.org", "langue": "en"})
    assert "/en/compte?jeton=" in liens[-1][3]
    r = c.post(f"{API}/nouveau-mot-de-passe", json={"jeton": jeton_de(liens[-1][3]), "mot_de_passe": "Nouveau-passe-77"})
    assert r.status_code == 200
    assert c.get(f"{API}/donnees").get_json()["donnees"]["cv"]["nom"] == "Awa"         # documents intacts
    assert ancien_appareil.get(API).get_json()["connecte"] is False
    assert nouveau_client().post(f"{API}/connexion", json={"email": "awa@exemple.org", "mot_de_passe": "Ancien-passe-11"}).status_code == 401


def test_lien_de_reinitialisation_expire(inscrire, liens, nouveau_client):
    inscrire("awa@exemple.org")
    c = nouveau_client()
    c.post(f"{API}/mot-de-passe-oublie", json={"email": "awa@exemple.org"})
    with comptes._base() as b:
        b.execute("UPDATE reinitialisations SET expire_le = ?", (int(time.time()) - 1,))
    assert c.post(f"{API}/nouveau-mot-de-passe", json={"jeton": jeton_de(liens[-1][3]), "mot_de_passe": "Trop-tard-44"}).status_code == 400


def test_supprimer_le_compte_annule_son_lien(inscrire, liens, nouveau_client):
    c = inscrire("awa@exemple.org", "Mot-de-passe-42")
    nouveau_client().post(f"{API}/mot-de-passe-oublie", json={"email": "awa@exemple.org"})
    jeton = jeton_de(liens[-1][3])
    assert c.delete(API, json={"mot_de_passe": "Mot-de-passe-42"}).status_code == 200
    assert nouveau_client().post(f"{API}/nouveau-mot-de-passe", json={"jeton": jeton, "mot_de_passe": "Retour-55-xx"}).status_code == 400
    assert nombre_de_comptes() == 0


# ---------------------------------------------------------------------------
# L'adresse mise dans les liens ne vient jamais de la requête, en production
# ---------------------------------------------------------------------------
def test_le_lien_utilise_l_adresse_du_site_pas_l_en_tete_host(client, liens, monkeypatch):
    monkeypatch.setattr(comptes, "ADRESSE_SITE", "https://cv.exemple")
    client.post(f"{API}/inscription", json={"email": "awa@exemple.org"}, headers={"Host": "site-du-pirate.example"})
    assert liens[-1][3].startswith("https://cv.exemple/compte?jeton=")
    assert "pirate" not in liens[-1][3]


def test_adresses_publiques_des_pages_independantes_de_l_en_tete_host(client, monkeypatch):
    monkeypatch.setattr(comptes, "ADRESSE_SITE", "https://cv.exemple")
    for chemin in ("/", "/en/cv-europass", "/sitemap.xml", "/robots.txt"):
        texte = client.get(chemin, headers={"Host": "site-du-pirate.example"}).get_data(as_text=True)
        assert "pirate" not in texte, chemin
        assert "https://cv.exemple" in texte, chemin


def test_en_production_seul_le_nom_du_site_est_accepte(client, monkeypatch):
    monkeypatch.setitem(application.app.config, "TRUSTED_HOSTS", ["cv.exemple", "localhost"])
    assert client.get("/", headers={"Host": "site-du-pirate.example"}).status_code == 400
    assert client.get("/", headers={"Host": "cv.exemple"}).status_code == 200


@pytest.mark.parametrize("reglages, manque", [
    ({}, "ADRESSE_SITE"),
    ({"ADRESSE_SITE": "http://cv.exemple"}, "ADRESSE_SITE"),              # https obligatoire
    ({"ADRESSE_SITE": "https://cv.exemple"}, "CLE_SECRETE"),
    ({"ADRESSE_SITE": "https://cv.exemple", "CLE_SECRETE": "k" * 40}, "SMTP_HOTE"),
])
def test_la_production_refuse_de_demarrer_sans_ses_reglages(monkeypatch, reglages, manque):
    monkeypatch.setattr(comptes, "PRODUCTION", True)
    monkeypatch.setattr(comptes, "ADRESSE_SITE", reglages.get("ADRESSE_SITE", ""))
    monkeypatch.setattr(comptes, "SMTP_HOTE", "")
    monkeypatch.setattr(comptes, "EMAIL_EXPEDITEUR", "")
    monkeypatch.delenv("CLE_SECRETE", raising=False)
    if "CLE_SECRETE" in reglages:
        monkeypatch.setenv("CLE_SECRETE", reglages["CLE_SECRETE"])
    with pytest.raises(RuntimeError, match=manque):
        comptes._verifier_configuration()


def test_la_production_demarre_avec_tous_ses_reglages(monkeypatch):
    monkeypatch.setattr(comptes, "PRODUCTION", True)
    monkeypatch.setattr(comptes, "ADRESSE_SITE", "https://cv.exemple")
    monkeypatch.setattr(comptes, "SMTP_HOTE", "smtp.exemple")
    monkeypatch.setattr(comptes, "EMAIL_EXPEDITEUR", "site@cv.exemple")
    monkeypatch.setenv("CLE_SECRETE", "k" * 40)
    comptes._verifier_configuration()


# ---------------------------------------------------------------------------
# Clé secrète et base de données
# ---------------------------------------------------------------------------
@pytest.mark.skipif(comptes.POSTGRES, reason="base SQLite seulement")
def test_base_et_cle_lisibles_par_le_serveur_seulement():
    dossier = comptes.DOSSIER
    assert stat.S_IMODE(os.stat(dossier).st_mode) == 0o700
    fichiers = list(dossier.glob("comptes.sqlite3*")) + [dossier / "cle_secrete"]
    assert len(fichiers) >= 2
    for fichier in fichiers:
        assert stat.S_IMODE(os.stat(fichier).st_mode) == 0o600, fichier.name


def test_les_fichiers_sensibles_ne_sont_pas_servis(client):
    for chemin in ("/comptes.py", "/donnees/comptes.sqlite3", "/donnees/cle_secrete", "/editeur.json",
                   "/../comptes.py", "/production.env", "/.git/config", "/tests/conftest.py"):
        assert client.get(chemin).status_code == 404, chemin


def test_les_connexions_a_la_base_sont_fermees(inscrire):
    c = inscrire("awa@exemple.org")
    ouverts = lambda: len(os.listdir("/proc/self/fd"))        # noqa: E731
    avant = ouverts()
    for _ in range(150):
        c.get(API)
        c.get(f"{API}/documents")
    assert ouverts() <= avant + 3                              # aucun descripteur de fichier ne s'accumule


# ---------------------------------------------------------------------------
# Session et origine des requêtes
# ---------------------------------------------------------------------------
def test_cookie_de_session_protege(inscrire, liens, client):
    client.post(f"{API}/inscription", json={"email": "awa@exemple.org"})
    r = client.post(f"{API}/nouveau-mot-de-passe", json={"jeton": jeton_de(liens[-1][3]), "mot_de_passe": "Mot-de-passe-42"})
    cookie = r.headers["Set-Cookie"]
    assert cookie.startswith("session_cv=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Mot-de-passe-42" not in cookie


def test_requete_venue_d_un_autre_site_refusee(inscrire):
    c = inscrire("awa@exemple.org")
    r = c.put(f"{API}/donnees", json={"donnees": {"cv": {"nom": "pirate"}}}, headers={"Origin": "https://mechant.example"})
    assert r.status_code == 403
    r = c.post(f"{API}/deconnexion", data="a=1", content_type="application/x-www-form-urlencoded")
    assert r.status_code == 400
    assert c.get(API).get_json()["connecte"] is True


def test_un_compte_n_accede_pas_aux_documents_d_un_autre(inscrire):
    a, b = inscrire("a@exemple.org"), inscrire("b@exemple.org", ip="10.0.0.2")
    numero = a.put(f"{API}/donnees", json={"donnees": {"cv": {"nom": "Awa SECRET"}}}).get_json()["doc"]
    b.put(f"{API}/donnees", json={"donnees": {"cv": {"nom": "Bob"}}})
    assert b.get(f"{API}/donnees?doc={numero}").status_code == 404
    assert b.put(f"{API}/donnees?doc={numero}", json={"donnees": {"cv": {"nom": "PIRATE"}}}).status_code == 404
    assert b.patch(f"{API}/documents/{numero}", json={"nom": "PIRATE"}).status_code == 404
    assert b.post(f"{API}/documents", json={"copie_de": numero}).status_code == 404
    assert b.delete(f"{API}/documents/{numero}", json={}).status_code == 404
    assert [d["donnees"]["cv"]["nom"] for d in b.get(f"{API}/documents").get_json()["documents"]] == ["Bob"]
    assert a.get(f"{API}/donnees?doc={numero}").get_json()["donnees"]["cv"]["nom"] == "Awa SECRET"
