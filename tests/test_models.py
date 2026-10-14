from decimal import Decimal as D
from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import User
from finances.models import (
    Account, Budget, BudgetAllocation, BudgetPlan, Category, Holding, InvestmentTransaction,
    Periodicite, SubCategory, Transaction, TypeCompte, TypeFlux, TypeOperationTitre as Op, TypeTransaction,
)


class ModelesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", "alice@example.com", "x")
        self.courant = Account.objects.create(user=self.user, nom="Courant", solde_initial=D("1000"))
        self.cto = Account.objects.create(user=self.user, nom="CTO", type=TypeCompte.TITRE)
        self.alim = Category.objects.create(user=self.user, nom="Alimentation", type=TypeFlux.DEPENSE)
        self.courses = SubCategory.objects.create(categorie=self.alim, nom="Courses")

    def test_profil_cree_automatiquement(self):
        self.assertEqual(self.user.profile.devise_principale, "EUR")

    def test_soldes_et_virement(self):
        Transaction.objects.create(user=self.user, compte=self.courant, montant=D("100"), sous_categorie=self.courses)
        Transaction.objects.create(user=self.user, compte=self.courant, type=TypeTransaction.VIREMENT_INTERNE,
                                   compte_destination=self.cto, montant=D("500"))
        t = Transaction.objects.get(sous_categorie=self.courses)
        self.assertEqual(t.categorie, self.alim)  # catégorie déduite de la sous-catégorie
        soldes = {a.nom: a.solde_especes for a in Account.objects.for_user(self.user).with_soldes()}
        self.assertEqual(soldes, {"Courant": D("400"), "CTO": D("500")})
        self.assertEqual(self.courant.calculer_solde(), D("400"))

    def test_categorie_incompatible(self):
        t = Transaction(user=self.user, compte=self.courant, type=TypeTransaction.REVENU, montant=D("1"), categorie=self.alim)
        with self.assertRaises(ValidationError):
            t.full_clean()

    def test_pmpa_et_plus_value(self):
        h = Holding.objects.create(user=self.user, compte_titre=self.cto, ticker_symbole="cw8", nom_action="MSCI World")
        mk = lambda **kw: InvestmentTransaction.objects.create(user=self.user, compte_titre=self.cto, holding=h, **kw)
        mk(type_operation=Op.ACHAT, date=date(2025, 1, 1), quantite=D("10"), prix_unitaire=D("100"), frais=D("10"))
        mk(type_operation=Op.ACHAT, date=date(2025, 2, 1), quantite=D("10"), prix_unitaire=D("120"), frais=D("10"))
        vente = mk(type_operation=Op.VENTE, date=date(2025, 3, 1), quantite=D("5"), prix_unitaire=D("150"), frais=D("5"))
        mk(type_operation=Op.DIVIDENDE, date=date(2025, 4, 1), quantite=D("15"), prix_unitaire=D("2"), frais=D("3"))
        h.refresh_from_db(); vente.refresh_from_db()
        self.assertEqual(h.ticker_symbole, "CW8")
        self.assertEqual(h.quantite, D("15"))
        self.assertEqual(h.pmpa, D("111"))                    # (1010 + 1210) / 20
        self.assertEqual(vente.plus_value_realisee, D("190"))  # 745 − 5 × 111
        self.assertEqual(h.dividendes_percus, D("27"))
        # Liquidités : −1010 −1210 +745 +27
        self.assertEqual(self.cto.calculer_solde_especes(), D("-1448"))
        h.mettre_a_jour_cours(D("130"), date(2025, 5, 1))
        self.assertEqual(self.cto.calculer_valorisation_titres(), D("1950"))
        self.assertEqual(self.cto.calculer_valorisation_titres(au=date(2025, 1, 15)), D("1010"))  # au PMPA

    def test_vente_a_decouvert_refusee(self):
        h = Holding.objects.create(user=self.user, compte_titre=self.cto, ticker_symbole="AI", nom_action="Air Liquide")
        op = InvestmentTransaction(user=self.user, compte_titre=self.cto, holding=h, type_operation=Op.VENTE,
                                   quantite=D("1"), prix_unitaire=D("1"))
        with self.assertRaises(ValidationError):
            op.full_clean()

    def test_budget_5_ans_indexe(self):
        plan = BudgetPlan.objects.create(user=self.user, nom="Plan", annee_debut=2026, taux_indexation=D("10"))
        ligne = Budget.objects.create(user=self.user, plan=plan, categorie=self.alim, montant_reference=D("100"))
        taxe = Budget.objects.create(user=self.user, plan=plan, categorie=self.alim, sous_categorie=self.courses,
                                     montant_reference=D("1200"), periodicite=Periodicite.ANNUELLE, mois_reference=9)
        self.assertEqual(ligne.montants.count(), 60)
        self.assertEqual(ligne.montants.get(annee=2027, mois=6).montant, D("110.00"))
        self.assertEqual(taxe.montant_annuel_theorique(2026), D("1200"))
        self.assertEqual(taxe.montants.get(annee=2026, mois=9).montant, D("1200"))
        # Une saisie manuelle survit à un changement d'indexation du plan
        cellule = ligne.montants.get(annee=2026, mois=1)
        cellule.montant, cellule.saisi_manuellement = D("50"), True
        cellule.save()
        plan = BudgetPlan.objects.get(pk=plan.pk)
        plan.taux_indexation = D("0")
        plan.save()
        self.assertEqual(ligne.montants.get(annee=2027, mois=6).montant, D("100.00"))
        self.assertEqual(ligne.montants.get(annee=2026, mois=1).montant, D("50"))
        # Un seul plan actif
        with self.assertRaises(Exception):
            BudgetPlan.objects.create(user=self.user, nom="Plan 2")

    def test_suppression_compte_protegee_mais_utilisateur_supprimable(self):
        Transaction.objects.create(user=self.user, compte=self.courant, montant=D("10"), categorie=self.alim)
        with self.assertRaises(Exception):
            self.courant.delete()
        self.user.delete()
        self.assertFalse(Transaction.objects.exists())


class SuppressionVolumineuseTests(TestCase):
    def test_suppression_utilisateur_avec_plan_budgetaire(self):
        """Régression : from_db de BudgetPlan provoquait une récursion infinie lors des suppressions en cascade."""
        u = User.objects.create_user("bob", "b@example.com", "x")
        cat = Category.objects.create(user=u, nom="Alim")
        plan = BudgetPlan.objects.create(user=u, nom="P", annee_debut=2026)
        Budget.objects.create(user=u, plan=plan, categorie=cat, montant_reference=D("10"))
        BudgetPlan.objects.only("pk").get(pk=plan.pk)  # chargement partiel
        u.delete()
        self.assertFalse(BudgetPlan.objects.exists())
