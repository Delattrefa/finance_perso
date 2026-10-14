"""Exports de données (CSV ; le PDF sera ajouté à l'étape 3)."""
from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence

from django.http import HttpResponse
from django.utils import timezone
from django.utils.text import slugify


def reponse_csv(nom: str, entetes: Sequence[str], lignes: Iterable[Sequence]) -> HttpResponse:
    """CSV compatible Excel FR : séparateur « ; », virgule décimale, BOM UTF-8."""
    reponse = HttpResponse(content_type="text/csv; charset=utf-8")
    horodatage = timezone.localtime().strftime("%Y%m%d-%H%M")
    reponse["Content-Disposition"] = f'attachment; filename="{slugify(nom)}-{horodatage}.csv"'
    reponse.write("﻿")
    writer = csv.writer(reponse, delimiter=";")
    writer.writerow(entetes)
    for ligne in lignes:
        writer.writerow([_cellule(v) for v in ligne])
    return reponse


def _cellule(valeur) -> str:
    if valeur is None:
        return ""
    if hasattr(valeur, "strftime"):
        return valeur.strftime("%d/%m/%Y")
    if isinstance(valeur, bool):
        return "oui" if valeur else "non"
    texte = str(valeur)
    if hasattr(valeur, "as_tuple"):  # Decimal
        texte = texte.replace(".", ",")
    return texte


def lignes_transactions(queryset):
    """Lignes d'export standard d'un queryset de Transaction."""
    for t in queryset.select_related("compte", "compte_destination", "categorie", "sous_categorie"):
        yield [
            t.date,
            t.get_type_display(),
            t.compte.nom,
            t.compte_destination.nom if t.compte_destination else "",
            t.montant_signe(),
            t.compte.devise,
            t.categorie.nom if t.categorie else "",
            t.sous_categorie.nom if t.sous_categorie else "",
            t.tiers_payeur,
            t.description,
            t.get_statut_display(),
        ]


ENTETES_TRANSACTIONS = [
    "Date", "Type", "Compte", "Compte destination", "Montant", "Devise",
    "Catégorie", "Sous-catégorie", "Tiers", "Description", "Statut",
]


# ---------------------------------------------------------------------------
# PDF (reportlab : aucune dépendance système)
# ---------------------------------------------------------------------------
def reponse_pdf_transactions(queryset, titre: str, criteres: Sequence[str], totaux: dict) -> HttpResponse:
    from io import BytesIO
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from core.formatage import formater_montant

    tampon = BytesIO()
    genere_le = timezone.localtime().strftime("%d/%m/%Y à %H:%M")

    def pied_de_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(12 * mm, 8 * mm, f"PyFinance · {titre} · généré le {genere_le}")
        canvas.drawRightString(doc.pagesize[0] - 12 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        tampon, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=14 * mm, title=titre, author="PyFinance",
    )
    styles = getSampleStyleSheet()
    cellule = ParagraphStyle("cellule", parent=styles["BodyText"], fontSize=8, leading=10)
    devise = totaux.get("devise", "EUR")

    elements = [Paragraph(escape(titre), styles["Title"])]
    if criteres:
        elements.append(Paragraph("Critères : " + escape(" · ".join(criteres)), styles["Italic"]))
    elements.append(Paragraph(
        f"Revenus : <b>{formater_montant(totaux['revenus'], devise)}</b> &nbsp;·&nbsp; "
        f"Dépenses : <b>{formater_montant(totaux['depenses'], devise)}</b> &nbsp;·&nbsp; "
        f"Solde : <b>{formater_montant(totaux['solde'], devise, signe=True)}</b>",
        styles["Normal"],
    ))
    elements.append(Spacer(0, 5 * mm))

    donnees = [["Date", "Compte", "Type", "Catégorie", "Tiers", "Description", "Statut", "Montant"]]
    styles_lignes = []
    for i, t in enumerate(
        queryset.select_related("compte", "compte_destination", "categorie", "sous_categorie"), start=1
    ):
        if t.est_virement:
            classement = f"→ {t.compte_destination.nom}"
        else:
            classement = str(t.sous_categorie) if t.sous_categorie else (t.categorie.nom if t.categorie else "—")
        montant = t.montant_signe()
        donnees.append([
            t.date.strftime("%d/%m/%Y"),
            Paragraph(escape(t.compte.nom), cellule),
            t.get_type_display(),
            Paragraph(escape(classement), cellule),
            Paragraph(escape(t.tiers_payeur), cellule),
            Paragraph(escape(t.description), cellule),
            t.get_statut_display(),
            formater_montant(montant, t.compte.devise, signe=True),
        ])
        if not t.est_virement:
            couleur = "#15803D" if montant > 0 else "#B91C1C"
            styles_lignes.append(("TEXTCOLOR", (7, i), (7, i), colors.HexColor(couleur)))

    largeurs = [x * mm for x in (20, 35, 26, 45, 42, 70, 22, 30)]
    table = Table(donnees, colWidths=largeurs, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F9")]),
        ("ALIGN", (7, 0), (7, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#1E293B")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        *styles_lignes,
    ]))
    elements.append(table)
    if len(donnees) == 1:
        elements.append(Paragraph("Aucune opération ne correspond à ces critères.", styles["Normal"]))

    doc.build(elements, onFirstPage=pied_de_page, onLaterPages=pied_de_page)
    reponse = HttpResponse(tampon.getvalue(), content_type="application/pdf")
    horodatage = timezone.localtime().strftime("%Y%m%d-%H%M")
    reponse["Content-Disposition"] = f'attachment; filename="{slugify(titre)}-{horodatage}.pdf"'
    return reponse
