"""Rendu réel de toutes les pages (templates de l'étape 4) avec un jeu de données complet."""
import re
from datetime import date
from decimal import Decimal as D

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import User
from finances.models import (
    Account, Budget, BudgetPlan, Category, Holding, InvestmentTransaction, SubCategory, Transaction, TypeCompte,
)
from finances.services.categories_defaut import creer_categories_par_defaut

HTMX = {"HTTP_HX_REQUEST": "true"}
MARQUEUR = "§VARIABLE_INVALIDE§"
TEMPLATES_STRICTS = [{**settings.TEMPLATES[0], "OPTIONS": {**settings.TEMPLATES[0]["OPTIONS"], "string_if_invalid": MARQUEUR}}]


@override_settings(TEMPLATES=TEMPLATES_STRICTS)
class RenduTemplatesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        u = cls.u = User.objects.create_user("alice", "a@ex.com", "x", first_name="Alice")
        creer_categories_par_defaut(u)
        aujourd_hui = timezone.localdate()
        cls.courant = Account.objects.create(user=u, nom="Courant", solde_initial=D("1500"), date_creation=date(2024, 1, 1), icone="wallet")
        cls.livret = Account.objects.create(user=u, nom="Livret A", type=TypeCompte.EPARGNE, date_creation=date(2024, 1, 1))
        cls.cto = Account.objects.create(user=u, nom="CTO", type=TypeCompte.TITRE, date_creation=date(2024, 1, 1))
        alim = Category.objects.get(user=u, nom="Alimentation")
        courses = SubCategory.objects.get(categorie=alim, nom="Courses")
        salaires = Category.objects.get(user=u, nom="Salaires")
        loisirs = Category.objects.get(user=u, nom="Loisirs")
        mk = lambda **kw: Transaction.objects.create(user=u, compte=cls.courant, **kw)
        cls.op = mk(montant=D("82.40"), date=aujourd_hui, sous_categorie=courses, tiers_payeur="Carrefour", description="Courses semaine")
        mk(montant=D("2900"), date=aujourd_hui.replace(day=1), type="REVENU", categorie=salaires, tiers_payeur="ACME")
        mk(montant=D("45"), date=aujourd_hui, categorie=loisirs, statut="RAPPROCHE")
        mk(montant=D("12"), date=aujourd_hui)
        mk(montant=D("1000"), date=aujourd_hui, type="VIREMENT_INTERNE", compte_destination=cls.cto)
        cls.h = Holding.objects.create(user=u, compte_titre=cls.cto, ticker_symbole="CW8", nom_action="Amundi MSCI World", type_actif="ETF", secteur="Monde")
        InvestmentTransaction.objects.create(user=u, compte_titre=cls.cto, holding=cls.h, type_operation="ACHAT", date=date(2024, 3, 1), quantite=D("2"), prix_unitaire=D("400"), frais=D("2"))
        InvestmentTransaction.objects.create(user=u, compte_titre=cls.cto, holding=cls.h, type_operation="VENTE", date=date(2025, 3, 1), quantite=D("1"), prix_unitaire=D("480"))
        Holding.objects.create(user=u, compte_titre=cls.cto, ticker_symbole="AI", nom_action="Air Liquide")
        InvestmentTransaction.objects.create(user=u, compte_titre=cls.cto, holding=Holding.objects.get(ticker_symbole="AI"), type_operation="ACHAT", date=date(2025, 6, 1), quantite=D("1"), prix_unitaire=D("170"))
        cls.h.mettre_a_jour_cours(D("512.3"), aujourd_hui)
        cls.plan = BudgetPlan.objects.create(user=u, nom="Plan 5 ans", annee_debut=aujourd_hui.year)
        cls.ligne = Budget.objects.create(user=u, plan=cls.plan, categorie=alim, montant_reference=D("400"))
        Budget.objects.create(user=u, plan=cls.plan, categorie=alim, sous_categorie=courses, montant_reference=D("50"))
        Budget.objects.create(user=u, plan=cls.plan, categorie=salaires, montant_reference=D("2900"))
        Budget.objects.create(user=u, plan=cls.plan, categorie=Category.objects.get(user=u, nom="Impôts & taxes"),
                              montant_reference=D("1200"), periodicite="ANNUELLE", mois_reference=10, lisser=True)

    def setUp(self):
        self.client.force_login(self.u)

    def verifier(self, url, **kwargs):
        r = self.client.get(url, **kwargs)
        self.assertEqual(r.status_code, 200, url)
        contenu = r.content.decode()
        extraits = [contenu[max(0, m.start() - 120): m.end() + 30] for m in re.finditer(MARQUEUR, contenu)][:3]
        self.assertFalse(extraits, f"Variable invalide dans {url} : {extraits}")
        return contenu

    def test_pages(self):
        u = reverse
        pages = [
            u("finances:dashboard"), u("finances:comptes"), u("finances:comptes") + "?archives=1", u("finances:compte_ajouter"),
            u("finances:compte", args=[self.courant.pk]), u("finances:compte", args=[self.cto.pk]),
            u("finances:compte_modifier", args=[self.courant.pk]), u("finances:compte_supprimer", args=[self.courant.pk]),
            u("finances:categories"), u("finances:categorie_ajouter"), u("finances:sous_categorie_ajouter"),
            u("finances:recherche"), u("finances:recherche") + "?q=carrefour&periode=annee&types=DEPENSE",
            u("finances:recherche") + "?montant_min=10&montant_max=1",
            u("finances:operation_ajouter"), u("finances:operation_ajouter") + f"?depuis={self.op.pk}",
            u("finances:operation_modifier", args=[self.op.pk]),
            u("finances:portefeuille"), u("finances:portefeuille") + f"?compte={self.cto.pk}",
            u("finances:position", args=[self.h.pk]), u("finances:position_modifier", args=[self.h.pk]),
            u("finances:titre_operation_ajouter"), u("finances:titre_operation_ajouter") + f"?position={self.h.pk}&type=VENTE",
            u("finances:budget_plans"), u("finances:budget_plan_ajouter"), u("finances:budget_plan_modifier", args=[self.plan.pk]),
            u("finances:budget_ligne_ajouter", args=[self.plan.pk]), u("finances:budget_ligne_modifier", args=[self.ligne.pk]),
            u("finances:rapport_categories"), u("finances:rapport_categories") + "?periode=annee",
            u("finances:rapport_patrimoine") + "?horizon=3", u("finances:rapport_comparatifs"), u("finances:rapport_titres"),
            u("profil"), u("password_change"),
        ]
        for vue in ("mensuelle", "annuelle", "quinquennale"):
            pages.append(u("finances:budget_matrice", args=[self.plan.pk]) + f"?vue={vue}")
        for page in pages:
            self.verifier(page)

    def test_fragments_htmx(self):
        contenu = self.verifier(reverse("finances:recherche") + "?q=carrefour", **HTMX)
        self.assertNotIn("<html", contenu)
        self.assertIn("Carrefour", contenu)
        contenu = self.verifier(reverse("finances:budget_matrice", args=[self.plan.pk]) + "?vue=annuelle", **HTMX)
        self.assertIn("Cumul à date", contenu)
        cellule = self.ligne.montants.first()
        self.assertIn('name="montant"', self.verifier(reverse("finances:budget_cellule", args=[cellule.pk]), **HTMX))
        r = self.client.post(reverse("finances:budget_cellule", args=[cellule.pk]), {"montant": "123"}, **HTMX)
        self.assertIn("123", r.content.decode())
        r = self.client.post(reverse("finances:position_cours", args=[self.h.pk]), {"cours": "520", "date": "2026-01-02"}, **HTMX)
        self.assertNotIn(MARQUEUR, r.content.decode())
        r = self.client.post(reverse("finances:operation_statut", args=[self.op.pk]), **HTMX)
        self.assertIn("Rapprochée", r.content.decode())

    def test_formulaire_invalide_reaffiche_les_erreurs(self):
        r = self.client.post(reverse("finances:operation_ajouter"), {"type": "DEPENSE", "montant": "abc"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "aria-invalid")

    def test_pages_publiques(self):
        self.client.logout()
        for nom in ("login", "inscription"):
            self.verifier(reverse(nom))


    def test_donnees_des_graphiques(self):
        """Chaque page à graphiques fournit son JSON et ses emplacements ; chaque source existe dans le JSON."""
        import json

        pages = {
            reverse("finances:dashboard"): {"flux", "tendance"},
            reverse("finances:rapport_categories") + "?periode=annee": {"donut"},
            reverse("finances:rapport_patrimoine"): {"courbe", "courbes"},
            reverse("finances:rapport_comparatifs"): {"flux", "comparatif"},
            reverse("finances:rapport_titres"): {"donut", "barres", "divergent", "annuel"},
        }
        for url, types_attendus in pages.items():
            contenu = self.verifier(url)
            donnees = json.loads(re.search(r'<script id="donnees-graphiques" type="application/json">(.*?)</script>', contenu, re.S).group(1))
            emplacements = re.findall(r'data-graphique="(\w+)" data-source="([\w.]*)"', contenu)
            self.assertEqual({t for t, _ in emplacements}, types_attendus, url)
            for _, source in emplacements:
                valeur = donnees
                for cle in filter(None, source.split(".")):
                    self.assertIn(cle, valeur, f"{url} : source « {source} » absente du JSON")
                    valeur = valeur[cle]
            self.assertIn("chart.umd.min.js", contenu)
