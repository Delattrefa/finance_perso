"""
Crée un utilisateur de démonstration avec ~18 mois d'historique réaliste.

    python manage.py donnees_demo                 # utilisateur « demo » / mot de passe « demo »
    python manage.py donnees_demo --reinitialiser # supprime et recrée
"""
import random
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from finances.models import (
    Account, Budget, BudgetPlan, Category, Holding, InvestmentTransaction, Periodicite, StatutTransaction,
    SubCategory, Transaction, TypeCompte, TypeTransaction,
)
from finances.services.categories_defaut import creer_categories_par_defaut
from finances.services.periodes import decaler_mois, fin_de_mois

NB_MOIS = 18


class Command(BaseCommand):
    help = "Crée un utilisateur « demo » avec des comptes, opérations, titres et un budget sur 5 ans."

    def add_arguments(self, parser):
        parser.add_argument("--utilisateur", default="demo")
        parser.add_argument("--mot-de-passe", default="demo")
        parser.add_argument("--reinitialiser", action="store_true", help="Supprime l'utilisateur existant avant de recréer.")

    @transaction.atomic
    def handle(self, *args, utilisateur, mot_de_passe, reinitialiser, **options):
        User = get_user_model()
        existant = User.objects.filter(username=utilisateur).first()
        if existant and not reinitialiser:
            raise CommandError(f"L'utilisateur « {utilisateur} » existe déjà (utilisez --reinitialiser).")
        if existant:
            existant.delete()
        self.alea = random.Random(42)
        self.aujourd_hui = timezone.localdate()
        u = self.u = User.objects.create_user(utilisateur, f"{utilisateur}@example.com", mot_de_passe, first_name="Camille")
        creer_categories_par_defaut(u)
        self.debut = decaler_mois(self.aujourd_hui, -(NB_MOIS - 1))

        self.courant = Account.objects.create(user=u, nom="Compte courant", institution="Banque Populaire", couleur="#3B82F6",
                                              icone="wallet", solde_initial=D("2450"), date_creation=self.debut, ordre=1)
        self.livret = Account.objects.create(user=u, nom="Livret A", type=TypeCompte.EPARGNE, couleur="#22C55E",
                                             icone="piggy-bank", solde_initial=D("8000"), date_creation=self.debut, ordre=2)
        self.cto = Account.objects.create(user=u, nom="Compte titres", type=TypeCompte.TITRE, institution="Boursorama",
                                          couleur="#A855F7", icone="chart-candlestick", solde_initial=D("1500"),
                                          date_creation=self.debut, ordre=3)
        self.operations()
        self.titres()
        self.budget()
        self.stdout.write(self.style.SUCCESS(
            f"Utilisateur « {utilisateur} » créé : {Transaction.objects.filter(user=u).count()} opérations, "
            f"{InvestmentTransaction.objects.filter(user=u).count()} opérations sur titres. Mot de passe : {mot_de_passe}"
        ))

    # --- utilitaires ---
    def sc(self, categorie, sous_categorie):
        return SubCategory.objects.get(user=self.u, categorie__nom=categorie, nom=sous_categorie)

    def op(self, jour, montant, sous_categorie=None, type_=TypeTransaction.DEPENSE, tiers="", **kw):
        if jour > self.aujourd_hui:
            return
        statut = StatutTransaction.RAPPROCHE if (self.aujourd_hui - jour).days > 5 else StatutTransaction.EN_ATTENTE
        Transaction.objects.create(user=self.u, compte=kw.pop("compte", self.courant), type=type_, date=jour,
                                   montant=D(montant).quantize(D("0.01")), sous_categorie=sous_categorie,
                                   tiers_payeur=tiers, statut=statut, **kw)

    def montant(self, mini, maxi):
        return D(str(round(self.alea.uniform(mini, maxi), 2)))

    # --- historique ---
    def operations(self):
        a = self.alea
        salaire, prime = self.sc("Salaires", "Salaire"), self.sc("Salaires", "Primes")
        loyer, energie, assurance = self.sc("Logement", "Loyer / crédit"), self.sc("Logement", "Énergie"), self.sc("Logement", "Assurance habitation")
        courses, resto = self.sc("Alimentation", "Courses"), self.sc("Alimentation", "Restaurant")
        carburant, tec = self.sc("Transport", "Carburant"), self.sc("Transport", "Transports en commun")
        tel, internet = self.sc("Communication", "Téléphone"), self.sc("Communication", "Internet")
        sorties, sport, vacances, abonnements = (self.sc("Loisirs", n) for n in ("Sorties", "Sport", "Vacances", "Abonnements"))
        vetements, hightech = self.sc("Shopping", "Vêtements"), self.sc("Shopping", "High-tech")
        medecin, pharmacie = self.sc("Santé", "Médecin"), self.sc("Santé", "Pharmacie")
        impot, fonciere = self.sc("Impôts & taxes", "Impôt sur le revenu"), self.sc("Impôts & taxes", "Taxe foncière")
        frais, interets = self.sc("Banque & frais", "Frais bancaires"), self.sc("Revenus du patrimoine", "Intérêts")
        rembours = self.sc("Aides & remboursements", "Remboursements santé")

        for i in range(NB_MOIS):
            m = decaler_mois(self.debut, i)
            j = lambda jour: m.replace(day=min(jour, fin_de_mois(m.year, m.month).day))
            self.op(j(28), "3150.00" if m.year > self.debut.year else "3020.00", salaire, TypeTransaction.REVENU, "ACME SAS", description="Salaire net")
            if m.month == 12:
                self.op(j(20), "1800", prime, TypeTransaction.REVENU, "ACME SAS", description="Prime annuelle")
            self.op(j(3), "890", loyer, tiers="SCI Les Tilleuls", description="Loyer")
            self.op(j(8), self.montant(55, 140) if m.month in (11, 12, 1, 2, 3) else self.montant(35, 70), energie, tiers="EDF")
            self.op(j(10), "11.99", tel, tiers="Free Mobile")
            self.op(j(12), "29.99", internet, tiers="Orange")
            self.op(j(5), "13.49", abonnements, tiers="Netflix")
            self.op(j(15), "34.90", sport, tiers="Basic-Fit")
            self.op(j(15), "214", impot, tiers="DGFiP", description="Prélèvement à la source")
            self.op(j(2), "2.50", frais, tiers="Banque Populaire", description="Frais de tenue de compte")
            for semaine in range(4):
                self.op(j(2 + semaine * 7), self.montant(48, 125), courses, tiers=a.choice(["Carrefour", "Lidl", "Biocoop", "Monoprix"]))
            for _ in range(a.randint(1, 4)):
                self.op(j(a.randint(1, 28)), self.montant(18, 75), resto, tiers=a.choice(["Le Bistrot", "Sushi Shop", "Pizzeria Roma", "Boulangerie Paul"]))
            for _ in range(a.randint(1, 3)):
                self.op(j(a.randint(1, 28)), self.montant(40, 75), carburant, tiers=a.choice(["TotalEnergies", "Leclerc Station"]))
            self.op(j(1), "86.40", tec, tiers="RATP", description="Navigo")
            for _ in range(a.randint(0, 3)):
                self.op(j(a.randint(1, 28)), self.montant(12, 60), sorties, tiers=a.choice(["UGC", "Fnac", "Bar Le Central"]))
            if a.random() < .45:
                self.op(j(a.randint(1, 28)), self.montant(30, 160), vetements, tiers=a.choice(["Zara", "Uniqlo", "Decathlon"]))
            if a.random() < .12:
                self.op(j(a.randint(1, 28)), self.montant(150, 900), hightech, tiers="Boulanger")
            if a.random() < .35:
                self.op(j(a.randint(1, 28)), "30", medecin, tiers="Dr Martin")
                self.op(j(a.randint(1, 28)), "21.50", rembours, TypeTransaction.REVENU, "CPAM")
            if a.random() < .5:
                self.op(j(a.randint(1, 28)), self.montant(6, 35), pharmacie, tiers="Pharmacie du Centre")
            if m.month in (7, 8):
                self.op(j(a.randint(5, 20)), self.montant(600, 1400), vacances, tiers=a.choice(["Airbnb", "SNCF", "Air France"]))
            if m.month == 10:
                self.op(j(15), "1180", fonciere, tiers="DGFiP", description="Taxe foncière")
            if m.month == 3:
                self.op(j(20), "312", assurance, tiers="MAIF")
            if m.month == 12:
                self.op(j(31), self.montant(180, 260), interets, TypeTransaction.REVENU, "Banque Populaire", compte=self.livret,
                        description="Intérêts Livret A")
            # Épargne automatique
            self.op(j(29), "250", type_=TypeTransaction.VIREMENT_INTERNE, compte_destination=self.livret, description="Épargne mensuelle")
            self.op(j(29), "500", type_=TypeTransaction.VIREMENT_INTERNE, compte_destination=self.cto, description="Investissement mensuel")
            # Quelques opérations non catégorisées
            if a.random() < .3:
                self.op(j(a.randint(1, 28)), self.montant(5, 40), None, tiers="Paiement carte", description="À classer")

    def titres(self):
        a = self.alea
        lignes = [
            ("CW8", "Amundi MSCI World", "ETF", "Monde", 420.0, 0.009),
            ("ESE", "BNP S&P 500", "ETF", "États-Unis", 21.0, 0.011),
            ("AI", "Air Liquide", "ACTION", "Industrie", 165.0, 0.006),
            ("MC", "LVMH", "ACTION", "Luxe", 720.0, -0.004),
        ]
        for ticker, nom, type_actif, secteur, cours, tendance in lignes:
            h = Holding.objects.create(user=self.u, compte_titre=self.cto, ticker_symbole=ticker, nom_action=nom,
                                       type_actif=type_actif, secteur=secteur)
            for i in range(NB_MOIS):
                m = decaler_mois(self.debut, i)
                jour = min(m.replace(day=5), self.aujourd_hui)
                cours *= 1 + tendance + a.gauss(0, 0.035)
                prix = D(str(round(cours, 2)))
                h.mettre_a_jour_cours(prix, jour)
                achat = (ticker == "CW8" and i % 2 == 0) or (ticker == "ESE" and i % 2 == 1) or (ticker in ("AI", "MC") and i % 6 == 0)
                if achat and jour <= self.aujourd_hui:
                    quantite = 10 if ticker == "ESE" else 1
                    InvestmentTransaction.objects.create(user=self.u, compte_titre=self.cto, holding=h, type_operation="ACHAT",
                                                         date=jour, quantite=D(quantite), prix_unitaire=prix, frais=D("1.99"))
                if ticker == "AI" and m.month == 5 and h.quantite:
                    InvestmentTransaction.objects.create(user=self.u, compte_titre=self.cto, holding=h, type_operation="DIVIDENDE",
                                                         date=jour + timedelta(days=10), quantite=h.quantite, prix_unitaire=D("3.20"),
                                                         frais=(h.quantite * D("3.20") * D("0.128")).quantize(D("0.01")))
                    h.refresh_from_db()
                if ticker == "MC" and i == NB_MOIS - 4 and h.quantite:
                    InvestmentTransaction.objects.create(user=self.u, compte_titre=self.cto, holding=h, type_operation="VENTE",
                                                         date=jour + timedelta(days=3), quantite=D(1), prix_unitaire=prix, frais=D("1.99"))
                h.refresh_from_db()

    def budget(self):
        plan = BudgetPlan.objects.create(user=self.u, nom="Plan quinquennal", annee_debut=self.aujourd_hui.year,
                                         taux_indexation=D("2.00"), description="Inflation estimée à 2 % par an.")
        cat = lambda nom: Category.objects.get(user=self.u, nom=nom)
        lignes = [
            ("Salaires", "Salaire", "3150", Periodicite.MENSUELLE, 1, False, D("2.5")),
            ("Logement", "Loyer / crédit", "890", Periodicite.MENSUELLE, 1, False, D("3.0")),
            ("Logement", None, "110", Periodicite.MENSUELLE, 1, False, None),
            ("Alimentation", "Courses", "360", Periodicite.MENSUELLE, 1, False, None),
            ("Alimentation", "Restaurant", "90", Periodicite.MENSUELLE, 1, False, None),
            ("Transport", None, "190", Periodicite.MENSUELLE, 1, False, None),
            ("Communication", None, "42", Periodicite.MENSUELLE, 1, False, D("0")),
            ("Loisirs", None, "160", Periodicite.MENSUELLE, 1, False, None),
            ("Loisirs", "Vacances", "1800", Periodicite.ANNUELLE, 7, False, None),
            ("Shopping", None, "120", Periodicite.MENSUELLE, 1, False, None),
            ("Santé", None, "40", Periodicite.MENSUELLE, 1, False, None),
            ("Impôts & taxes", "Impôt sur le revenu", "214", Periodicite.MENSUELLE, 1, False, None),
            ("Impôts & taxes", "Taxe foncière", "1180", Periodicite.ANNUELLE, 10, True, None),
        ]
        for nom_cat, nom_sc, montant, periodicite, mois, lisser, taux in lignes:
            categorie = cat(nom_cat)
            sous_categorie = SubCategory.objects.get(categorie=categorie, nom=nom_sc) if nom_sc else None
            Budget.objects.create(user=self.u, plan=plan, categorie=categorie, sous_categorie=sous_categorie,
                                  montant_reference=D(montant), periodicite=periodicite, mois_reference=mois,
                                  lisser=lisser, taux_indexation=taux)
