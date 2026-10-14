/*
 * PyFinance — graphiques Chart.js.
 *
 * Chaque graphique est déclaré dans le HTML :
 *   <div data-graphique="flux" data-source="evolution" data-titre="…"><canvas></canvas></div>
 * et lit ses données dans le JSON <script id="donnees-graphiques"> fourni par la vue
 * (dict `graphiques`, sérialisé par json_script). `data-source` est un chemin pointé.
 *
 * Règles (méthode dataviz) : palette catégorielle validée dans un ordre fixe, un seul axe,
 * barres ≤ 24 px aux extrémités arrondies (base carrée), lignes de 2 px, grilles en filet
 * plein discret, texte jamais coloré par la série, info-bulle au survol, thème sombre
 * avec ses propres teintes (validées sur la surface sombre).
 */
(function () {
  "use strict";

  // --- Thèmes : surfaces = fond des cartes (blanc / slate-900) -----------------------------
  const THEMES = {
    clair: {
      series: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
      surface: "#ffffff", grille: "#e1e0d9", axe: "#c3c2b7", texte: "#52514e", muet: "#898781",
      reference: "#c3c2b7", autres: "#c3c2b7", negatif: "#e34948", encre: "#0b0b0b",
    },
    sombre: {
      series: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
      surface: "#0f172a", grille: "#1e293b", axe: "#334155", texte: "#cbd5e1", muet: "#94a3b8",
      reference: "#475569", autres: "#475569", negatif: "#e66767", encre: "#f1f5f9",
    },
  };
  const ORDRE_TYPES_COMPTE = ["COURANT", "EPARGNE", "TITRE", "AUTRE"]; // couleur = type, jamais le rang
  const MAX_SEGMENTS = 6; // donut : 5 postes + « Autres »

  const theme = () => (document.documentElement.classList.contains("dark") ? THEMES.sombre : THEMES.clair);
  const graphiques = [];
  let donnees = {};

  // --- Formatage --------------------------------------------------------------------------
  function formateurs(devise) {
    const complet = new Intl.NumberFormat("fr-FR", { style: "currency", currency: devise, maximumFractionDigits: 0 });
    const compact = new Intl.NumberFormat("fr-FR", { style: "currency", currency: devise, notation: "compact", maximumFractionDigits: 1 });
    const pct = new Intl.NumberFormat("fr-FR", { style: "percent", maximumFractionDigits: 1 });
    return {
      montant: (v) => (v === null || v === undefined ? "—" : complet.format(v)),
      signe: (v) => (v > 0 ? "+" : "") + complet.format(v),
      axe: (v) => (Math.abs(v) >= 1000 ? compact.format(v) : complet.format(v)),
      pct: (v) => pct.format(v),
    };
  }

  function chemin(objet, cle) {
    if (!cle) return objet; // source vide : racine des données
    return cle.split(".").reduce((o, k) => (o == null ? undefined : o[k]), objet);
  }

  // --- Plugins ----------------------------------------------------------------------------
  // Réticule vertical sur les courbes (accompagne l'info-bulle en mode « index »).
  const reticule = {
    id: "reticule",
    afterDatasetsDraw(chart, _args, options) {
      if (!options || !options.actif) return;
      const actifs = chart.tooltip && chart.tooltip.getActiveElements();
      if (!actifs || !actifs.length) return;
      const { ctx, chartArea } = chart;
      const x = actifs[0].element.x;
      ctx.save();
      ctx.strokeStyle = theme().axe;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, chartArea.top);
      ctx.lineTo(x, chartArea.bottom);
      ctx.stroke();
      ctx.restore();
    },
  };

  // Étiquette directe sur le dernier point d'une courbe (encre de texte, pas la couleur de série).
  const etiquetteFinale = {
    id: "etiquetteFinale",
    afterDatasetsDraw(chart, _args, options) {
      if (!options || !options.formater) return;
      const { ctx } = chart;
      chart.data.datasets.forEach((ds, i) => {
        const meta = chart.getDatasetMeta(i);
        if (meta.hidden || !meta.data.length) return;
        let j = ds.data.length - 1;
        while (j >= 0 && ds.data[j] == null) j--;
        if (j < 0) return;
        const point = meta.data[j];
        ctx.save();
        ctx.font = "600 12px Inter, system-ui, sans-serif";
        ctx.fillStyle = theme().encre;
        ctx.textBaseline = "middle";
        ctx.fillText(options.formater(ds.data[j]), point.x + 8, point.y);
        ctx.restore();
      });
    },
  };

  // Total au centre d'un donut.
  const centreDonut = {
    id: "centreDonut",
    afterDraw(chart, _args, options) {
      if (!options || !options.texte) return;
      const { ctx } = chart;
      const meta = chart.getDatasetMeta(0);
      if (!meta.data.length) return;
      const { x, y } = meta.data[0];
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = theme().muet;
      ctx.font = "500 12px Inter, system-ui, sans-serif";
      ctx.fillText(options.libelle || "Total", x, y - 11);
      ctx.fillStyle = theme().encre;
      ctx.font = "600 16px Inter, system-ui, sans-serif";
      ctx.fillText(options.texte, x, y + 9);
      ctx.restore();
    },
  };

  // --- Options communes -------------------------------------------------------------------
  function optionsBase(f, { legende = true, horizontal = false } = {}) {
    const t = theme();
    const axeValeur = {
      beginAtZero: true,
      border: { display: false },
      grid: { color: t.grille, lineWidth: 1, drawTicks: false },
      ticks: { color: t.muet, padding: 8, maxTicksLimit: 6, callback: (v) => f.axe(v) },
    };
    const axeCategorie = {
      border: { color: t.axe },
      grid: { display: false },
      ticks: { color: t.muet, padding: 6, autoSkipPadding: 12, maxRotation: 0, autoSkip: !horizontal },
    };
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      indexAxis: horizontal ? "y" : "x",
      interaction: { mode: "index", intersect: false },
      layout: { padding: { top: 4, right: 8 } },
      scales: horizontal ? { x: axeValeur, y: axeCategorie } : { x: axeCategorie, y: axeValeur },
      plugins: {
        legend: {
          display: legende,
          position: "top",
          align: "end",
          labels: { color: t.texte, usePointStyle: true, pointStyle: "circle", boxWidth: 8, boxHeight: 8, padding: 16 },
        },
        tooltip: {
          backgroundColor: t.surface === "#ffffff" ? "#0f172a" : "#f8fafc",
          titleColor: t.surface === "#ffffff" ? "#f8fafc" : "#0f172a",
          bodyColor: t.surface === "#ffffff" ? "#e2e8f0" : "#1e293b",
          footerColor: t.surface === "#ffffff" ? "#f8fafc" : "#0f172a",
          padding: 10,
          cornerRadius: 8,
          usePointStyle: true,
          boxPadding: 4,
          callbacks: { label: (c) => ` ${c.dataset.label} : ${f.montant(horizontal ? c.parsed.x : c.parsed.y)}` },
        },
      },
    };
  }

  function barre(couleur, extra = {}) {
    return {
      backgroundColor: couleur,
      hoverBackgroundColor: couleur,
      borderRadius: 4,
      borderSkipped: "start", // base carrée, extrémité arrondie
      maxBarThickness: 24,
      categoryPercentage: 0.72,
      barPercentage: 0.88,
      ...extra,
    };
  }

  function ligne(couleur, extra = {}) {
    return {
      borderColor: couleur,
      backgroundColor: couleur,
      borderWidth: 2,
      borderCapStyle: "round",
      borderJoinStyle: "round",
      tension: 0.25,
      pointRadius: 0,
      pointHoverRadius: 5,
      pointHoverBorderWidth: 2,
      pointHoverBorderColor: theme().surface, // anneau de surface
      pointHitRadius: 12,
      spanGaps: false,
      ...extra,
    };
  }

  function lavis(hex, alpha = 0.1) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  // --- Fabriques par type ----------------------------------------------------------------
  const FABRIQUES = {
    /** Revenus vs dépenses par mois : barres groupées (2 séries, légende). */
    flux(d, f) {
      const t = theme();
      const options = optionsBase(f);
      options.plugins.tooltip.callbacks.footer = (items) => {
        const r = items.find((i) => i.datasetIndex === 0), dp = items.find((i) => i.datasetIndex === 1);
        return r && dp ? `Épargne : ${f.signe(r.parsed.y - dp.parsed.y)}` : "";
      };
      return {
        type: "bar",
        data: {
          labels: d.libelles,
          datasets: [
            { label: "Revenus", data: d.revenus, ...barre(t.series[0]) },
            { label: "Dépenses", data: d.depenses, ...barre(t.series[1]) },
          ],
        },
        options,
      };
    },

    /** Tendance compacte (carte patrimoine) : une courbe, sans axes. */
    tendance(d, f) {
      const options = optionsBase(f, { legende: false });
      options.scales = { x: { display: false }, y: { display: false } };
      options.layout = { padding: 2 };
      options.plugins.tooltip.callbacks.label = (c) => ` ${f.montant(c.parsed.y)}`;
      return {
        type: "line",
        data: { labels: d.libelles, datasets: [ligne("rgba(255,255,255,0.9)", { data: d.total, label: "Patrimoine", pointHoverBorderColor: "#6366f1" })] },
        options,
      };
    },

    /** Une courbe principale avec lavis à 10 %, réticule et étiquette de fin. */
    courbe(d, f) {
      const t = theme();
      const options = optionsBase(f, { legende: false });
      options.scales.y.beginAtZero = false;
      options.layout.padding.right = 96;
      options.plugins.reticule = { actif: true };
      options.plugins.etiquetteFinale = { formater: f.montant };
      return {
        type: "line",
        data: {
          labels: d.libelles,
          datasets: [ligne(t.series[0], { label: d.libelle || "Total", data: d.total, fill: "origin", backgroundColor: lavis(t.series[0]) })],
        },
        options,
        plugins: [reticule, etiquetteFinale],
      };
    },

    /** Plusieurs courbes (types de compte) : couleur fixée par le type, légende. */
    courbes(d, f, element) {
      const t = theme();
      const series = chemin(donnees, element.dataset.series) || [];
      const options = optionsBase(f);
      options.plugins.reticule = { actif: true };
      return {
        type: "line",
        data: {
          labels: d.libelles,
          datasets: series.map((s, i) => {
            const index = ORDRE_TYPES_COMPTE.indexOf(s.type);
            const couleur = t.series[index >= 0 ? index : i % t.series.length];
            return ligne(couleur, { label: s.libelle, data: s.valeurs });
          }),
        },
        options,
        plugins: [reticule],
      };
    },

    /** Répartition : donut ≤ 6 segments (5 + « Autres »), couleurs = identité des catégories. */
    donut(d, f) {
      const t = theme();
      let libelles = [...d.libelles], valeurs = [...d.valeurs];
      let couleurs = d.couleurs
        ? [...d.couleurs] // identité choisie (catégories)
        : (d.emplacements || libelles.map((_, i) => i)).map((i) => t.series[i % t.series.length]); // emplacement stable
      if (libelles.length > MAX_SEGMENTS) {
        const reste = valeurs.slice(MAX_SEGMENTS - 1).reduce((a, b) => a + b, 0);
        libelles = [...libelles.slice(0, MAX_SEGMENTS - 1), "Autres"];
        valeurs = [...valeurs.slice(0, MAX_SEGMENTS - 1), reste];
        couleurs = [...couleurs.slice(0, MAX_SEGMENTS - 1), t.autres];
      }
      const total = valeurs.reduce((a, b) => a + b, 0);
      return {
        type: "doughnut",
        data: {
          labels: libelles,
          datasets: [{
            data: valeurs,
            backgroundColor: couleurs,
            hoverBackgroundColor: couleurs,
            borderColor: t.surface, // écart de 2 px couleur surface entre segments
            borderWidth: 2,
            hoverOffset: 6,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: "68%",
          animation: { duration: 300 },
          layout: { padding: 6 },
          plugins: {
            legend: { display: false }, // la légende est la liste HTML voisine (nom, %, montant)
            centreDonut: { texte: f.montant(total) },
            tooltip: {
              ...optionsBase(f).plugins.tooltip,
              callbacks: { label: (c) => ` ${c.label} : ${f.montant(c.parsed)} (${f.pct(total ? c.parsed / total : 0)})` },
            },
          },
        },
        plugins: [centreDonut],
      };
    },

    /** Période courante vs référence, par catégorie : barres horizontales (accent vs gris). */
    comparatif(d, f) {
      const t = theme();
      return {
        type: "bar",
        data: {
          labels: d.libelles,
          datasets: [
            { label: d.libelle_a, data: d.a, ...barre(t.series[0], { maxBarThickness: 12 }) },
            { label: d.libelle_b, data: d.b, ...barre(t.reference, { maxBarThickness: 12 }) },
          ],
        },
        options: optionsBase(f, { horizontal: true }),
      };
    },

    /** Une seule mesure par libellé (valeur par titre) : une couleur, pas de légende. */
    barres(d, f, element) {
      const t = theme();
      const options = optionsBase(f, { legende: false, horizontal: true });
      options.plugins.tooltip.callbacks.label = (c) => ` ${f.montant(c.parsed.x)}`;
      return {
        type: "bar",
        data: { labels: d.libelles, datasets: [{ label: element.dataset.titre || "", data: chemin(d, element.dataset.champ || "valeurs"), ...barre(t.series[0]) }] },
        options,
      };
    },

    /** Mesure signée (plus-value par titre) : paire divergente bleu / rouge, zéro neutre. */
    divergent(d, f, element) {
      const t = theme();
      const valeurs = chemin(d, element.dataset.champ || "valeurs");
      const options = optionsBase(f, { legende: false, horizontal: true });
      options.scales.x.grid.color = (c) => (c.tick && c.tick.value === 0 ? t.axe : t.grille);
      options.plugins.tooltip.callbacks.label = (c) => ` ${f.signe(c.parsed.x)}`;
      return {
        type: "bar",
        data: {
          labels: d.libelles,
          datasets: [{ label: element.dataset.titre || "", data: valeurs, ...barre(t.series[0], { backgroundColor: valeurs.map((v) => (v < 0 ? t.negatif : t.series[0])) }) }],
        },
        options,
      };
    },

    /** Deux mesures de même unité par année : barres groupées. */
    annuel(d, f) {
      const t = theme();
      return {
        type: "bar",
        data: {
          labels: d.libelles,
          datasets: [
            { label: "+/- values réalisées", data: d.pv_realisee, ...barre(t.series[0]) },
            { label: "Dividendes nets", data: d.dividendes, ...barre(t.series[1]) },
          ],
        },
        options: optionsBase(f),
      };
    },
  };

  // --- Cycle de vie -------------------------------------------------------------------------
  function dessiner() {
    if (!window.Chart) return;
    graphiques.splice(0).forEach((g) => g.destroy());
    const balise = document.getElementById("donnees-graphiques");
    if (!balise) return;
    donnees = JSON.parse(balise.textContent);
    const f = formateurs(donnees.devise || "EUR");
    Chart.defaults.font.family = "Inter, system-ui, sans-serif";
    Chart.defaults.font.size = 12;

    document.querySelectorAll("[data-graphique]").forEach((element) => {
      const fabrique = FABRIQUES[element.dataset.graphique];
      const d = chemin(donnees, element.dataset.source || "");
      const canvas = element.querySelector("canvas");
      if (!fabrique || !d || !canvas) return;
      let vide = Array.isArray(d.valeurs) ? !d.valeurs.some((v) => v) : false;
      if (element.dataset.graphique === "donut" && d.valeurs.filter((v) => v > 0).length < 3) vide = true;
      element.classList.toggle("hidden", vide);
      if (vide) return;
      graphiques.push(new Chart(canvas, fabrique(d, f, element)));
    });
  }

  document.addEventListener("DOMContentLoaded", dessiner);
  // Le thème change : les teintes sombres sont des pas distincts, on redessine.
  let sombre = document.documentElement.classList.contains("dark");
  new MutationObserver(() => {
    const maintenant = document.documentElement.classList.contains("dark");
    if (maintenant !== sombre) { sombre = maintenant; dessiner(); }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
})();
