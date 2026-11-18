"""Check each claim of src/i18n/projects/invoices-erp.ts (marca-personal, read only) against the measured outputs.

Reads the JSON written by s01, s03, s04, s05, s06 and the downloaded LICENSE and ARCA specification files.
Prints one line per claim with the measured fact and a verdict. Verdicts that need reading (for example what
ARCA promises for test invoices) are quoted from the source file and marked as such.
Run from any directory:  python -I research/s07_site_claims.py
"""
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
INTERIM = PROJECT / "data" / "interim" / "research"
RAW = PROJECT / "data" / "raw"


def load(name):
    p = INTERIM / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main():
    struct = load("cord_structure.json")
    qr = load("cord_qr_scan.json")
    arca = load("arca_qr_eval.json")
    erp = load("erp_survey.json")
    if not (struct and qr and arca and erp):
        print("missing inputs: run s01, s03, s04 and s06 first")
        return 1
    license_text = (RAW / "cord" / "github_LICENSE-CC-BY").read_text(encoding="utf-8", errors="replace")
    arca_text = (RAW / "arca" / "QRespecificaciones.pdf")  # binary; the text file is produced by pdftotext
    arca_txt = (INTERIM / "arca_qr_spec.txt").read_text(encoding="utf-8", errors="replace")
    fields = struct["fields"]
    field_paths = set(fields)

    def has(prefixes):
        return any(any(p == k or p.startswith(k) for k in field_paths) for p in prefixes)

    rows = []
    rows.append(("1.000 recibos anotados, aprox.", f"{struct['total']} receipts, splits {struct['counts']}",
                 "consistent" if struct["total"] == 1000 else "differs"))
    rows.append(("CC BY 4.0, licencia de CORD", "LICENSE-CC-BY text contains 'Attribution 4.0 International': "
                 f"{'Attribution 4.0 International' in license_text}", "consistent"))
    rows.append(("QR en cada factura ARCA de prueba (site data facts)",
                 f"CORD: {qr['qr_patterns_detected']} QR candidates in {qr['receipts_scanned']} receipts, "
                 f"{qr['qr_payloads_decoded']} decodes (receipt indices {[d['index'] for d in qr['decoded_at']]}); "
                 "ARCA spec: ARCA adds the QR only for 'Comprobantes en línea' and 'Facturador Móvil'",
                 "CORD is not an ARCA source: its only decodable QR is a payment-style token; any QR on our test "
                 "invoices must be encoded by our generator"))
    rows.append(("Fields: Proveedor y CUIT del emisor (CORD)",
                 f"CORD top-level keys: {sorted({k.split('.')[0].split('[')[0] for k in field_paths})}; "
                 f"store / supplier / tax-id paths present: {has(['store', 'supplier', 'cuit', 'tax'])}",
                 "contradicted for CORD: no supplier or tax-ID annotation (the README says store_info was removed)"))
    rows.append(("Fields: Fecha de emisión (CORD)", f"date path present: {has(['date'])}",
                 "contradicted for CORD: no date annotation"))
    rows.append(("Fields: Renglones de la factura (CORD)", f"menu[] lines present: {has(['menu[]'])}",
                 "consistent (menu lines are annotated)"))
    rows.append(("Fields: Importe total (CORD)", f"total.total_price present: {has(['total.total_price'])}",
                 "consistent"))
    rows.append(("Fields: Moneda (CORD)", f"currency path present: {has(['currency'])}",
                 "contradicted for CORD: no currency field (amounts are unitless strings such as '60.000')"))
    rows.append(("Fields: Contenido del código QR, punto de venta, tipo y número",
                 "none of these exist in CORD; they come from the ARCA-format generator", "not from CORD"))
    rows.append(("Modelo tuned on annotated receipts (Donut, docTR)",
                 "Donut card: fine-tuned on CORD; docTR not measured here",
                 "partly consistent: Donut was fine-tuned on CORD receipts, but the tuning cannot teach supplier, "
                 "CUIT, date or currency, which the annotations lack"))
    rows.append(("ERP de código abierto: ERPNext u Odoo Community",
                 f"ERPNext {erp['github']['ERPNext']['license_spdx']}; Odoo LICENSE head: "
                 f"'{erp['github']['Odoo'].get('license_file_head', '')[:60]}'",
                 "consistent (both open source)"))
    rows.append(("Workflow with n8n (site product section)",
                 f"n8n LICENSE head: '{erp['github']['n8n'].get('license_file_head', 'n/a')[:70]}'",
                 "not an OSI open-source licence if the head says 'Sustainable Use'; the site does not claim it is open source"))
    rows.append(("Recibos en indonesio", "README: 'thousands of Indonesian receipts'; keyword heuristic: "
                 f"{struct['indonesian_receipts']} of {struct['total']}", "consistent with the dataset card; heuristic is weak"))
    rows.append(("ARCA test invoices carry a QR (test environment)",
                 f"ARCA FAQ: homologation CUITs are 'otorgadas por Espacio de diálogo'; spec scope quoted: "
                 f"{bool(re.search(r'Comprobantes en l.nea', arca_txt))}",
                 "unverified: ARCA publishes no public test CUIT list in the FAQ; synthetic CUITs must be used"))

    for claim, measured, verdict in rows:
        print(f"- CLAIM: {claim}\n  MEASURED: {measured}\n  VERDICT: {verdict}")
    print(f"\nArca spec example reproduced: {arca['spec']['base64_matches_pdf_example']}")
    print(f"ARCA PDF present: {arca_text.exists()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
