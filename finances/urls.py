from django.urls import path

from .views import budget, categories, comptes, dashboard, operations, rapports, recherche, titres

app_name = "finances"

urlpatterns = [
    path("", dashboard.DashboardView.as_view(), name="dashboard"),

    # Comptes
    path("comptes/", comptes.AccountListView.as_view(), name="comptes"),
    path("comptes/ajouter/", comptes.AccountCreateView.as_view(), name="compte_ajouter"),
    path("comptes/<int:pk>/", comptes.AccountDetailView.as_view(), name="compte"),
    path("comptes/<int:pk>/modifier/", comptes.AccountUpdateView.as_view(), name="compte_modifier"),
    path("comptes/<int:pk>/supprimer/", comptes.AccountDeleteView.as_view(), name="compte_supprimer"),

    # Catégories
    path("categories/", categories.CategoryListView.as_view(), name="categories"),
    path("categories/ajouter/", categories.CategoryCreateView.as_view(), name="categorie_ajouter"),
    path("categories/par-defaut/", categories.categories_par_defaut, name="categories_par_defaut"),
    path("categories/<int:pk>/modifier/", categories.CategoryUpdateView.as_view(), name="categorie_modifier"),
    path("categories/<int:pk>/supprimer/", categories.CategoryDeleteView.as_view(), name="categorie_supprimer"),
    path("sous-categories/ajouter/", categories.SubCategoryCreateView.as_view(), name="sous_categorie_ajouter"),
    path("sous-categories/<int:pk>/modifier/", categories.SubCategoryUpdateView.as_view(), name="sous_categorie_modifier"),
    path("sous-categories/<int:pk>/supprimer/", categories.SubCategoryDeleteView.as_view(), name="sous_categorie_supprimer"),

    # Opérations & recherche
    path("operations/", recherche.RechercheView.as_view(), name="recherche"),
    path("operations/ajouter/", operations.TransactionCreateView.as_view(), name="operation_ajouter"),
    path("operations/<int:pk>/modifier/", operations.TransactionUpdateView.as_view(), name="operation_modifier"),
    path("operations/<int:pk>/supprimer/", operations.TransactionDeleteView.as_view(), name="operation_supprimer"),
    path("operations/<int:pk>/statut/", operations.basculer_statut, name="operation_statut"),

    # Compte titres
    path("titres/", titres.PortefeuilleView.as_view(), name="portefeuille"),
    path("titres/operations/ajouter/", titres.InvestmentTransactionCreateView.as_view(), name="titre_operation_ajouter"),
    path("titres/operations/<int:pk>/modifier/", titres.InvestmentTransactionUpdateView.as_view(), name="titre_operation_modifier"),
    path("titres/operations/<int:pk>/supprimer/", titres.InvestmentTransactionDeleteView.as_view(), name="titre_operation_supprimer"),
    path("titres/positions/<int:pk>/", titres.HoldingDetailView.as_view(), name="position"),
    path("titres/positions/<int:pk>/modifier/", titres.HoldingUpdateView.as_view(), name="position_modifier"),
    path("titres/positions/<int:pk>/supprimer/", titres.HoldingDeleteView.as_view(), name="position_supprimer"),
    path("titres/positions/<int:pk>/cours/", titres.mettre_a_jour_cours, name="position_cours"),

    # Budget
    path("budget/", budget.budget_accueil, name="budget"),
    path("budget/plans/", budget.BudgetPlanListView.as_view(), name="budget_plans"),
    path("budget/plans/ajouter/", budget.BudgetPlanCreateView.as_view(), name="budget_plan_ajouter"),
    path("budget/plans/<int:pk>/", budget.BudgetMatriceView.as_view(), name="budget_matrice"),
    path("budget/plans/<int:pk>/modifier/", budget.BudgetPlanUpdateView.as_view(), name="budget_plan_modifier"),
    path("budget/plans/<int:pk>/supprimer/", budget.BudgetPlanDeleteView.as_view(), name="budget_plan_supprimer"),
    path("budget/plans/<int:pk>/activer/", budget.activer_plan, name="budget_plan_activer"),
    path("budget/plans/<int:pk>/recalculer/", budget.regenerer_plan, name="budget_plan_recalculer"),
    path("budget/plans/<int:plan_pk>/lignes/ajouter/", budget.BudgetLigneCreateView.as_view(), name="budget_ligne_ajouter"),
    path("budget/lignes/<int:pk>/modifier/", budget.BudgetLigneUpdateView.as_view(), name="budget_ligne_modifier"),
    path("budget/lignes/<int:pk>/supprimer/", budget.BudgetLigneDeleteView.as_view(), name="budget_ligne_supprimer"),
    path("budget/cellules/<int:pk>/", budget.editer_cellule, name="budget_cellule"),
    path("budget/cellules/<int:pk>/reinitialiser/", budget.reinitialiser_cellule, name="budget_cellule_reinitialiser"),

    # Rapports
    path("rapports/categories/", rapports.RapportCategoriesView.as_view(), name="rapport_categories"),
    path("rapports/patrimoine/", rapports.RapportPatrimoineView.as_view(), name="rapport_patrimoine"),
    path("rapports/comparatifs/", rapports.RapportComparatifsView.as_view(), name="rapport_comparatifs"),
    path("rapports/titres/", rapports.RapportTitresView.as_view(), name="rapport_titres"),

    # Listes dépendantes (HTMX)
    path("htmx/categories/", categories.options_categories, name="options_categories"),
    path("htmx/sous-categories/", categories.options_sous_categories, name="options_sous_categories"),
    path("htmx/positions/", titres.options_positions, name="options_positions"),
]
