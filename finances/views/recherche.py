from django.views.generic import ListView

from ..forms import RechercheForm
from ..models import Transaction
from ..services.exports import ENTETES_TRANSACTIONS, lignes_transactions, reponse_csv, reponse_pdf_transactions
from ..services.rapports import devise_principale, totaux_convertis
from .mixins import HtmxTemplateMixin, PaginationProfilMixin

PARAMETRES_TECHNIQUES = {"page", "export"}


class RechercheView(HtmxTemplateMixin, PaginationProfilMixin, ListView):
    """
    Moteur de recherche multicritère (GET). Sert aussi de liste des opérations.
    ?export=csv|pdf exporte l'intégralité des résultats filtrés.
    Requête HTMX : seul le bloc des résultats est renvoyé (recherche « live »).
    """

    model = Transaction
    template_name = "finances/recherche.html"
    partial_template_name = "finances/partials/resultats_recherche.html"
    context_object_name = "operations"

    def get(self, request, *args, **kwargs):
        criteres = set(request.GET) - PARAMETRES_TECHNIQUES
        self.form = RechercheForm(request.GET if criteres else None, user=request.user)
        self.resultats = self.form.filtrer()
        export = request.GET.get("export")
        if export in ("csv", "pdf") and not self.form.errors:
            return self.exporter(export)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return self.resultats

    def totaux(self):
        return totaux_convertis(self.resultats, devise_principale(self.request.user))

    def exporter(self, format_export):
        if format_export == "csv":
            return reponse_csv("operations", ENTETES_TRANSACTIONS, lignes_transactions(self.resultats))
        return reponse_pdf_transactions(
            self.resultats, "Relevé d'opérations", self.form.criteres_actifs(), self.totaux()
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        parametres = self.request.GET.copy()
        parametres.pop("page", None)
        parametres.pop("export", None)
        ctx.update(
            form=self.form,
            criteres=self.form.criteres_actifs(),
            totaux=self.totaux(),
            nb_resultats=ctx["paginator"].count if ctx.get("paginator") else len(ctx["operations"]),
            parametres=parametres.urlencode(),  # pour la pagination et les liens d'export
        )
        return ctx
