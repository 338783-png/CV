# CV.express

Générateur de CV et de lettres de motivation en PDF (français, anglais, italien).

## Lancer en développement

```bash
.venv/bin/python app.py          # puis ouvrir http://localhost:5000
```

Sans serveur d'e-mails configuré, les liens d'inscription et de mot de passe oublié
sont écrits dans le terminal au lieu d'être envoyés.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

## Production

Voir le `Dockerfile`. Avec `DERRIERE_PROXY=1`, le serveur refuse de démarrer sans
`ADRESSE_SITE` (en https), `CLE_SECRETE` et les réglages SMTP.

La base des comptes vit dans `/donnees` : monter un volume à cet endroit.

## Sauvegardes de la base

Le serveur fait une copie de la base toutes les 24 heures dans `/donnees/sauvegardes/`
et garde les 14 plus récentes. Réglages : `SAUVEGARDE_HEURES` (0 pour désactiver) et
`SAUVEGARDES_GARDEES`. Copie immédiate, par exemple depuis une tâche cron :

```bash
python -m comptes sauvegarder
```

**Ces copies sont sur le même disque que la base.** Elles protègent d'une erreur ou
d'une base abîmée, pas de la perte du disque : activer aussi les instantanés (snapshots)
du volume chez l'hébergeur, ou copier régulièrement `/donnees/sauvegardes/` ailleurs.

Restaurer une sauvegarde :

1. arrêter le serveur ;
2. remplacer `/donnees/comptes.sqlite3` par la copie choisie, et supprimer
   `comptes.sqlite3-wal` et `comptes.sqlite3-shm` s'ils existent ;
3. vérifier que le fichier appartient à l'utilisateur du serveur, avec les droits 600 ;
4. relancer le serveur.
