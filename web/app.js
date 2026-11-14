"use strict";
/*
 * Éditeur : formulaire -> état (objet JS) -> POST /api/apercu (images) et /api/pdf (téléchargement).
 * L'état est sauvegardé dans le navigateur (localStorage) : rien n'est conservé sur le serveur.
 */

const CLE_STOCKAGE = "cv-saas-v1";
const MODELES = ["moderne", "classique", "europeen", "africain"];

// Description des listes répétables : [champ, libellé, type]
const LISTES = {
  experiences: { nom: "Expérience", champs: [
    ["poste", "Poste"], ["entreprise", "Entreprise"], ["lieu", "Lieu"],
    ["dates", "Dates (ex. 08/2026 – 10/2026)"],
    ["points", "Missions et résultats (un par ligne)", "textarea"]] },
  formations: { nom: "Formation", champs: [
    ["diplome", "Diplôme"], ["ecole", "Établissement"], ["dates", "Dates"],
    ["details", "Détails (mention, matières…)"]] },
  competences: { nom: "Compétence", champs: [
    ["categorie", "Catégorie (ex. Informatique)"], ["liste", "Compétences"]] },
  langues: { nom: "Langue", champs: [
    ["langue", "Langue"], ["niveau", "Niveau (ex. Langue maternelle, B2…)"]] },
  liens: { nom: "Lien", champs: [["label", "Texte affiché"], ["url", "Adresse (URL)"]] },
  references: { nom: "Référence", champs: [
    ["nom", "Nom"], ["fonction", "Fonction et entreprise"], ["contact", "Téléphone ou e-mail"]] },
};

const CONSEILS = {
  moderne: "Bandeau de couleur et photo ronde : un CV qui se remarque, idéal pour les métiers de contact, du commerce et du numérique.",
  classique: "Une colonne, sans fioritures : le plus sûr pour les logiciels de tri de candidatures (ATS). En Europe, la photo et l'état civil sont en général facultatifs.",
  europeen: "Présentation inspirée d'Europass : libellés à gauche, expériences de la plus récente à la plus ancienne, langues avec niveau CECRL (A1 à C2).",
  africain: "Présentation courante en Afrique : bloc d'état civil complet, photo, références et mention de certification sur l'honneur.",
};

// Ce qui compte dans la jauge « CV complété à … % » : [test, conseil si manquant]
const ETAPES = [
  [cv => cv.nom.trim(), "ton nom"],
  [cv => cv.titre.trim(), "le poste visé"],
  [cv => cv.email.trim(), "ton e-mail"],
  [cv => cv.telephone.trim(), "ton téléphone"],
  [cv => cv.ville.trim(), "ta ville"],
  [cv => cv.experiences.some(e => (e.poste || "").trim()), "une expérience"],
  [cv => cv.formations.some(f => (f.diplome || "").trim()), "une formation"],
  [cv => cv.competences.some(c => (c.liste || "").trim()), "tes compétences"],
  [cv => cv.langues.some(l => (l.langue || "").trim()), "les langues que tu parles"],
  [cv => cv.profil.trim(), "quelques lignes de profil"],
];

const TEXTES_LETTRE = {
  fr: { salutation: "Madame, Monsieur,",
        formule: "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.",
        trame: "Actuellement [ta situation], je vous adresse ma candidature au poste de [poste] au sein de [entreprise].\n\n[Ce que tu as fait : formation, expériences, résultats concrets en lien avec le poste.]\n\n[Pourquoi cette entreprise, et ce que tu peux lui apporter.]\n\nJe me tiens à votre disposition pour un entretien afin de vous exposer plus en détail ma motivation." },
  en: { salutation: "Dear Sir or Madam,",
        formule: "Yours faithfully,",
        trame: "I am writing to apply for the position of [position] at [company].\n\n[What you have done: education, experience, concrete results relevant to the role.]\n\n[Why this company, and what you can bring to it.]\n\nI would welcome the opportunity to discuss my application with you in an interview." },
  it: { salutation: "Gentili Signori,",
        formule: "In attesa di un Vostro cortese riscontro, porgo distinti saluti.",
        trame: "Con la presente desidero candidarmi per la posizione di [posizione] presso [azienda].\n\n[Cosa hai fatto: formazione, esperienze, risultati concreti legati al ruolo.]\n\n[Perché questa azienda e cosa puoi offrirle.]\n\nResto a disposizione per un colloquio conoscitivo." },
};

function etatVide() {
  return {
    type: "cv",
    options: { modele: "moderne", langue: "fr", couleur: "1F4E79", certifier: false },
    cv: { nom: "", titre: "", email: "", telephone: "", ville: "", etat_civil: {}, profil: "",
          experiences: [{}], formations: [{}], competences: [{}], langues: [{}], liens: [],
          interets: "", references: [], photo: "" },
    lettre: { destinataire: {}, lieu: "", objet: "", corps: "", pj: "",
              date: new Date().toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }),
              salutation: TEXTES_LETTRE.fr.salutation, formule: TEXTES_LETTRE.fr.formule },
  };
}

/** Fusionne des données (import, sauvegarde) dans un état propre, sans faire confiance à leur forme. */
function fusionner(source) {
  const e = etatVide();
  if (!source || typeof source !== "object") return e;
  // Un cv.json « nu » (sans enveloppe cv/lettre/options) est accepté aussi.
  const s = source.cv || source.lettre || source.options ? source : { cv: source };
  if (s.type === "lettre") e.type = "lettre";
  for (const bloc of ["options", "cv", "lettre"]) {
    if (s[bloc] && typeof s[bloc] === "object") Object.assign(e[bloc], s[bloc]);
  }
  // Un champ texte doit rester une chaîne, quoi que contienne le fichier importé.
  const modele = etatVide();
  for (const bloc of ["cv", "lettre", "options"]) {
    for (const [cle, defaut] of Object.entries(modele[bloc])) {
      if (typeof defaut === "string" && typeof e[bloc][cle] !== "string") e[bloc][cle] = defaut;
    }
  }
  if (!MODELES.includes(e.options.modele)) e.options.modele = modele.options.modele;
  for (const nom of Object.keys(LISTES)) {
    const liste = Array.isArray(e.cv[nom]) ? e.cv[nom] : [];
    e.cv[nom] = liste.filter(x => x && typeof x === "object").map(x => ({ ...x }));
  }
  // « points » peut arriver sous forme de tableau : la zone de texte veut une chaîne.
  for (const exp of e.cv.experiences) {
    if (Array.isArray(exp.points)) exp.points = exp.points.join("\n");
  }
  for (const [bloc, cle] of [["cv", "etat_civil"], ["lettre", "destinataire"]]) {
    const v = e[bloc][cle];
    e[bloc][cle] = v && typeof v === "object" && !Array.isArray(v) ? { ...v } : {};
  }
  return e;
}

let etat = etatVide();
try { etat = fusionner(JSON.parse(localStorage.getItem(CLE_STOCKAGE))); } catch { /* stockage indisponible */ }

// Arrivée depuis la page d'accueil : /creer?modele=africain ou /creer?type=lettre
const parametres = new URLSearchParams(location.search);
if (MODELES.includes(parametres.get("modele"))) { etat.options.modele = parametres.get("modele"); etat.type = "cv"; }
if (parametres.get("type") === "lettre") etat.type = "lettre";

const $ = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];

function sauvegarder() {
  try { localStorage.setItem(CLE_STOCKAGE, JSON.stringify(etat)); } catch { /* quota ou navigation privée */ }
}

// ---------- Champs simples : data-k="cv.etat_civil.permis" ----------
function lire(chemin) {
  return chemin.split(".").reduce((o, c) => (o == null ? undefined : o[c]), etat);
}
function ecrire(chemin, valeur) {
  const cles = chemin.split(".");
  const derniere = cles.pop();
  let o = etat;
  for (const c of cles) o = o[c] ??= {};
  o[derniere] = valeur;
}
function remplirChamps() {
  for (const el of $$("[data-k]")) {
    const v = lire(el.dataset.k);
    if (el.type === "checkbox") el.checked = v === true;
    else if (el.type === "radio") el.checked = v === el.value;
    else el.value = v ?? "";
  }
}

// ---------- Listes répétables ----------
function dessinerListe(nom) {
  const conteneur = $(`[data-liste="${nom}"]`);
  const { nom: libelle, champs } = LISTES[nom];
  const elements = etat.cv[nom];
  conteneur.replaceChildren();

  elements.forEach((element, i) => {
    const carte = document.createElement("div");
    carte.className = "element";

    const tete = document.createElement("div");
    tete.className = "element-tete";
    const titre = document.createElement("span");
    titre.textContent = `${libelle} ${i + 1}`;
    const actions = document.createElement("div");
    const bouton = (texte, aide, action, inactif) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "mini"; b.textContent = texte;
      b.title = aide; b.setAttribute("aria-label", `${aide} : ${libelle} ${i + 1}`);
      b.disabled = !!inactif;
      b.addEventListener("click", () => { action(); dessinerListe(nom); modifie(); });
      actions.append(b);
    };
    bouton("↑", "Monter", () => elements.splice(i - 1, 0, elements.splice(i, 1)[0]), i === 0);
    bouton("↓", "Descendre", () => elements.splice(i + 1, 0, elements.splice(i, 1)[0]), i === elements.length - 1);
    bouton("Supprimer", "Supprimer", () => elements.splice(i, 1));
    tete.append(titre, actions);

    const grille = document.createElement("div");
    grille.className = "grille";
    for (const [champ, texte, type] of champs) {
      const label = document.createElement("label");
      label.textContent = texte;
      const saisie = document.createElement(type === "textarea" ? "textarea" : "input");
      if (type === "textarea") { saisie.rows = 4; label.className = "large"; }
      saisie.maxLength = type === "textarea" ? 3000 : 300;
      saisie.value = element[champ] ?? "";
      saisie.addEventListener("input", () => { element[champ] = saisie.value; modifie(); });
      label.append(saisie);
      grille.append(label);
    }
    carte.append(tete, grille);
    conteneur.append(carte);
  });

  const ajouter = document.createElement("button");
  ajouter.type = "button"; ajouter.className = "ajouter";
  ajouter.textContent = `+ Ajouter : ${libelle.toLowerCase()}`;
  ajouter.addEventListener("click", () => {
    elements.push({});
    dessinerListe(nom);
    conteneur.querySelector(".element:last-of-type input")?.focus();
  });
  conteneur.append(ajouter);
}

// ---------- Photo : réduite dans le navigateur avant envoi ----------
function afficherPhoto() {
  const img = $("#photo-apercu");
  img.hidden = !etat.cv.photo;
  $("#photo-retirer").hidden = !etat.cv.photo;
  if (etat.cv.photo) img.src = etat.cv.photo;
}
$("#photo-fichier").addEventListener("change", async e => {
  const fichier = e.target.files[0];
  e.target.value = "";
  if (!fichier) return;
  try {
    const image = await createImageBitmap(fichier);
    const echelle = Math.min(1, 600 / Math.max(image.width, image.height));
    const toile = document.createElement("canvas");
    toile.width = Math.round(image.width * echelle);
    toile.height = Math.round(image.height * echelle);
    const ctx = toile.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, toile.width, toile.height);
    ctx.drawImage(image, 0, 0, toile.width, toile.height);
    etat.cv.photo = toile.toDataURL("image/jpeg", 0.88);
    afficherPhoto(); modifie();
  } catch {
    montrerErreur("Impossible de lire cette image (JPEG ou PNG uniquement).");
  }
});
$("#photo-retirer").addEventListener("click", () => { etat.cv.photo = ""; afficherPhoto(); modifie(); });

// ---------- Onglets CV / Lettre, conseil, jauge ----------
function afficherType() {
  for (const b of $$(".onglets button")) b.setAttribute("aria-selected", String(b.dataset.type === etat.type));
  for (const el of $$(".pour-cv")) el.hidden = etat.type !== "cv";
  for (const el of $$(".pour-lettre")) el.hidden = etat.type !== "lettre";
  $("#conseil-modele").textContent = CONSEILS[etat.options.modele] || "";
  afficherProgres();
}
for (const b of $$(".onglets button")) {
  b.addEventListener("click", () => { etat.type = b.dataset.type; afficherType(); modifie(0); });
}

function afficherProgres() {
  const manquants = ETAPES.filter(([test]) => !test(etat.cv)).map(([, conseil]) => conseil);
  const pourcent = Math.round(100 * (ETAPES.length - manquants.length) / ETAPES.length);
  $("#progres").value = pourcent;
  $("#progres-texte").textContent = manquants.length
    ? `CV complété à ${pourcent} % · ajoute ${manquants[0]}`
    : "CV complet, prêt à être envoyé";
}

// ---------- Aperçu et téléchargement ----------
let minuterie = null;
let requete = null;

function montrerErreur(message) {
  const p = $("#erreur");
  p.textContent = message || "";
  p.hidden = !message;
}

function pret() {
  if (!etat.cv.nom.trim()) return "Renseigne ton nom pour voir l'aperçu.";
  if (etat.type === "lettre" && !etat.lettre.corps.trim()) return "Écris le texte de la lettre pour voir l'aperçu.";
  return "";
}

async function appeler(chemin, signal) {
  let reponse;
  try {
    reponse = await fetch(chemin, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: etat.type, cv: etat.cv, lettre: etat.lettre, options: etat.options }),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new Error("Connexion impossible. Vérifie ta connexion Internet et réessaie.");
  }
  if (!reponse.ok) {
    const detail = await reponse.json().catch(() => ({}));
    throw new Error(detail.erreur || `Erreur ${reponse.status}`);
  }
  return reponse;
}

async function actualiser() {
  clearTimeout(minuterie);
  const manque = pret();
  $("#btn-telecharger").disabled = !!manque;
  $("#btn-ouvrir").disabled = !!manque;
  $("#btn-partager").disabled = !!manque;
  $("#btn-telecharger-bas").disabled = !!manque;
  if (manque) {
    $("#etat").textContent = manque;
    $("#vide").hidden = false;
    $("#pages").replaceChildren();
    return;
  }
  requete?.abort();
  requete = new AbortController();
  $("#etat").textContent = "Mise à jour…";
  try {
    const { pages } = await (await appeler("/api/apercu", requete.signal)).json();
    const images = pages.map((src, i) => {
      const img = document.createElement("img");
      img.src = src;
      img.alt = `Aperçu, page ${i + 1}`;
      return img;
    });
    $("#pages").replaceChildren(...images);
    $("#vide").hidden = true;
    montrerErreur("");
    $("#etat").textContent = pages.length > 1 ? `${pages.length} pages` : "1 page";
  } catch (e) {
    if (e.name === "AbortError") return;
    $("#etat").textContent = "";
    montrerErreur(e.message);
  }
}

async function telecharger(bouton) {
  const texte = bouton.textContent;
  bouton.disabled = true;
  bouton.textContent = "Préparation…";
  try {
    const pdf = await (await appeler("/api/pdf")).blob();
    const nom = etat.cv.nom.trim().normalize("NFD").replace(/[^A-Za-z0-9]+/g, "_").replace(/^_|_$/g, "");
    const lien = document.createElement("a");
    lien.href = URL.createObjectURL(pdf);
    lien.download = `${etat.type === "lettre" ? "Lettre" : "CV"}_${nom || "document"}.pdf`;
    document.body.append(lien);
    lien.click();
    lien.remove();
    setTimeout(() => URL.revokeObjectURL(lien.href), 10_000);
    montrerErreur("");
  } catch (e) {
    montrerErreur(e.message);
  } finally {
    bouton.textContent = texte;
    bouton.disabled = false;
  }
}
function nomFichier() {
  const nom = etat.cv.nom.trim().normalize("NFD").replace(/[^A-Za-z0-9]+/g, "_").replace(/^_|_$/g, "");
  return `${etat.type === "lettre" ? "Lettre" : "CV"}_${nom || "document"}.pdf`;
}

/** Partage le PDF vers une autre application (WhatsApp, e-mail…), surtout utile sur téléphone. */
const partagePossible = (() => {
  try {
    return !!navigator.canShare && navigator.canShare({
      files: [new File([""], "essai.pdf", { type: "application/pdf" })] });
  } catch { return false; }
})();
$("#btn-partager").hidden = !partagePossible;

async function partager(bouton) {
  bouton.disabled = true;
  try {
    const pdf = await (await appeler("/api/pdf")).blob();
    const fichier = new File([pdf], nomFichier(), { type: "application/pdf" });
    await navigator.share({ files: [fichier], title: fichier.name });
    montrerErreur("");
  } catch (e) {
    // AbortError : la personne a fermé le menu de partage, ce n'est pas une erreur.
    if (e.name === "NotAllowedError") montrerErreur("Le partage a été bloqué par le navigateur. Utilise « Télécharger le PDF ».");
    else if (e.name !== "AbortError") montrerErreur(e.message);
  } finally {
    bouton.disabled = false;
  }
}
$("#btn-partager").addEventListener("click", e => partager(e.currentTarget));

/** Ouvre le PDF dans un nouvel onglet (au lieu de le télécharger). */
async function ouvrir(bouton) {
  // L'onglet est ouvert TOUT DE SUITE, pendant le clic : ouvert plus tard (après la
  // réponse du serveur), le navigateur le bloquerait comme une fenêtre publicitaire.
  const onglet = window.open("", "_blank");
  bouton.disabled = true;
  try {
    const pdf = await (await appeler("/api/pdf")).blob();
    const url = URL.createObjectURL(pdf);
    if (onglet) onglet.location = url;
    else location.href = url;          // fenêtres bloquées : on ouvre dans l'onglet actuel
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    montrerErreur("");
  } catch (e) {
    onglet?.close();
    montrerErreur(e.message);
  } finally {
    bouton.disabled = false;
  }
}
$("#btn-ouvrir").addEventListener("click", e => ouvrir(e.currentTarget));
$("#btn-telecharger").addEventListener("click", e => telecharger(e.currentTarget));
$("#btn-telecharger-bas").addEventListener("click", e => telecharger(e.currentTarget));
$("#btn-voir").addEventListener("click", () => $("#apercu").scrollIntoView({ behavior: "smooth" }));

/** À appeler après chaque modification : sauvegarde, puis aperçu différé (on attend la fin de la frappe). */
function modifie(delai = 1200) {
  sauvegarder();
  afficherProgres();
  clearTimeout(minuterie);
  minuterie = setTimeout(actualiser, delai);
}

$("#formulaire").addEventListener("input", e => {
  const el = e.target;
  if (!el.dataset.k) return;   // les champs des listes ont leur propre écouteur
  ecrire(el.dataset.k, el.type === "checkbox" ? el.checked : el.value);
  if (el.dataset.k === "options.modele") afficherType();
  if (el.dataset.k === "options.langue") adapterLangueLettre(el.value);
  const immediat = el.tagName === "SELECT" || el.type === "checkbox" || el.type === "radio";
  modifie(immediat ? 0 : 1200);
});
$("#formulaire").addEventListener("submit", e => e.preventDefault());

/** Change les formules de la lettre avec la langue, sauf si l'utilisateur les a personnalisées. */
function adapterLangueLettre(langue) {
  const standards = Object.values(TEXTES_LETTRE);
  for (const champ of ["salutation", "formule"]) {
    const actuel = etat.lettre[champ] || "";
    if (!actuel || standards.some(t => t[champ] === actuel)) etat.lettre[champ] = TEXTES_LETTRE[langue][champ];
  }
  remplirChamps();
}

$("#btn-trame").addEventListener("click", () => {
  if (etat.lettre.corps.trim() && !confirm("Remplacer le texte actuel de la lettre par la trame ?")) return;
  etat.lettre.corps = (TEXTES_LETTRE[etat.options.langue] || TEXTES_LETTRE.fr).trame;
  remplirChamps(); modifie();
});

// ---------- Exemple / export / import / vider ----------
function toutDessiner() {
  remplirChamps();
  Object.keys(LISTES).forEach(dessinerListe);
  afficherPhoto();
  afficherType();
}

async function chargerExemple() {
  if (etat.cv.nom.trim() && !confirm("Remplacer tes données par l'exemple ?")) return;
  try {
    const exemple = await (await fetch("exemple.json")).json();
    etat = fusionner({ type: etat.type, options: etat.options, lettre: etat.lettre, cv: exemple });
    toutDessiner(); modifie(0);
  } catch {
    montrerErreur("Impossible de charger l'exemple.");
  }
}
$("#btn-exemple").addEventListener("click", chargerExemple);
$("#btn-exemple-vide").addEventListener("click", chargerExemple);

$("#btn-vider").addEventListener("click", () => {
  if (!confirm("Effacer toutes les données du formulaire ?")) return;
  etat = etatVide();
  toutDessiner(); sauvegarder(); actualiser();
});

$("#btn-exporter").addEventListener("click", () => {
  const lien = document.createElement("a");
  lien.href = URL.createObjectURL(new Blob([JSON.stringify(etat, null, 2)], { type: "application/json" }));
  lien.download = "mes-donnees-cv.json";
  lien.click();
  setTimeout(() => URL.revokeObjectURL(lien.href), 1000);
});

$("#btn-importer").addEventListener("change", async e => {
  const fichier = e.target.files[0];
  e.target.value = "";
  if (!fichier) return;
  try {
    etat = fusionner(JSON.parse(await fichier.text()));
    toutDessiner(); modifie(0);
  } catch {
    montrerErreur("Ce fichier n'est pas un export valide (JSON attendu).");
  }
});

toutDessiner();
// Arrivée par « Voir un exemple » : on charge l'exemple, sauf si un CV est déjà en cours.
if (parametres.get("exemple") === "1" && !etat.cv.nom.trim()) chargerExemple();
else actualiser();
