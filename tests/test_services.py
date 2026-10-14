from datetime import date
from decimal import Decimal as D

from django.test import TestCase

from core.models import TauxChange, User
from finances.models import (
    Account, Budget, BudgetPlan, Category, Holding, InvestmentTransaction, SubCategory, Transaction,
    TypeCompte, TypeFlux,
)
from finances.services import budget, rapports

AUJ = date(2026, 3, 15)


class Donnees(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("alice", "a@ex.com", "x")
        self.courant = Account.objects.create(user=self.u, nom="Courant", solde_initial=D("1000"), date_creation=date(2025, 1, 1))
        self.livret = Account.objects.create(user=self.u, nom="Livret", type=TypeCompte.EPARGNE, date_creation=date(2025, 1, 1))
        self.alim = Category.objects.create(user=self.u, nom="Alimentation", couleur="#22C55E")
        self.courses = SubCategory.objects.create(categorie=self.alim, nom="Courses")
        self.resto = SubCategory.objects.create(categorie=self.alim, nom="Restaurant")
        self.loisirs = Category.objects.create(user=self.u, nom="Loisirs")
        self.salaire = Category.objects.create(user=self.u, nom="Salaire", type=TypeFlux.REVENU)
        op = lambda **kw: Transaction.objects.create(user=self.u, compte=self.courant, **kw)
        op(montant=D("350"), date=date(2026, 3, 2), sous_categorie=self.courses)
        op(montant=D("100"), date=date(2026, 3, 5), sous_categorie=self.resto)
        op(montant=D("50"), date=date(2026, 3, 6), categorie=self.loisirs)
        op(montant=D("20"), date=date(2026, 3, 7))
        op(montant=D("3000"), date=date(2026, 3, 1), type="REVENU", categorie=self.salaire)
        op(montant=D("500"), date=date(2026, 3, 8), type="VIREMENT_INTERNE", compte_destination=self.livret)
        op(montant=D("200"), date=date(2026, 2, 10), sous_categorie=self.courses)
        op(montant=D("2800"), date=date(2026, 2, 1), type="REVENU", categorie=self.salaire)


class MatriceBudgetTests(Donnees):
    def setUp(self):
        super().setUp()
        self.plan = BudgetPlan.objects.create(user=self.u, nom="Plan", annee_debut=2026, taux_indexation=D("0"))
        Budget.objects.create(user=self.u, plan=self.plan, categorie=self.alim, montant_reference=D("400"))
        Budget.objects.create(user=self.u, plan=self.plan, categorie=self.alim, sous_categorie=self.courses, montant_reference=D("300"))
        Budget.objects.create(user=self.u, plan=self.plan, categorie=self.salaire, montant_reference=D("3000"))

    def ligne(self, matrice, libelle):
        return next(l for l in matrice.toutes_les_lignes if l.libelle == libelle)

    def test_vue_mensuelle(self):
        m = budget.construire_matrice(self.plan, "mensuelle", 2026, aujourd_hui=AUJ)
        self.assertEqual(len(m.colonnes), 13)
        mars = 2
        courses = self.ligne(m, "Courses").cellules[mars]
        self.assertEqual((courses.prevu, courses.reel, courses.statut), (D("300"), D("350"), "depassement"))
        autres = self.ligne(m, "Autres / non détaillé").cellules[mars]  # restaurant → ligne catégorie entière
        self.assertEqual((autres.prevu, autres.reel, autres.statut), (D("400"), D("100"), "ok"))
        groupes = {g.total.libelle: g for g in m.depenses.groupes}
        self.assertEqual(list(groupes), ["Alimentation", "Loisirs", "Non catégorisé"])
        self.assertTrue(groupes["Loisirs"].lignes[0].hors_budget)
        self.assertEqual(groupes["Alimentation"].total.cellules[mars].reel, D("450"))
        self.assertEqual(m.depenses.total.cellules[mars].reel, D("520"))  # virement exclu
        self.assertEqual(m.solde.cellules[mars].prevu, D("2300"))
        self.assertEqual(m.solde.cellules[mars].reel, D("2480"))
        self.assertEqual(self.ligne(m, "Courses").cellules[3].statut, "a_venir")
        # Projection annuelle Courses : 0 (janv.) + 200 (févr.) + max(350, 300) + 9 × 300
        self.assertEqual(self.ligne(m, "Courses").total.projection, D("3250"))
        self.assertIsNotNone(courses.allocation_id)
        self.assertIn("Courses", [l.libelle for l, _ in m.alertes(mars)])
        salaire = self.ligne(m, "Salaire").cellules[1]
        self.assertEqual(salaire.statut, "insuffisant")  # février terminé, 2800 < 3000

    def test_vues_annuelle_et_quinquennale(self):
        m = budget.construire_matrice(self.plan, "annuelle", 2026, aujourd_hui=AUJ)
        self.assertEqual([c.cle for c in m.colonnes], ["T1", "T2", "T3", "T4", "cumul", "total"])
        self.assertEqual(self.ligne(m, "Courses").cellules[4].prevu, D("900"))  # cumul janv.–mars
        m = budget.construire_matrice(self.plan, "quinquennale", aujourd_hui=AUJ)
        self.assertEqual([c.libelle for c in m.colonnes], ["2026", "2027", "2028", "2029", "2030", "Total 5 ans"])
        self.assertEqual(self.ligne(m, "Courses").total.prevu, D("18000"))
        # Année hors plan → année par défaut
        self.assertEqual(budget.construire_matrice(self.plan, "mensuelle", 1999, aujourd_hui=AUJ).annee, 2026)

    def test_resume_mois(self):
        r = budget.resume_mois(self.u, 2026, 3, aujourd_hui=AUJ)
        self.assertEqual(r["depenses"].reel, D("520"))
        self.assertEqual(r["alertes"][0][0].libelle, "Courses")
        self.assertIsNone(budget.resume_mois(self.u, 2040, 1))


class RapportsTests(Donnees):
    def test_totaux_et_taux_epargne(self):
        t = rapports.totaux_periode(self.u, date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual((t["revenus"], t["depenses"], t["solde"]), (D("3000"), D("520"), D("2480")))
        self.assertEqual(t["taux_epargne"], D("82.7"))

    def test_repartition_et_comparatif(self):
        rep = rapports.repartition_categories(self.u, date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual([c["nom"] for c in rep["categories"]], ["Alimentation", "Loisirs", "Non catégorisé"])
        self.assertEqual(rep["categories"][0]["sous_categories"][0]["nom"], "Courses")
        self.assertEqual(rep["total"], D("520"))
        comp = rapports.comparatif_mois(self.u, 2026, 3)
        alim = comp["sections"][0]["lignes"][0]
        self.assertEqual((alim["a"], alim["b"], alim["variation"], alim["variation_pct"]), (D("450"), D("200"), D("250"), D("125.0")))
        self.assertEqual(comp["libelle_b"], "Février 2026")

    def test_patrimoine_multidevise_et_historique(self):
        usd = Account.objects.create(user=self.u, nom="US", devise="USD", solde_initial=D("100"), date_creation=date(2025, 6, 1))
        p = rapports.patrimoine(self.u)
        self.assertIn("USD", p.avertissements[0])  # pas encore de taux
        TauxChange.objects.create(devise_source="EUR", devise_cible="USD", date=date(2025, 1, 1), taux=D("1.25"))
        p = rapports.patrimoine(self.u)
        # Courant 1000 + 2800 + 3000 − 200 − 350 − 100 − 50 − 20 − 500 = 5580 ; livret 500 ; 100 USD = 80 €
        self.assertEqual(p.total, D("6160"))
        self.assertEqual(p.avertissements, [])
        h = rapports.historique_patrimoine(self.u, 3, AUJ)
        self.assertEqual(h["total"][-1], p.total)
        self.assertEqual(h["dates"], [date(2026, 1, 31), date(2026, 2, 28), AUJ])
        self.assertEqual(h["total"][0], D("1080"))  # janvier : soldes initiaux uniquement
        self.assertEqual(rapports.historique_patrimoine(self.u, 24, AUJ)["comptes"][2]["valeurs"][0], None)  # US pas encore ouvert
        del usd

    def test_titres_dans_patrimoine_et_rapport(self):
        cto = Account.objects.create(user=self.u, nom="CTO", type=TypeCompte.TITRE, date_creation=date(2025, 1, 1))
        Transaction.objects.create(user=self.u, compte=self.courant, type="VIREMENT_INTERNE", compte_destination=cto,
                                   montant=D("2000"), date=date(2026, 1, 5))
        h = Holding.objects.create(user=self.u, compte_titre=cto, ticker_symbole="CW8", nom_action="World", type_actif="ETF")
        mk = lambda **kw: InvestmentTransaction.objects.create(user=self.u, compte_titre=cto, holding=h, **kw)
        mk(type_operation="ACHAT", date=date(2026, 1, 10), quantite=D("10"), prix_unitaire=D("100"), frais=D("10"))
        mk(type_operation="VENTE", date=date(2026, 2, 10), quantite=D("5"), prix_unitaire=D("120"))
        mk(type_operation="DIVIDENDE", date=date(2026, 3, 1), quantite=D("5"), prix_unitaire=D("2"))
        h.mettre_a_jour_cours(D("110"), date(2026, 2, 20))
        r = rapports.rapport_titres(self.u)
        self.assertEqual(r["valeur"], D("550"))
        self.assertEqual(r["cout"], D("505"))
        self.assertEqual(r["pv_latente"], D("45"))
        self.assertEqual(r["pv_realisee"], D("95"))  # 600 − 505
        self.assertEqual(r["dividendes"], D("10"))
        self.assertEqual(r["liquidites"], D("1600"))  # 2000 − 1010 + 600 + 10
        self.assertEqual(r["performance"], D("150"))
        self.assertEqual(r["investi"], D("1010"))
        self.assertEqual(r["allocation_types"][0]["libelle"], "ETF / Tracker")
        self.assertEqual(r["par_annee"][0]["pv_realisee"], D("95"))
        hist = rapports.historique_patrimoine(self.u, 3, AUJ)
        self.assertEqual(hist["total"][-1], rapports.patrimoine(self.u).total)
        cto_serie = next(s for s in hist["comptes"] if s["nom"] == "CTO")["valeurs"]
        # Fin janvier : 990 € de liquidités + 10 titres au PMPA (aucun cours avant le 20/02)
        self.assertEqual(cto_serie[0], D("2000.00"))
        self.assertEqual(cto_serie[1], D("2140.00"))  # 1590 + 5 × 110
