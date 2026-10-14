"""
Jeu de catégories proposé à l'inscription (icônes : noms Lucide).

Couleurs : palette catégorielle validée (séparation daltonisme, contraste) dans un ordre fixe
pour les postes principaux ; au-delà de 8 teintes, les postes mineurs réutilisent une teinte
(la légende des graphiques porte toujours le nom, jamais la couleur seule).
"""
from django.db import transaction as db_transaction

from ..models import Category, SubCategory, TypeFlux

CATEGORIES_PAR_DEFAUT = [
    # (type, nom, icône, couleur, [sous-catégories])
    (TypeFlux.DEPENSE, "Logement", "home", "#2A78D6", ["Loyer / crédit", "Charges", "Énergie", "Eau", "Assurance habitation", "Entretien"]),
    (TypeFlux.DEPENSE, "Alimentation", "shopping-cart", "#EB6834", ["Courses", "Restaurant", "Livraison"]),
    (TypeFlux.DEPENSE, "Transport", "car", "#1BAF7A", ["Carburant", "Transports en commun", "Entretien véhicule", "Assurance auto", "Parking / péages"]),
    (TypeFlux.DEPENSE, "Santé", "heart-pulse", "#008300", ["Médecin", "Pharmacie", "Mutuelle"]),
    (TypeFlux.DEPENSE, "Loisirs", "party-popper", "#EDA100", ["Sorties", "Sport", "Vacances", "Culture", "Abonnements"]),
    (TypeFlux.DEPENSE, "Shopping", "shopping-bag", "#6250D6", ["Vêtements", "High-tech", "Maison"]),
    (TypeFlux.DEPENSE, "Communication", "smartphone", "#E34948", ["Téléphone", "Internet"]),
    (TypeFlux.DEPENSE, "Impôts & taxes", "landmark", "#E87BA4", ["Impôt sur le revenu", "Taxe foncière", "Autres taxes"]),
    (TypeFlux.DEPENSE, "Banque & frais", "credit-card", "#6250D6", ["Frais bancaires", "Intérêts"]),
    (TypeFlux.DEPENSE, "Enfants", "baby", "#2A78D6", ["Garde", "Scolarité", "Activités"]),
    (TypeFlux.DEPENSE, "Dons & cadeaux", "gift", "#1BAF7A", ["Cadeaux", "Dons"]),
    (TypeFlux.REVENU, "Salaires", "briefcase", "#2A78D6", ["Salaire", "Primes"]),
    (TypeFlux.REVENU, "Revenus du patrimoine", "trending-up", "#EB6834", ["Intérêts", "Dividendes", "Loyers perçus"]),
    (TypeFlux.REVENU, "Aides & remboursements", "hand-coins", "#1BAF7A", ["Allocations", "Remboursements santé", "Remboursements divers"]),
    (TypeFlux.REVENU, "Autres revenus", "plus-circle", "#EDA100", ["Ventes", "Divers"]),
]


@db_transaction.atomic
def creer_categories_par_defaut(user) -> int:
    """Crée les catégories manquantes (idempotent). Renvoie le nombre d'éléments créés."""
    crees = 0
    for ordre, (type_flux, nom, icone, couleur, sous_categories) in enumerate(CATEGORIES_PAR_DEFAUT):
        categorie, cree = Category.objects.get_or_create(
            user=user, type=type_flux, nom=nom, defaults={"icone": icone, "couleur": couleur, "ordre": ordre}
        )
        crees += cree
        for ordre_sc, nom_sc in enumerate(sous_categories):
            _, cree = SubCategory.objects.get_or_create(
                user=user, categorie=categorie, nom=nom_sc, defaults={"ordre": ordre_sc}
            )
            crees += cree
    return crees
