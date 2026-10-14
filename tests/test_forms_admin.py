from datetime import date
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from core.models import User
from finances.forms import (
    AccountForm, BudgetAllocationForm, BudgetForm, BudgetPlanForm, InvestmentTransactionForm,
    RechercheForm, SubCategoryForm, TransactionForm,
)
from finances.models import (
    Account, Budget, BudgetPlan, Category, Holding, InvestmentTransaction, SubCategory, Transaction,
    TypeCompte, TypeFlux,
)


class FormulairesTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", "a@ex.com", "x")
        self.bob = User.objects.create_user("bob", "b@ex.com", "x")
        self.courant = Account.objects.create(user=self.alice, nom="Courant")
        self.livret = Account.objects.create(user=self.alice, nom="Livret", type=TypeCompte.EPARGNE)
        self.cto = Account.objects.create(user=self.alice, nom="CTO", type=TypeCompte.TITRE)
        self.compte_bob = Account.objects.create(user=self.bob, nom="Bob")
        self.alim = Category.objects.create(user=self.alice, nom="Alimentation")
        self.salaire = Category.objects.create(user=self.alice, nom="Salaire", type=TypeFlux.REVENU)
        self.courses = SubCategory.objects.create(categorie=self.alim, nom="Courses")
        self.resto = SubCategory.objects.create(categorie=self.alim, nom="Restaurant")

    def test_isolation_des_choix(self):
        form = TransactionForm(user=self.alice)
        self.assertNotIn(self.compte_bob, form.fields["compte"].queryset)
        # Non lié : aucune sous-catégorie tant qu'aucune catégorie n'est choisie ; type DEPENSE par défaut
        self.assertFalse(form.fields["sous_categorie"].queryset.exists())
        self.assertNotIn(self.salaire, form.fields["categorie"].queryset)
        form = TransactionForm(user=self.alice, data={"type": "DEPENSE", "compte": self.compte_bob.pk,
                                                      "montant": "10", "date": "2026-01-01", "statut": "EN_ATTENTE"})
        self.assertIn("compte", form.errors)

    def test_saisie_depense_virgule_et_sous_categorie(self):
        form = TransactionForm(user=self.alice, data={
            "type": "DEPENSE", "compte": self.courant.pk, "montant": "1 234,56", "date": "2026-01-05",
            "categorie": self.alim.pk, "sous_categorie": self.courses.pk, "statut": "EN_ATTENTE",
        })
        self.assertTrue(form.is_valid(), form.errors)
        t = form.save()
        self.assertEqual((t.user, t.montant), (self.alice, D("1234.56")))
        form = TransactionForm(user=self.alice, data={"type": "DEPENSE", "categorie": self.alim.pk})
        self.assertEqual(set(form.fields["sous_categorie"].queryset), {self.courses, self.resto})

    def test_virement_et_type_incoherent(self):
        form = TransactionForm(user=self.alice, data={
            "type": "VIREMENT_INTERNE", "compte": self.courant.pk, "compte_destination": self.livret.pk,
            "montant": "200", "date": "2026-01-05", "categorie": self.alim.pk, "statut": "RAPPROCHE",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.save().categorie)
        form = TransactionForm(user=self.alice, data={
            "type": "VIREMENT_INTERNE", "compte": self.courant.pk, "montant": "1", "date": "2026-01-05", "statut": "RAPPROCHE"})
        self.assertIn("compte_destination", form.errors)
        form = TransactionForm(user=self.alice, data={
            "type": "REVENU", "compte": self.courant.pk, "montant": "1", "date": "2026-01-05",
            "categorie": self.alim.pk, "statut": "RAPPROCHE"})
        self.assertIn("categorie", form.errors)

    def test_nom_de_compte_en_double(self):
        form = AccountForm(user=self.alice, data={"nom": "Courant", "type": "COURANT", "devise": "EUR",
                                                  "solde_initial": "0", "date_creation": "2026-01-01",
                                                  "couleur": "#000000", "ordre": 0})
        self.assertFalse(form.is_valid())
        self.assertIn("Vous avez déjà un compte portant ce nom.", str(form.errors))

    def test_sous_categorie_ne_change_pas_de_categorie_si_utilisee(self):
        Transaction.objects.create(user=self.alice, compte=self.courant, montant=D("5"), sous_categorie=self.courses)
        autre = Category.objects.create(user=self.alice, nom="Loisirs")
        form = SubCategoryForm(user=self.alice, instance=self.courses,
                               data={"categorie": autre.pk, "nom": "Courses", "ordre": 0})
        self.assertIn("categorie", form.errors)

    def test_titre_cree_a_la_volee_puis_vente(self):
        base = {"date": "2026-02-01", "frais": "1,50", "compte_titre": self.cto.pk}
        form = InvestmentTransactionForm(user=self.alice, data={
            **base, "ticker_symbole": "cw8", "nom_action": "Amundi MSCI World", "type_actif": "ETF",
            "type_operation": "ACHAT", "quantite": "4", "prix_unitaire": "500"})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        h = Holding.objects.get(ticker_symbole="CW8")
        self.assertEqual((h.quantite, h.cout_total, h.user), (D("4"), D("2001.50"), self.alice))
        # Même ticker saisi à nouveau : réutilise la position
        form = InvestmentTransactionForm(user=self.alice, data={
            **base, "ticker_symbole": "CW8", "type_operation": "VENTE", "quantite": "10", "prix_unitaire": "600"})
        self.assertFalse(form.is_valid())
        self.assertIn("quantite", form.errors)  # vente supérieure à la position
        form = InvestmentTransactionForm(user=self.alice, data={
            **base, "holding": h.pk, "type_operation": "VENTE", "quantite": "1", "prix_unitaire": "600"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().plus_value_realisee, D("98.13"))  # 598,50 − 2001,50/4 = 98,125
        # Nouveau titre dont la première opération n'est pas un achat
        form = InvestmentTransactionForm(user=self.alice, data={
            **base, "ticker_symbole": "AI", "nom_action": "Air Liquide", "type_operation": "DIVIDENDE",
            "quantite": "1", "prix_unitaire": "3"})
        self.assertIn("type_operation", form.errors)

    def test_plans_budgetaires(self):
        donnees = {"nom": "P1", "annee_debut": 2026, "duree_annees": 5, "taux_indexation": "2", "actif": "on"}
        p1 = BudgetPlanForm(user=self.alice, data=donnees).save()
        form = BudgetPlanForm(user=self.alice, data={**donnees, "nom": "P2"})
        self.assertTrue(form.is_valid(), form.errors)
        p2 = form.save()
        p1.refresh_from_db()
        self.assertEqual((p1.actif, p2.actif), (False, True))

        form = BudgetForm(user=self.alice, plan=p2, data={
            "categorie": self.alim.pk, "montant_reference": "400", "periodicite": "MENSUELLE", "mois_reference": "1"})
        self.assertTrue(form.is_valid(), form.errors)
        ligne = form.save()
        self.assertEqual(ligne.montants.count(), 60)
        doublon = BudgetForm(user=self.alice, plan=p2, data={
            "categorie": self.alim.pk, "montant_reference": "1", "periodicite": "MENSUELLE", "mois_reference": "1"})
        self.assertIn("Cette catégorie est déjà budgétée dans ce plan.", str(doublon.errors))

        cellule = ligne.montants.get(annee=2026, mois=3)
        f = BudgetAllocationForm(instance=cellule, data={"montant": "450,00"})
        self.assertTrue(f.is_valid(), f.errors)
        self.assertTrue(f.save().saisi_manuellement)

    def test_recherche(self):
        mk = lambda **kw: Transaction.objects.create(user=self.alice, compte=self.courant, **kw)
        mk(montant=D("50"), date=date(2026, 3, 2), sous_categorie=self.courses, tiers_payeur="Carrefour Market")
        mk(montant=D("80"), date=date(2026, 3, 9), sous_categorie=self.resto, tiers_payeur="Chez Paul")
        mk(montant=D("2500"), date=date(2026, 3, 1), type="REVENU", categorie=self.salaire, tiers_payeur="Employeur")
        mk(montant=D("12"), date=date(2026, 2, 1), description="Divers")
        Transaction.objects.create(user=self.bob, compte=self.compte_bob, montant=D("50"), tiers_payeur="Carrefour")

        def chercher(**criteres):
            return list(RechercheForm(user=self.alice, data=criteres).filtrer().values_list("montant", flat=True))

        self.assertEqual(chercher(q="carrefour"), [D("50")])
        self.assertEqual(chercher(categories=[self.alim.pk], tri="montant"), [D("50"), D("80")])
        self.assertEqual(chercher(montant_min="60", types=["DEPENSE"]), [D("80")])
        self.assertEqual(chercher(sans_categorie="on"), [D("12")])
        self.assertEqual(chercher(periode="perso", date_debut="2026-03-01", date_fin="2026-03-05", tri="date"),
                         [D("2500"), D("50")])
        self.assertEqual(chercher(montant_min="100", montant_max="10"), [])
        form = RechercheForm(user=self.alice, data={"q": "paul", "comptes": [self.courant.pk]})
        form.is_valid()
        self.assertEqual(form.criteres_actifs(), ["« paul »", "Courant"])


class AdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root", "r@ex.com", "x")
        self.staff = User.objects.create_user("staff", "s@ex.com", "x", is_staff=True)
        from django.contrib.auth.models import Permission
        self.staff.user_permissions.set(Permission.objects.filter(content_type__app_label="finances"))
        self.autre = User.objects.create_user("autre", "o@ex.com", "x")
        self.compte_autre = Account.objects.create(user=self.autre, nom="Secret")
        self.compte_staff = Account.objects.create(user=self.staff, nom="Perso")
        self.cto = Account.objects.create(user=self.autre, nom="CTO", type=TypeCompte.TITRE)
        cat = Category.objects.create(user=self.autre, nom="Alim")
        SubCategory.objects.create(categorie=cat, nom="Courses")
        Transaction.objects.create(user=self.autre, compte=self.compte_autre, montant=D("9"), categorie=cat)
        h = Holding.objects.create(user=self.autre, compte_titre=self.cto, ticker_symbole="AI", nom_action="Air Liquide",
                                   dernier_cours=D("180"))
        InvestmentTransaction.objects.create(user=self.autre, compte_titre=self.cto, holding=h, type_operation="ACHAT",
                                             quantite=D("2"), prix_unitaire=D("150"))
        plan = BudgetPlan.objects.create(user=self.autre, nom="Plan", annee_debut=2026)
        Budget.objects.create(user=self.autre, plan=plan, categorie=cat, montant_reference=D("100"))

    def test_toutes_les_pages_admin_superuser(self):
        self.client.force_login(self.admin)
        for modele in ("user", "tauxchange"):
            self.assertEqual(self.client.get(reverse(f"admin:core_{modele}_changelist")).status_code, 200)
        for modele in ("account", "category", "subcategory", "transaction", "holding",
                       "investmenttransaction", "courshistorique", "budgetplan", "budget"):
            for vue in ("changelist", "add"):
                r = self.client.get(reverse(f"admin:finances_{modele}_{vue}"))
                self.assertEqual(r.status_code, 200, f"{modele} {vue}")
        for obj, nom in [(Holding.objects.get(), "holding"), (BudgetPlan.objects.get(), "budgetplan"),
                         (Budget.objects.get(), "budget"), (Category.objects.get(), "category"),
                         (Transaction.objects.get(), "transaction"), (self.cto, "account")]:
            r = self.client.get(reverse(f"admin:finances_{nom}_change", args=[obj.pk]))
            self.assertEqual(r.status_code, 200, nom)

    def test_staff_ne_voit_que_ses_donnees(self):
        self.client.force_login(self.staff)
        r = self.client.get(reverse("admin:finances_account_changelist"))
        self.assertContains(r, "Perso")
        self.assertNotContains(r, "Secret")
        r = self.client.get(reverse("admin:finances_account_change", args=[self.compte_autre.pk]))
        self.assertEqual(r.status_code, 302)  # objet introuvable pour lui → redirection
        # Création : l'utilisateur est affecté automatiquement, la contrainte d'unicité est validée
        donnees = {"nom": "Perso", "type": "COURANT", "devise": "EUR", "solde_initial": "0",
                   "date_creation": "2026-01-01", "couleur": "#000000", "ordre": "0"}
        r = self.client.post(reverse("admin:finances_account_add"), donnees)
        self.assertContains(r, "Vous avez déjà un compte portant ce nom.")
        self.client.post(reverse("admin:finances_account_add"), {**donnees, "nom": "Livret"})
        self.assertEqual(Account.objects.get(nom="Livret").user, self.staff)

    def test_creation_categorie_avec_inline(self):
        self.client.force_login(self.staff)
        r = self.client.post(reverse("admin:finances_category_add"), {
            "nom": "Logement", "type": "DEPENSE", "couleur": "#112233", "ordre": "0",
            "sous_categories-TOTAL_FORMS": "1", "sous_categories-INITIAL_FORMS": "0",
            "sous_categories-0-nom": "Loyer", "sous_categories-0-ordre": "0",
        })
        self.assertEqual(r.status_code, 302, r.content[:3000])
        self.assertEqual(SubCategory.objects.get(nom="Loyer").user, self.staff)

    def test_suppression_groupee_operations_titres_recalcule(self):
        self.client.force_login(self.admin)
        op = InvestmentTransaction.objects.get()
        self.client.post(reverse("admin:finances_investmenttransaction_changelist"),
                         {"action": "delete_selected", "_selected_action": [op.pk], "post": "yes"})
        self.assertEqual(Holding.objects.get().quantite, 0)


class TotauxPlanAdminTests(TestCase):
    def test_totaux_premiere_annee_sans_doublons(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory
        u = User.objects.create_superuser("root", "r@ex.com", "x")
        dep = Category.objects.create(user=u, nom="Alim")
        dep2 = Category.objects.create(user=u, nom="Loisirs")
        rev = Category.objects.create(user=u, nom="Salaire", type=TypeFlux.REVENU)
        plan = BudgetPlan.objects.create(user=u, nom="P", annee_debut=2026)
        Budget.objects.create(user=u, plan=plan, categorie=dep, montant_reference=D("100"))
        Budget.objects.create(user=u, plan=plan, categorie=dep2, montant_reference=D("50"))
        Budget.objects.create(user=u, plan=plan, categorie=rev, montant_reference=D("2000"))
        requete = RequestFactory().get("/")
        requete.user = u
        p = site._registry[BudgetPlan].get_queryset(requete).get()
        self.assertEqual((p.nb, p.total_dep, p.total_rev), (3, D("1800"), D("24000")))
