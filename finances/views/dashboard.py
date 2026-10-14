from django.utils import timezone
from django.views.generic import TemplateView

from ..models import Transaction
from ..services import budget, rapports
from ..services.periodes import bornes_periode
from .rapports import en_float


class DashboardView(TemplateView):
    """Valeur nette, répartition par type de compte, taux d'épargne du mois, dernières opérations."""

    template_name = "finances/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user, aujourd_hui = self.request.user, timezone.localdate()
        profil = user.profile
        patrimoine = rapports.patrimoine(user)
        flux_mois = rapports.totaux_periode(user, *bornes_periode("mois", aujourd_hui))
        flux_precedent = rapports.totaux_periode(user, *bornes_periode("mois_prec", aujourd_hui))
        historique = rapports.historique_patrimoine(user, 12, aujourd_hui)
        evolution = rapports.evolution_mensuelle(user, 12, aujourd_hui)
        ctx.update(
            aujourd_hui=aujourd_hui,
            patrimoine=patrimoine,
            flux_mois=flux_mois,
            flux_precedent=flux_precedent,
            objectif_epargne=profil.objectif_taux_epargne,
            objectif_atteint=(
                flux_mois["taux_epargne"] is not None and flux_mois["taux_epargne"] >= profil.objectif_taux_epargne
            ),
            budget=budget.resume_mois(user, aujourd_hui.year, aujourd_hui.month, profil.seuil_alerte_budget),
            dernieres_operations=(
                Transaction.objects.for_user(user)
                .select_related("compte", "compte_destination", "categorie", "sous_categorie")
                .order_by("-date", "-pk")[:10]
            ),
            historique=historique,
            evolution=evolution,
            graphiques={
                "devise": historique["devise"],
                "flux": {
                    "libelles": evolution["libelles"],
                    "revenus": en_float(evolution["revenus"]),
                    "depenses": en_float(evolution["depenses"]),
                },
                "patrimoine": {"libelles": historique["libelles"], "total": en_float(historique["total"])},
            },
            avertissements=sorted(
                set(patrimoine.avertissements) | set(flux_mois["avertissements"]) | set(historique["avertissements"])
            ),
        )
        return ctx
