"""
Rapports d'analyse. Chaque vue expose `graphiques` : un dict de données déjà converties
en types JSON (float, str) pour Chart.js, à injecter via {{ graphiques|json_script:"donnees" }}.
"""
from datetime import date
from decimal import Decimal

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.generic import TemplateView

from ..forms import PeriodeForm
from ..models import Account, Holding, TypeActif, TypeFlux
from ..services import rapports

HORIZONS = {"1": 12, "3": 36, "5": 60}


def en_float(valeurs):
    """Décimaux → float pour JSON / Chart.js (None conservé : trou dans la courbe)."""
    return [float(v) if isinstance(v, Decimal) else v for v in valeurs]


class RapportCategoriesView(TemplateView):
    """Répartition des dépenses et revenus par catégorie / sous-catégorie (donuts)."""

    template_name = "finances/rapports/categories.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        form = PeriodeForm(self.request.GET or None)
        debut, fin = form.bornes("mois")
        depenses = rapports.repartition_categories(user, debut, fin, TypeFlux.DEPENSE)
        revenus = rapports.repartition_categories(user, debut, fin, TypeFlux.REVENU)

        def donut(rep):
            return {
                "libelles": [c["nom"] for c in rep["categories"]],
                "valeurs": en_float(c["total"] for c in rep["categories"]),
                "couleurs": [c["couleur"] for c in rep["categories"]],
                "detail": {
                    c["nom"]: {
                        "libelles": [s["nom"] for s in c["sous_categories"]],
                        "valeurs": en_float(s["total"] for s in c["sous_categories"]),
                        "couleurs": [s["couleur"] for s in c["sous_categories"]],
                    }
                    for c in rep["categories"]
                },
            }

        ctx.update(
            form=form,
            debut=debut,
            fin=fin,
            depenses=depenses,
            revenus=revenus,
            totaux=rapports.totaux_periode(user, debut, fin),
            graphiques={"devise": depenses["devise"], "depenses": donut(depenses), "revenus": donut(revenus)},
        )
        return ctx


class RapportPatrimoineView(TemplateView):
    """Évolution du patrimoine et des comptes sur 1, 3 ou 5 ans (?horizon=1|3|5)."""

    template_name = "finances/rapports/patrimoine.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        horizon = self.request.GET.get("horizon", "1")
        horizon = horizon if horizon in HORIZONS else "1"
        historique = rapports.historique_patrimoine(self.request.user, HORIZONS[horizon])
        ctx.update(
            horizon=horizon,
            horizons=HORIZONS,
            historique=historique,
            patrimoine=rapports.patrimoine(self.request.user),
            graphiques={
                "devise": historique["devise"],
                "libelles": historique["libelles"],
                "total": en_float(historique["total"]),
                "par_type": [
                    {"type": s["type"], "libelle": s["libelle"], "valeurs": en_float(s["valeurs"])}
                    for s in historique["par_type"]
                ],
                "comptes": [
                    {"libelle": s["nom"], "couleur": s["couleur"], "valeurs": en_float(s["valeurs"])}
                    for s in historique["comptes"]
                ],
            },
        )
        return ctx


class RapportComparatifsView(TemplateView):
    """M vs M-1 (?mois=AAAA-MM) et N vs N-1 (?annee=AAAA, ?a_date=0 pour comparer des années pleines)."""

    template_name = "finances/rapports/comparatifs.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user, aujourd_hui = self.request.user, timezone.localdate()
        try:
            an, mois = (int(x) for x in self.request.GET.get("mois", "").split("-"))
            reference = date(an, mois, 1)
        except ValueError:
            reference = aujourd_hui
        annee = self.request.GET.get("annee", "")
        annee = int(annee) if annee.isdigit() and 1900 < int(annee) < 3000 else aujourd_hui.year
        a_date = self.request.GET.get("a_date", "1") != "0"
        mensuel = rapports.comparatif_mois(user, reference.year, reference.month)
        annuel = rapports.comparatif_annees(user, annee, a_date, aujourd_hui)
        evolution = rapports.evolution_mensuelle(user, 24, aujourd_hui)

        def barres(comp):
            lignes = comp["sections"][0]["lignes"][:12]
            return {
                "libelles": [l["nom"] for l in lignes],
                "a": en_float(l["a"] for l in lignes),
                "b": en_float(l["b"] for l in lignes),
                "libelle_a": comp["libelle_a"],
                "libelle_b": comp["libelle_b"],
            }

        ctx.update(
            mois_reference=reference.strftime("%Y-%m"),
            annee=annee,
            a_date=a_date,
            mensuel=mensuel,
            annuel=annuel,
            evolution=evolution,
            graphiques={
                "devise": evolution["devise"],
                "mensuel": barres(mensuel),
                "annuel": barres(annuel),
                "evolution": {
                    "libelles": evolution["libelles"],
                    "revenus": en_float(evolution["revenus"]),
                    "depenses": en_float(evolution["depenses"]),
                    "epargne": en_float(evolution["epargne"]),
                    "taux_epargne": en_float(evolution["taux_epargne"]),
                },
            },
        )
        return ctx


class RapportTitresView(TemplateView):
    """Performance globale, plus-values latentes et réalisées, allocation d'actifs (?compte=<id>)."""

    template_name = "finances/rapports/titres.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        compte_id = self.request.GET.get("compte", "")
        compte = (
            get_object_or_404(Account.objects.for_user(self.request.user).comptes_titres(), pk=compte_id)
            if compte_id.isdigit()
            else None
        )
        rapport = rapports.rapport_titres(self.request.user, compte)

        def repartition(groupes, ordre_stable):
            # Couleur = emplacement fixe de l'entité (type d'actif, secteur), jamais son rang.
            for g in groupes:
                g["emplacement"] = ordre_stable.index(g["cle"]) % 8
            return {
                "libelles": [g["libelle"] for g in groupes],
                "valeurs": en_float(g["valeur"] for g in groupes),
                "emplacements": [ordre_stable.index(g["cle"]) for g in groupes],
            }

        secteurs = sorted({p.secteur for p in Holding.objects.for_user(self.request.user)} | {""})

        ctx.update(
            compte=compte,
            comptes_titres=Account.objects.for_user(self.request.user).comptes_titres(),
            rapport=rapport,
            graphiques={
                "devise": rapport["devise"],
                "types": repartition(rapport["allocation_types"], list(TypeActif.values)),
                "secteurs": repartition(rapport["allocation_secteurs"], secteurs),
                "titres": {
                    "libelles": [l["position"].ticker_symbole for l in rapport["lignes"]],
                    "valeurs": en_float(l["valeur"] for l in rapport["lignes"]),
                    "pv": en_float(l["pv_latente"] for l in rapport["lignes"]),
                },
                "par_annee": {
                    "libelles": [str(a["annee"]) for a in reversed(rapport["par_annee"])],
                    "pv_realisee": en_float(a["pv_realisee"] for a in reversed(rapport["par_annee"])),
                    "dividendes": en_float(a["dividendes"] for a in reversed(rapport["par_annee"])),
                },
            },
        )
        return ctx
