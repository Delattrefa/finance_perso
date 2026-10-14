/* PyFinance — comportements communs (sans framework). */
(function () {
  "use strict";

  // Icônes Lucide : au chargement et après chaque échange HTMX
  function icones() { if (window.lucide) window.lucide.createIcons(); }

  // Affichage conditionnel : data-afficher-si="type:VIREMENT_INTERNE" ou "holding:" (vide)
  // ou négation "!type:VIREMENT_INTERNE". Plusieurs valeurs séparées par des virgules.
  function valeurChamp(form, nom) {
    const champs = form.querySelectorAll(`[name="${nom}"]`);
    if (!champs.length) return "";
    if (champs[0].type === "radio") {
      const coche = form.querySelector(`[name="${nom}"]:checked`);
      return coche ? coche.value : "";
    }
    if (champs[0].type === "checkbox") return champs[0].checked ? "on" : "";
    return champs[0].value;
  }
  function conditions(racine) {
    racine.querySelectorAll("form").forEach(function (form) {
      form.querySelectorAll("[data-afficher-si]").forEach(function (el) {
        let regle = el.dataset.afficherSi, negation = regle.startsWith("!");
        if (negation) regle = regle.slice(1);
        const [nom, valeurs] = regle.split(":");
        let visible = valeurs.split(",").includes(valeurChamp(form, nom));
        if (negation) visible = !visible;
        el.hidden = !visible;
        el.querySelectorAll("input, select, textarea").forEach((c) => (c.disabled = !visible));
      });
    });
  }

  // Aperçu de l'impact d'une opération sur titre : data-apercu-titre sur le formulaire
  function nombre(v) { return parseFloat(String(v || "0").replace(/\s/g, "").replace(",", ".")) || 0; }
  function apercuTitre(racine) {
    racine.querySelectorAll("form[data-apercu-titre]").forEach(function (form) {
      const sortie = form.querySelector("[data-apercu-sortie]");
      if (!sortie) return;
      const q = nombre(form.elements.quantite && form.elements.quantite.value);
      const p = nombre(form.elements.prix_unitaire && form.elements.prix_unitaire.value);
      const f = nombre(form.elements.frais && form.elements.frais.value);
      const type = valeurChamp(form, "type_operation");
      const brut = q * p, net = type === "ACHAT" ? -(brut + f) : brut - f;
      const fmt = new Intl.NumberFormat("fr-FR", { style: "currency", currency: sortie.dataset.devise || "EUR" });
      sortie.textContent = (net > 0 ? "+" : "") + fmt.format(net);
      sortie.className = net < 0 ? "text-rose-600 dark:text-rose-400" : "text-emerald-600 dark:text-emerald-400";
    });
  }

  // Groupes repliables de la matrice budgétaire (état mémorisé localement)
  function groupes(racine) {
    racine.querySelectorAll("[data-groupe]").forEach(function (corps) {
      const cle = "pf-groupe-" + corps.dataset.groupe;
      try { if (localStorage.getItem(cle) === "1") corps.classList.add("groupe-replie"); } catch (e) {}
    });
  }
  document.addEventListener("click", function (e) {
    const bouton = e.target.closest("[data-basculer-groupe]");
    if (!bouton) return;
    const corps = bouton.closest("[data-groupe]");
    corps.classList.toggle("groupe-replie");
    try { localStorage.setItem("pf-groupe-" + corps.dataset.groupe, corps.classList.contains("groupe-replie") ? "1" : "0"); } catch (e) {}
  });

  // Messages : fermeture manuelle et automatique
  function messages(racine) {
    racine.querySelectorAll("[data-message]").forEach(function (m) {
      setTimeout(() => m.remove(), m.dataset.message === "error" ? 9000 : 5000);
    });
  }
  document.addEventListener("click", function (e) {
    const fermer = e.target.closest("[data-fermer]");
    if (fermer) fermer.closest("[data-message], [data-fermable]").remove();
  });

  // Menu mobile
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-menu-ouvrir]")) document.getElementById("menu").classList.remove("-translate-x-full");
    if (e.target.closest("[data-menu-fermer]")) document.getElementById("menu").classList.add("-translate-x-full");
  });

  // Raccourcis : « n » nouvelle opération, « / » recherche, Ctrl+Entrée soumet le formulaire
  document.addEventListener("keydown", function (e) {
    const saisie = /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName);
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && saisie) {
      const form = document.activeElement.form;
      if (form) { e.preventDefault(); form.requestSubmit(); }
      return;
    }
    if (saisie || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "n" && document.body.dataset.urlSaisie) window.location = document.body.dataset.urlSaisie;
    if (e.key === "/" && document.body.dataset.urlRecherche) { e.preventDefault(); window.location = document.body.dataset.urlRecherche; }
  });

  // Thème : bascule clair / sombre mémorisée pour la session (le profil fixe la valeur par défaut)
  document.addEventListener("click", function (e) {
    if (!e.target.closest("[data-basculer-theme]")) return;
    const sombre = document.documentElement.classList.toggle("dark");
    try { localStorage.setItem("pf-theme", sombre ? "SOMBRE" : "CLAIR"); } catch (e) {}
  });

  function initialiser(racine) { icones(); conditions(racine); apercuTitre(racine); groupes(racine); messages(racine); }
  document.addEventListener("DOMContentLoaded", () => initialiser(document));
  document.addEventListener("htmx:afterSettle", () => initialiser(document));
  document.addEventListener("change", (e) => { if (e.target.form) { conditions(e.target.form.parentNode); apercuTitre(e.target.form.parentNode); } });
  document.addEventListener("input", (e) => { if (e.target.form) apercuTitre(e.target.form.parentNode); });
})();
