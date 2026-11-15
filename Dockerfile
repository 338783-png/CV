# Image du site : Python + LaTeX + poppler (aperçus).
# Construire :  docker build -t cv-saas .
# Lancer     :  docker run -p 8000:8000 -e DERRIERE_PROXY=1 -e CLE_SECRETE=… -v cv-donnees:/donnees cv-saas
FROM python:3.12-slim

# LaTeX : uniquement les paquets utilisés par les modèles (templates/).
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-base texlive-latex-recommended texlive-latex-extra \
        texlive-fonts-recommended texlive-pictures lmodern tex-gyre \
        texlive-luatex fonts-dejavu-core fonts-dejavu-extra \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py moteur.py comptes.py importer.py textes.py miniatures.py editeur.json ./
COPY templates ./templates
COPY web ./web
COPY pages ./pages

# Le serveur ne tourne pas en root.
RUN useradd --create-home site

# Comptes utilisateurs : la base de données vit dans /donnees. Monter un volume à cet
# endroit (docker run -v cv-donnees:/donnees …), sinon les comptes disparaissent à chaque
# mise à jour du site. CLE_SECRETE (32 caractères ou plus) signe les cookies de session.
# Mot de passe oublié : SMTP_HOTE, SMTP_PORT, SMTP_UTILISATEUR, SMTP_MOT_DE_PASSE, EMAIL_EXPEDITEUR
# et ADRESSE_SITE (ex. https://cv.express) ; voir le début de comptes.py.
RUN mkdir /donnees && chown site /donnees
ENV DOSSIER_DONNEES=/donnees
VOLUME /donnees
USER site

ENV PORT=8000
EXPOSE 8000
# Un seul processus, plusieurs fils : la limite de débit est comptée en mémoire.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 --timeout 60 -b 0.0.0.0:${PORT} app:app"]
