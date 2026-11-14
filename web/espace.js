"use strict";
/*
 * « Mon espace » (/compte) : connexion ou création de compte, puis le profil du visiteur :
 * ses documents, les dates de son compte, le changement de mot de passe, ses données.
 * Tout passe par /api/compte… ; le serveur ne renvoie jamais que les données du compte connecté.
 */
const CLE_STOCKAGE = "cv-saas-v1", CLE_SYNCHRO = "cv-saas-synchro";   // les mêmes que l'éditeur (app.js)
const $ = sel => document.querySelector(sel);
const MODELES = { moderne: "Moderne", classique: "Classique", europeen: "Européen", africain: "Africain", epure: "Épurée" };
const PARTIES = ["accroche", "parcours", "entreprise", "conclusion"];
let donnees = null;          // le CV enregistré dans le compte
let inscription = false;

async function api(methode, chemin, corps) {
  try {
    const r = await fetch("/api/compte" + chemin, {
      method: methode, credentials: "same-origin",
      headers: corps ? { "Content-Type": "application/json" } : {},
      body: corps ? JSON.stringify(corps) : undefined,
    });
    return { ok: r.ok, statut: r.status, corps: await r.json().catch(() => ({})) };
  } catch {
    return { ok: false, statut: 0, corps: { erreur: "Connexion impossible. Vérifie ta connexion Internet." } };
  }
}
function erreur(sel, message) { $(sel).textContent = message || ""; $(sel).hidden = !message; }
function message(texte) { $("#espace-message").textContent = texte || ""; $("#espace-message").hidden = !texte; }
function date(secondes, avecHeure) {
  if (!secondes) return "—";
  const d = new Date(secondes * 1000);
  // Dans la langue du site (définie par traductions.js) : « 14 novembre 2026 à 20:53 », « 14 November 2026 at 20:53 »…
  return d.toLocaleString(window.LOCALE_SITE || "fr-FR", avecHeure
    ? { dateStyle: "long", timeStyle: "short" } : { dateStyle: "long" });
}
const texte = v => (typeof v === "string" ? v.trim() : "");
const liste = v => (Array.isArray(v) ? v.filter(x => x && typeof x === "object") : []);

// ---------- Porte d'entrée : connexion / création de compte ----------
function afficherPorte() {
  $("#porte-titre").textContent = inscription ? "Créer un compte" : "Se connecter";
  $("#porte-valider").textContent = inscription ? "Créer mon compte" : "Se connecter";
  $("#porte-question").textContent = inscription ? "Déjà un compte ?" : "Pas encore de compte ?";
  $("#porte-bascule").textContent = inscription ? "Se connecter" : "Créer un compte";
  $("#porte-aide").hidden = !inscription;
  $("#porte-info").hidden = !inscription;
  $("#porte-mdp").autocomplete = inscription ? "new-password" : "current-password";
}
$("#porte-bascule").addEventListener("click", () => { inscription = !inscription; erreur("#porte-erreur", ""); afficherPorte(); });
$("#porte-form").addEventListener("submit", async e => {
  e.preventDefault();
  const email = $("#porte-email").value.trim(), mdp = $("#porte-mdp").value;
  if (!email || !mdp) return erreur("#porte-erreur", "Renseigne ton adresse e-mail et ton mot de passe.");
  if (inscription && mdp.length < 8) return erreur("#porte-erreur", "Le mot de passe doit contenir au moins 8 caractères.");
  $("#porte-valider").disabled = true;
  const r = await api("POST", inscription ? "/inscription" : "/connexion", { email, mot_de_passe: mdp });
  $("#porte-valider").disabled = false;
  $("#porte-mdp").value = "";
  if (!r.ok) return erreur("#porte-erreur", r.corps.erreur || "Une erreur est survenue. Réessaie.");
  // Autre compte possible sur cet appareil : l'éditeur refera le point à sa prochaine ouverture.
  try { localStorage.removeItem(CLE_SYNCHRO); } catch { /* stockage indisponible */ }
  await envoyerLocal();        // un CV déjà commencé sur cet appareil rejoint le compte s'il est vide
  await charger();
});

/** Si le compte n'a encore aucun CV et que cet appareil en contient un, on l'y enregistre. */
async function envoyerLocal() {
  let local;
  try { local = JSON.parse(localStorage.getItem(CLE_STOCKAGE)); } catch { return; }
  if (!local || !local.cv || !texte(local.cv.nom)) return;
  const r = await api("GET", "/donnees");
  if (!r.ok || r.corps.donnees) return;
  const e = await api("PUT", "/donnees", { donnees: local });
  if (e.ok) { try { localStorage.setItem(CLE_SYNCHRO, JSON.stringify({ vu: e.corps.modifie_le, sale: false })); } catch { /* idem */ } }
}

// ---------- Espace du visiteur connecté ----------
function progression(cv) {
  const etapes = [texte(cv.nom), texte(cv.titre), texte(cv.email), texte(cv.telephone), texte(cv.ville), texte(cv.profil),
    liste(cv.experiences).some(x => texte(x.poste) || texte(x.entreprise)),
    liste(cv.formations).some(x => texte(x.diplome) || texte(x.ecole)),
    liste(cv.competences).some(x => texte(x.liste)),
    liste(cv.langues).some(x => texte(x.langue))];
  return Math.round(100 * etapes.filter(Boolean).length / etapes.length);
}
function texteLettre(lettre) {
  const parties = lettre && typeof lettre.parties === "object" && lettre.parties ? lettre.parties : {};
  return PARTIES.map(c => texte(parties[c])).filter(Boolean).join("\n") || texte(lettre && lettre.corps);
}

function afficherEspace(compte) {
  $("#espace-initiale").textContent = compte.email[0].toUpperCase();
  $("#espace-email").textContent = compte.email;
  $("#profil-email").textContent = compte.email;
  $("#profil-cree").textContent = date(compte.cree_le);
  $("#profil-vu").textContent = date(compte.vu_le, true);

  const cv = donnees && typeof donnees.cv === "object" && donnees.cv ? donnees.cv : null;
  const aUnCv = !!(cv && texte(cv.nom));
  $("#docs").hidden = !aUnCv;
  $("#docs-vide").hidden = aUnCv;
  $("#docs-creer").hidden = aUnCv;
  $("#docs-date").textContent = aUnCv ? `Dernier enregistrement : ${date(compte.modifie_le, true)}` : "";
  $("#donnees-copie").disabled = !donnees;
  if (!aUnCv) return;

  const options = donnees.options && typeof donnees.options === "object" ? donnees.options : {};
  const lettre = donnees.lettre && typeof donnees.lettre === "object" ? donnees.lettre : {};
  $("#cv-modele").textContent = "Modèle " + (MODELES[options.modele] || "Classique");
  $("#cv-resume").textContent = [texte(cv.nom), texte(cv.titre)].filter(Boolean).join(" · ");
  const pourcent = progression(cv);
  $("#cv-progres").value = pourcent;
  $("#cv-progres-texte").textContent = pourcent === 100 ? "CV complet" : `Complété à ${pourcent} %`;

  const corps = texteLettre(lettre);
  const mots = corps.split(/\s+/).filter(Boolean).length;
  $("#lettre-modele").textContent = corps ? "Modèle " + (MODELES[options.modele_lettre] || "Classique") : "";
  $("#lettre-resume").textContent = corps
    ? [texte(lettre.objet) || "Sans objet", `${mots} mots`].join(" · ")
    : "Pas encore écrite.";
  $("#lettre-pdf").disabled = !corps;
}

async function charger() {
  const r = await api("GET", "");
  $("#espace-attente").hidden = true;
  const connecte = r.ok && r.corps.connecte;
  $("#espace-porte").hidden = connecte;
  $("#espace").hidden = !connecte;
  if (!connecte) { afficherPorte(); return; }
  const d = await api("GET", "/donnees");
  donnees = d.ok ? d.corps.donnees : null;
  afficherEspace(r.corps);
  // L'en-tête (entete.js) a pu être dessiné avant la connexion : on le met à jour.
  for (const lien of document.querySelectorAll("a[data-lien-compte]")) {
    const pastille = document.createElement("span");
    pastille.className = "initiale";
    pastille.textContent = r.corps.email[0].toUpperCase();
    lien.replaceChildren(pastille, "Mon espace");
  }
}

// ---------- Télécharger un PDF directement depuis l'espace ----------
async function telechargerPdf(type, bouton) {
  bouton.disabled = true; message("");
  try {
    const r = await fetch("/api/pdf", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, cv: donnees.cv, lettre: donnees.lettre, options: donnees.options }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).erreur || "La génération du document a échoué.");
    enregistrer(await r.blob(), `${type === "lettre" ? "Lettre" : "CV"}_${texte(donnees.cv.nom).replace(/[^A-Za-z0-9]+/g, "_") || "document"}.pdf`);
  } catch (e) { message(e.message); }
  bouton.disabled = false;
}
function enregistrer(blob, nom) {
  const lien = document.createElement("a");
  lien.href = URL.createObjectURL(blob);
  lien.download = nom;
  document.body.append(lien); lien.click(); lien.remove();
  setTimeout(() => URL.revokeObjectURL(lien.href), 1000);
}
$("#cv-pdf").addEventListener("click", e => telechargerPdf("cv", e.currentTarget));
$("#lettre-pdf").addEventListener("click", e => telechargerPdf("lettre", e.currentTarget));
$("#donnees-copie").addEventListener("click", () => {
  enregistrer(new Blob([JSON.stringify(donnees, null, 2)], { type: "application/json" }), "mes-donnees-cv.json");
});

// ---------- Mot de passe, déconnexion, suppression ----------
$("#mdp-form").addEventListener("submit", async e => {
  e.preventDefault();
  const ancien = $("#mdp-ancien").value, nouveau = $("#mdp-nouveau").value;
  if (!ancien || !nouveau) return erreur("#mdp-erreur", "Renseigne les deux mots de passe.");
  if (nouveau.length < 8) return erreur("#mdp-erreur", "Le nouveau mot de passe doit contenir au moins 8 caractères.");
  $("#mdp-valider").disabled = true;
  const r = await api("POST", "/mot-de-passe", { ancien, nouveau });
  $("#mdp-valider").disabled = false;
  if (!r.ok) return erreur("#mdp-erreur", r.corps.erreur || "Changement impossible. Réessaie.");
  $("#mdp-form").reset(); erreur("#mdp-erreur", "");
  message("Ton mot de passe a été changé. Tes autres appareils ont été déconnectés.");
});

$("#espace-deconnexion").addEventListener("click", async () => {
  // Des modifications faites dans l'éditeur et pas encore envoyées ? On les enregistre d'abord.
  try {
    const s = JSON.parse(localStorage.getItem(CLE_SYNCHRO)) || {};
    const local = JSON.parse(localStorage.getItem(CLE_STOCKAGE));
    if (s.sale && local) await api("PUT", "/donnees", { donnees: local });
  } catch { /* rien à envoyer */ }
  await api("POST", "/deconnexion", {});
  // Ordinateur partagé : après la déconnexion, il ne reste rien sur cet appareil.
  try { localStorage.removeItem(CLE_STOCKAGE); localStorage.removeItem(CLE_SYNCHRO); } catch { /* idem */ }
  location.href = (window.PREFIXE_SITE || "") + "/";
});

$("#suppr-valider").addEventListener("click", async () => {
  const mdp = $("#suppr-mdp").value;
  if (!mdp) return erreur("#suppr-erreur", "Saisis ton mot de passe pour confirmer.");
  const r = await api("DELETE", "", { mot_de_passe: mdp });
  $("#suppr-mdp").value = "";
  if (!r.ok) return erreur("#suppr-erreur", r.corps.erreur || "Suppression impossible. Réessaie.");
  try { localStorage.removeItem(CLE_SYNCHRO); } catch { /* idem */ }
  alert("Ton compte et les documents enregistrés sur nos serveurs ont été supprimés.");
  location.href = (window.PREFIXE_SITE || "") + "/";
});

charger();
