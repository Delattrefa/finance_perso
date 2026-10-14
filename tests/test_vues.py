from datetime import date
from decimal import Decimal as D

from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import User
from finances.models import (
    Account, Budget, BudgetAllocation, BudgetPlan, Category, Holding, InvestmentTransaction, SubCategory,
    Transaction, TypeCompte,
)

from .chargeur import TEMPLATES_FACTICES

HTMX = {"HTTP_HX_REQUEST": "true"}


@override_settings(TEMPLATES=TEMPLATES_FACTICES)
class VuesTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("alice", "a@ex.com", "x")
        self.intrus = User.objects.create_user("eve", "e@ex.com", "x")
        self.courant = Account.objects.create(user=self.u, nom="Courant", solde_initial=D("100"))
        self.cto = Account.objects.create(user=self.u, nom="CTO", type=TypeCompte.TITRE)
        self.alim = Category.objects.create(user=self.u, nom="Alimentation")
        self.courses = SubCategory.objects.create(categorie=self.alim, nom="Courses")
        self.op = Transaction.objects.create(user=self.u, compte=self.courant, montant=D("40"),
                                             sous_categorie=self.courses, tiers_payeur="Carrefour")
        self.h = Holding.objects.create(user=self.u, compte_titre=self.cto, ticker_symbole="AI", nom_action="Air Liquide")
        self.op_titre = InvestmentTransaction.objects.create(user=self.u, compte_titre=self.cto, holding=self.h,
                                                             type_operation="ACHAT", quantite=D("1"), prix_unitaire=D("150"))
        self.plan = BudgetPlan.objects.create(user=self.u, nom="Plan")
        self.ligne = Budget.objects.create(user=self.u, plan=self.plan, categorie=self.alim, montant_reference=D("300"))
        self.cellule = self.ligne.montants.first()
        self.client.force_login(self.u)

    def urls_objets(self):
        return [
            ("compte", self.courant.pk), ("compte_modifier", self.courant.pk), ("compte_supprimer", self.courant.pk),
            ("categorie_modifier", self.alim.pk), ("categorie_supprimer", self.alim.pk),
            ("sous_categorie_modifier", self.courses.pk), ("sous_categorie_supprimer", self.courses.pk),
            ("operation_modifier", self.op.pk), ("operation_supprimer", self.op.pk),
            ("position", self.h.pk), ("position_modifier", self.h.pk), ("position_supprimer", self.h.pk),
            ("titre_operation_modifier", self.op_titre.pk), ("titre_operation_supprimer", self.op_titre.pk),
            ("budget_matrice", self.plan.pk), ("budget_plan_modifier", self.plan.pk), ("budget_plan_supprimer", self.plan.pk),
            ("budget_ligne_ajouter", self.plan.pk), ("budget_ligne_modifier", self.ligne.pk),
            ("budget_ligne_supprimer", self.ligne.pk), ("budget_cellule", self.cellule.pk),
        ]

    def test_toutes_les_pages_repondent(self):
        sans_parametre = [
            "dashboard", "comptes", "compte_ajouter", "categories", "categorie_ajouter", "sous_categorie_ajouter",
            "recherche", "operation_ajouter", "portefeuille", "titre_operation_ajouter", "budget_plans",
            "budget_plan_ajouter", "rapport_categories", "rapport_patrimoine", "rapport_comparatifs", "rapport_titres",
        ]
        for nom in sans_parametre:
            self.assertEqual(self.client.get(reverse(f"finances:{nom}")).status_code, 200, nom)
        for nom, pk in self.urls_objets():
            self.assertEqual(self.client.get(reverse(f"finances:{nom}", args=[pk])).status_code, 200, nom)
        self.assertRedirects(self.client.get(reverse("finances:budget")), reverse("finances:budget_matrice", args=[self.plan.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("profil")).status_code, 200)

    def test_isolation_entre_utilisateurs(self):
        self.client.force_login(self.intrus)
        for nom, pk in self.urls_objets():
            self.assertEqual(self.client.get(reverse(f"finances:{nom}", args=[pk])).status_code, 404, nom)
        self.assertEqual(self.client.post(reverse("finances:operation_statut", args=[self.op.pk])).status_code, 404)
        r = self.client.get(reverse("finances:recherche"))
        self.assertEqual(r.context["nb_resultats"], 0)
        r = self.client.get(reverse("finances:options_sous_categories"), {"categorie": self.alim.pk})
        self.assertNotContains(r, "Courses")

    def test_connexion_obligatoire(self):
        self.client.logout()
        r = self.client.get(reverse("finances:dashboard"))
        self.assertRedirects(r, f"{reverse('login')}?next=/", fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        self.assertEqual(self.client.get(reverse("inscription")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:login")).status_code, 200)

    def test_inscription_cree_les_categories(self):
        self.client.logout()
        r = self.client.post(reverse("inscription"), {
            "username": "bob", "email": "b@ex.com", "first_name": "Bob",
            "password1": "Tr3s-solide-123", "password2": "Tr3s-solide-123",
        })
        self.assertRedirects(r, reverse("finances:dashboard"), fetch_redirect_response=False)
        self.assertGreater(Category.objects.filter(user__username="bob").count(), 10)

    def test_dashboard(self):
        ctx = self.client.get(reverse("finances:dashboard")).context
        self.assertEqual(ctx["patrimoine"].total, D("60"))  # 100 − 40 + (−150 + 150)
        self.assertEqual(ctx["flux_mois"]["depenses"], D("40"))
        self.assertEqual(ctx["budget"]["depenses"].prevu, D("300"))
        self.assertEqual(len(ctx["dernieres_operations"]), 1)

    def test_saisie_rapide_et_enchainement(self):
        r = self.client.post(reverse("finances:operation_ajouter"), {
            "type": "DEPENSE", "compte": self.courant.pk, "montant": "12,30", "date": "2026-04-02",
            "categorie": self.alim.pk, "sous_categorie": self.courses.pk, "statut": "EN_ATTENTE", "_autre": "1",
        })
        self.assertRedirects(r, reverse("finances:operation_ajouter") + f"?compte={self.courant.pk}&type=DEPENSE&date=2026-04-02",
                             fetch_redirect_response=False)
        r = self.client.get(reverse("finances:operation_ajouter"), {"depuis": self.op.pk})
        self.assertEqual(r.context["form"].initial["sous_categorie"], self.courses.pk)
        self.assertIn("Carrefour", r.context["suggestions_tiers"])

    def test_next_externe_ignore(self):
        r = self.client.post(reverse("finances:operation_modifier", args=[self.op.pk]), {
            "type": "DEPENSE", "compte": self.courant.pk, "montant": "41", "date": "2026-04-02",
            "statut": "EN_ATTENTE", "next": "https://pirate.example/",
        })
        self.assertRedirects(r, reverse("finances:recherche"), fetch_redirect_response=False)

    def test_htmx_listes_dependantes_et_statut(self):
        r = self.client.get(reverse("finances:options_sous_categories"), {"categorie": self.alim.pk, "sous_categorie": self.courses.pk})
        self.assertContains(r, f'<option value="{self.courses.pk}" selected>Courses</option>', html=True)
        r = self.client.get(reverse("finances:options_categories"), {"type": "REVENU"})
        self.assertNotContains(r, "Alimentation")
        r = self.client.get(reverse("finances:options_positions"), {"compte_titre": self.cto.pk})
        self.assertContains(r, "AI · Air Liquide")
        r = self.client.post(reverse("finances:operation_statut", args=[self.op.pk]), **HTMX)
        self.assertEqual(r.templates[0].name, "finances/partials/statut_operation.html")
        self.op.refresh_from_db()
        self.assertEqual(self.op.statut, "RAPPROCHE")

    def test_recherche_et_exports(self):
        r = self.client.get(reverse("finances:recherche"), {"q": "carrefour", "tri": "-date"})
        self.assertEqual(r.context["nb_resultats"], 1)
        self.assertEqual(r.context["totaux"]["depenses"], D("40"))
        self.assertEqual(r.context["parametres"], "q=carrefour&tri=-date")
        r = self.client.get(reverse("finances:recherche"), {"q": "carrefour"}, **HTMX)
        self.assertEqual(r.templates[0].name, "finances/partials/resultats_recherche.html")
        r = self.client.get(reverse("finances:recherche"), {"q": "carrefour", "export": "csv"})
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        contenu = r.content.decode("utf-8-sig")
        self.assertIn("Carrefour", contenu)
        self.assertIn("-40,00", contenu)
        r = self.client.get(reverse("finances:recherche"), {"q": "carrefour", "export": "pdf"})
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_matrice_et_edition_de_cellule(self):
        r = self.client.get(reverse("finances:budget_matrice", args=[self.plan.pk]), {"vue": "quinquennale"})
        self.assertEqual(r.context["matrice"].vue, "quinquennale")
        r = self.client.get(reverse("finances:budget_matrice", args=[self.plan.pk]), {"vue": "annuelle"}, **HTMX)
        self.assertEqual(r.templates[0].name, "finances/budget/partials/matrice.html")
        r = self.client.post(reverse("finances:budget_cellule", args=[self.cellule.pk]), {"montant": "350,00"}, **HTMX)
        self.assertEqual(r["HX-Trigger"], "budget-modifie")
        self.cellule.refresh_from_db()
        self.assertEqual((self.cellule.montant, self.cellule.saisi_manuellement), (D("350"), True))
        self.client.post(reverse("finances:budget_cellule_reinitialiser", args=[self.cellule.pk]))
        self.cellule.refresh_from_db()
        self.assertEqual((self.cellule.montant, self.cellule.saisi_manuellement), (D("300"), False))

    def test_plans_activation_et_recalcul(self):
        autre = BudgetPlan.objects.create(user=self.u, nom="Plan B", actif=False)
        self.client.post(reverse("finances:budget_plan_activer", args=[autre.pk]))
        self.assertEqual(BudgetPlan.objects.get(actif=True), autre)
        BudgetAllocation.objects.filter(pk=self.cellule.pk).update(montant=D("1"), saisi_manuellement=True)
        self.client.post(reverse("finances:budget_plan_recalculer", args=[self.plan.pk]), {"ecraser": "1"})
        self.cellule.refresh_from_db()
        self.assertEqual(self.cellule.montant, D("300"))

    def test_suppression_protegee(self):
        r = self.client.post(reverse("finances:compte_supprimer", args=[self.courant.pk]), follow=True)
        self.assertTrue(Account.objects.filter(pk=self.courant.pk).exists())
        self.assertIn("archivez-le", str(list(r.context["messages"])[0]))

    def test_titres(self):
        r = self.client.post(reverse("finances:titre_operation_ajouter"), {
            "compte_titre": self.cto.pk, "ticker_symbole": "cw8", "nom_action": "World", "type_actif": "ETF",
            "type_operation": "ACHAT", "date": "2026-01-02", "quantite": "2", "prix_unitaire": "500", "frais": "0",
        })
        cw8 = Holding.objects.get(ticker_symbole="CW8")
        self.assertRedirects(r, reverse("finances:position", args=[cw8.pk]), fetch_redirect_response=False)
        self.client.post(reverse("finances:position_cours", args=[cw8.pk]), {"cours": "520,5", "date": "2026-01-05"})
        cw8.refresh_from_db()
        self.assertEqual(cw8.dernier_cours, D("520.5"))
        r = self.client.get(reverse("finances:rapport_titres"))
        self.assertEqual(r.context["graphiques"]["titres"]["libelles"], ["CW8", "AI"])
        self.client.post(reverse("finances:titre_operation_supprimer", args=[self.op_titre.pk]))
        self.h.refresh_from_db()
        self.assertEqual(self.h.quantite, 0)

    def test_rapports_parametres(self):
        r = self.client.get(reverse("finances:rapport_patrimoine"), {"horizon": "5"})
        self.assertEqual(len(r.context["graphiques"]["total"]), 60)
        r = self.client.get(reverse("finances:rapport_patrimoine"), {"horizon": "abc"})
        self.assertEqual(r.context["horizon"], "1")
        r = self.client.get(reverse("finances:rapport_comparatifs"), {"mois": "n'importe", "annee": "2025", "a_date": "0"})
        self.assertEqual(r.context["annuel"]["libelle_b"], "2024")
        r = self.client.get(reverse("finances:rapport_categories"), {"periode": "perso", "date_debut": "2020-01-01", "date_fin": "2030-12-31"})
        self.assertEqual(r.context["graphiques"]["depenses"]["valeurs"], [40.0])
        self.assertEqual(r.context["graphiques"]["depenses"]["detail"]["Alimentation"]["libelles"], ["Courses"])
