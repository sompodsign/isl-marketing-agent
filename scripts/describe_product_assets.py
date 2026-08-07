"""Apply reviewed, image-specific labels and visual-context descriptions.

Run after ``import_product_assets.py``. The IDs are derived from the source paths,
so this updates the same assets in local and production databases.
"""

import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import connect, initialise


ASSET_DETAILS = {
    "jamuna_model_diagnostic/logo.jpg": (
        "Jamuna Model Diagnostic — Diagnostic Centre Logo",
        "Circular green-and-white logo with Bengali text, the English name “Jamuna Model Diagnostic”, and a microscope icon. Suitable as a partner or client brand visual; it is not a LabLink interface screenshot.",
    ),
    "lablink/logo.png": (
        "LabLink — Diagnostic Workflow Icon",
        "Square blue-to-teal glowing icon showing laboratory glassware and a droplet on a dark gradient background. Use as a compact LabLink brand visual for diagnostic-workflow posts.",
    ),
    "lablink/logo-with-text.png": (
        "LabLink — Logo Wordmark",
        "Landscape LabLink logo with the laboratory-glassware-and-droplet icon at left and the “LabLink” wordmark at right, in a blue-to-teal glow on a dark gradient. Use when the product name should be visible.",
    ),
    "karbarpro/logo_name_with_white_bg.png": (
        "KarbarPro — Logo on White Background",
        "Landscape KarbarPro logo on white. Its K-shaped mark combines a rising chart, shopping cart, and product boxes; the full “KarbarPro” name appears at right. Use for light-background brand presentation.",
    ),
    "karbarpro/logo.png": (
        "KarbarPro — Brand Icon",
        "Square blue-to-teal K-shaped mark combining a rising chart, shopping cart, and product boxes. No product name is shown; use as a compact KarbarPro brand visual.",
    ),
    "karbarpro/logo_with_name.png": (
        "KarbarPro — Logo Wordmark",
        "Landscape KarbarPro logo with the K-shaped chart, cart, and product-box icon at left and the “KarbarPro” wordmark at right, on a dark blue-to-teal gradient. Use when the product name should be visible.",
    ),
    "inarisoftlabs/logo.png": (
        "InariSoftLabs — Brand Icon",
        "Portrait blue-to-teal InariSoftLabs symbol: an upward arrow/person shape over a connected-node path, on a dark gradient background. No text; use as a compact corporate brand visual.",
    ),
    "inarisoftlabs/logo_with_text_on_right_side.png": (
        "InariSoftLabs — Logo Wordmark",
        "Landscape InariSoftLabs logo with the blue-to-teal brand symbol at left and the “InariSoftLabs” wordmark at right, on a dark gradient. Use for company-brand posts where the name should be visible.",
    ),
    "shikha/logo.png": (
        "Shikha — Learning App Icon",
        "Square Shikha app icon on a soft cream background: a gold-to-coral circular line surrounds a simplified book or learning symbol. Use as a compact Shikha product visual; no product name is shown in the image.",
    ),
    "lablink/appointments.png": ("LabLink — Appointments", "LabLink appointments workspace with a booking list, schedule filters, appointment statuses, and a new-appointment action."),
    "lablink/coupon-management.png": ("LabLink — Coupon Management", "LabLink coupon-management workspace with coupon filters, a coupon table, and an add-coupon action."),
    "lablink/daily-expenses.png": ("LabLink — Daily Expenses", "LabLink daily-expenses workspace with summary cards, expense filters, and an expense ledger."),
    "lablink/dashboard.png": ("LabLink — Operations Dashboard", "LabLink diagnostic-centre dashboard with operational summary cards and a business-performance trend chart."),
    "lablink/doctors-list.png": ("LabLink — Referring Doctors", "LabLink referring-doctors directory with a searchable list and doctor-record management controls."),
    "lablink/invoice-management.png": ("LabLink — Invoice Management", "LabLink invoice-management workspace showing diagnostic invoices, payment states, filters, and invoice actions."),
    "lablink/invoice-print.png": ("LabLink — Invoice Print Preview", "LabLink billing screen with a diagnostic-centre invoice print preview and print settings."),
    "lablink/lab-worklist.png": ("LabLink — Lab Worklist", "LabLink lab worklist showing diagnostic orders, test details, workflow stages, and status controls."),
    "lablink/money-in:out-from-lab.png": ("LabLink — Money In / Out", "LabLink cash-flow workspace with money-in and money-out summary cards, filters, and a transaction ledger."),
    "lablink/patients-due-list.png": ("LabLink — Patient Due List", "LabLink patient-due list with outstanding balances, payment-status labels, and collection actions."),
    "lablink/patients-management.png": ("LabLink — Patient Management", "LabLink patient-management workspace with a searchable patient directory and registration controls."),
    "lablink/print-report-directly.png": ("LabLink — Direct Report Print", "LabLink diagnostic-report print preview with report content and print settings."),
    "lablink/referrers.png": ("LabLink — Referrer Management", "LabLink referrer-management workspace with summary cards, a referrer list, balances, and status controls."),
    "lablink/report-management.png": ("LabLink — Report Management", "LabLink report-management workspace showing diagnostic orders grouped by report workflow state."),
    "lablink/report-templates.png": ("LabLink — Report Templates", "LabLink report-template workspace with a template directory, test counts, and template-management controls."),
    "lablink/reporting.png": ("LabLink — Daily Overview", "LabLink daily-overview dashboard with operational totals, activity panels, and trends."),
    "lablink/role-based-access.png": ("LabLink — Role-Based Access", "LabLink role-management workspace showing role records, permissions, and staff-access controls."),
    "lablink/staff-members.png": ("LabLink — Staff Members", "LabLink staff-management workspace with team member records, role labels, contact fields, and status controls."),
    "lablink/test-order.png": ("LabLink — Test Orders", "LabLink diagnostic test-orders workspace with search, status filters, and order workflow controls."),
    "lablink/test-pricing.png": ("LabLink — Test Pricing", "LabLink diagnostic-test pricing workspace showing a test catalogue with rates and update controls."),
    "karbarpro/Dues-list.png": ("KarbarPro — Customer Dues", "KarbarPro customer-dues workspace with outstanding balances, customer records, and collection actions."),
    "karbarpro/Expenses-list.png": ("KarbarPro — Expense Tracking", "KarbarPro expense-tracking workspace with expense summary cards, date filters, categories, and a ledger."),
    "karbarpro/Money-in:out.png": ("KarbarPro — Money In / Out", "KarbarPro cash-flow workspace with money-in and money-out totals, filters, and transaction records."),
    "karbarpro/Sales.png": ("KarbarPro — Sales History", "KarbarPro sales workspace with sales filters, payment-mode controls, and a transaction list."),
    "karbarpro/business-report.png": ("KarbarPro — Business Report", "KarbarPro business-report dashboard with sales and financial summary cards, trend charts, and date filters."),
    "karbarpro/customers.png": ("KarbarPro — Customer Management", "KarbarPro customer-management workspace with customer summaries, due balances, search, and payment actions."),
    "karbarpro/dashboard.png": ("KarbarPro — Business Dashboard", "KarbarPro business dashboard with financial totals, sales-performance trends, and at-a-glance business indicators."),
    "karbarpro/invoices.png": ("KarbarPro — Invoice Management", "KarbarPro invoice workspace with invoice records, dates, totals, due amounts, and payment-status labels."),
    "karbarpro/new sale.png": ("KarbarPro — New Sale", "KarbarPro point-of-sale screen for selecting products, setting quantities and payment details, and completing a sale."),
    "karbarpro/stock-management.png": ("KarbarPro — Stock Management", "KarbarPro products-and-stock workspace with inventory summary cards, product search, stock levels, and management controls."),
}


def main() -> None:
    initialise()
    updated = 0
    with connect() as db:
        for relative_path, (label, description) in ASSET_DETAILS.items():
            asset_id = str(uuid5(NAMESPACE_URL, f"inarisoftlabs-product-asset/{relative_path}"))
            updated += db.execute(
                "UPDATE assets SET label=?, description=? WHERE id=?",
                (label, description, asset_id),
            ).rowcount
    if updated != len(ASSET_DETAILS):
        raise SystemExit(f"Updated {updated}/{len(ASSET_DETAILS)} assets; import the product assets first.")
    print(f"Updated reviewed details for {updated} product assets.")


if __name__ == "__main__":
    main()
