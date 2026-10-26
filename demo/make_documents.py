"""Generate the fictional demo documents as PDFs with headless Chrome.

The PDFs are committed; rerun this script only when the fictional content changes:
    python -m demo.make_documents
"""

from __future__ import annotations

import argparse
import html
import subprocess
import tempfile
from pathlib import Path

from demo.io import DEMO_DIR

CHROME_CANDIDATES = ("google-chrome", "chromium", "chromium-browser")

DOCUMENTS: dict[str, dict[str, dict]] = {
    "ru": {
        "kp1.pdf": {
            "title": "Коммерческое предложение № 14/10-2026",
            "subtitle": "ООО «Вектор-Тех»",
            "meta": [
                ("Получатель", "МБОУ «Школа № 7»"),
                ("Дата", "12 октября 2026 г."),
                ("Срок действия предложения", "30 календарных дней"),
                ("Контакт", "отдел продаж, доб. 214"),
            ],
            "sections": [
                (
                    "Предмет предложения",
                    [
                        "ООО «Вектор-Тех» предлагает поставку двадцати ноутбуков для оснащения "
                        "компьютерного класса, включая док-станции, доставку и настройку оборудования.",
                    ],
                ),
            ],
            "table": {
                "head": ["Наименование", "Кол-во", "Цена, руб.", "Сумма, руб."],
                "rows": [
                    ["Ноутбук 14″, 16 ГБ, 512 ГБ", "20", "54 900", "1 098 000"],
                    ["Док-станция USB-C", "20", "8 400", "168 000"],
                    ["Доставка и настройка", "1", "12 000", "12 000"],
                ],
                "total": ["Итого", "", "", "1 278 000"],
            },
            "terms": [
                "Срок поставки — 14 рабочих дней с даты подписания договора.",
                "Гарантия — 24 месяца на всё оборудование.",
                "Оплата — 50 % аванс, 50 % после приёмки.",
                "Неустойка за просрочку — 0,1 % от суммы договора за каждый день, не более 10 %.",
                "Доставка до кабинета и первоначальная настройка входят в стоимость.",
            ],
        },
        "kp2.pdf": {
            "title": "Коммерческое предложение № 27-кс",
            "subtitle": "ООО «Смарт-Снаб»",
            "meta": [
                ("Получатель", "МБОУ «Школа № 7»"),
                ("Дата", "13 октября 2026 г."),
                ("Срок действия предложения", "45 календарных дней"),
                ("Контакт", "менеджер по работе с образовательными учреждениями"),
            ],
            "sections": [
                (
                    "Предмет предложения",
                    [
                        "Поставка двадцати ноутбуков и док-станций для компьютерного класса. "
                        "Оборудование поставляется партией, настройка выполняется на месте.",
                    ],
                ),
            ],
            "table": {
                "head": ["Наименование", "Кол-во", "Цена, руб.", "Сумма, руб."],
                "rows": [
                    ["Ноутбук 14″, 16 ГБ, 512 ГБ", "20", "52 400", "1 048 000"],
                    ["Док-станция USB-C", "20", "9 100", "182 000"],
                    ["Доставка", "1", "18 500", "18 500"],
                ],
                "total": ["Итого", "", "", "1 248 500"],
            },
            "terms": [
                "Срок поставки — 21 рабочий день с даты подписания договора.",
                "Гарантия — 36 месяцев, сервис на территории заказчика.",
                "Оплата — 100 % по факту поставки и приёмки.",
                "Неустойка за просрочку — 0,05 % от суммы договора за каждый день, не более 5 %.",
                "Подъём оборудования на этаж в стоимость не входит.",
            ],
        },
        "procurement.pdf": {
            "title": "Положение о закупках",
            "subtitle": "МБОУ «Школа № 7»",
            "meta": [
                ("Редакция", "третья"),
                ("Утверждено", "30 августа 2026 г."),
                ("Действует до", "31 августа 2029 г."),
            ],
            "sections": [
                (
                    "1. Общие положения",
                    [
                        "Положение определяет порядок закупки товаров, работ и услуг для нужд школы "
                        "и применяется ко всем закупкам, финансируемым из бюджетных средств.",
                    ],
                ),
                (
                    "2. Закупки до 600 000 рублей",
                    [
                        "Закупка до 600 000 рублей может проводиться у единственного поставщика. "
                        "Решение оформляется служебной запиской с обоснованием цены.",
                    ],
                ),
                (
                    "3. Закупки от 600 000 рублей",
                    [
                        "Требуется не менее двух коммерческих предложений. Предложения рассматриваются "
                        "по трём критериям: цена — 60 %, гарантийный срок — 25 %, срок поставки — 15 %.",
                    ],
                ),
                (
                    "4. Комиссия",
                    [
                        "Решение принимает комиссия из трёх человек простым большинством голосов. "
                        "Протокол оформляется в течение трёх рабочих дней.",
                    ],
                ),
                (
                    "5. Договор",
                    [
                        "Договор с победителем подписывается в течение десяти рабочих дней после протокола. "
                        "Существенные условия должны совпадать с предложением победителя.",
                    ],
                ),
            ],
            "table": None,
            "terms": [],
        },
    },
    "en": {
        "quote-a.pdf": {
            "title": "Quotation No. NF-1420",
            "subtitle": "Northfield IT Supplies Ltd",
            "meta": [
                ("Customer", "Bracken Hill School"),
                ("Date", "12 October 2026"),
                ("Valid for", "30 days"),
                ("Contact", "education team, ext. 214"),
            ],
            "sections": [
                (
                    "Scope",
                    [
                        "Supply of twenty laptops for the computing classroom, including docking "
                        "stations, delivery and on-site setup.",
                    ],
                ),
            ],
            "table": {
                "head": ["Item", "Qty", "Unit price, GBP", "Amount, GBP"],
                "rows": [
                    ["14″ laptop, 16 GB RAM, 512 GB SSD", "20", "899", "17,980"],
                    ["USB-C docking station", "20", "95", "1,900"],
                    ["Delivery and setup", "1", "250", "250"],
                ],
                "total": ["Total", "", "", "20,130"],
            },
            "terms": [
                "Delivery within 14 working days of order.",
                "Warranty: 24 months, collect and return.",
                "Payment: 50 % on order, 50 % on delivery.",
                "Late delivery penalty: 0.1 % of the order value per day, capped at 10 %.",
                "Delivery to the classroom and initial setup are included.",
            ],
        },
        "quote-b.pdf": {
            "title": "Quotation No. BL-8831",
            "subtitle": "Brightline Office Systems Ltd",
            "meta": [
                ("Customer", "Bracken Hill School"),
                ("Date", "13 October 2026"),
                ("Valid for", "45 days"),
                ("Contact", "public sector account manager"),
            ],
            "sections": [
                (
                    "Scope",
                    [
                        "Supply of twenty laptops and docking stations for the computing classroom, "
                        "delivered as a single batch with on-site configuration.",
                    ],
                ),
            ],
            "table": {
                "head": ["Item", "Qty", "Unit price, GBP", "Amount, GBP"],
                "rows": [
                    ["14″ laptop, 16 GB RAM, 512 GB SSD", "20", "845", "16,900"],
                    ["USB-C docking station", "20", "110", "2,200"],
                    ["Delivery", "1", "420", "420"],
                ],
                "total": ["Total", "", "", "19,520"],
            },
            "terms": [
                "Delivery within 21 working days of order.",
                "Warranty: 36 months, on-site service.",
                "Payment: 100 % on delivery and acceptance.",
                "Late delivery penalty: 0.05 % of the order value per day, capped at 5 %.",
                "Delivery to upper floors is not included.",
            ],
        },
        "policy.pdf": {
            "title": "Procurement Policy",
            "subtitle": "Bracken Hill School",
            "meta": [
                ("Revision", "third"),
                ("Approved", "30 August 2026"),
                ("Valid until", "31 August 2029"),
            ],
            "sections": [
                (
                    "1. General",
                    [
                        "This policy sets out how the school buys goods and services and applies to "
                        "every purchase made from public funds.",
                    ],
                ),
                (
                    "2. Purchases below £10,000",
                    [
                        "Purchases below £10,000 may be made from a single supplier. The decision is "
                        "recorded in a written note explaining the price.",
                    ],
                ),
                (
                    "3. Purchases of £10,000 and above",
                    [
                        "At least two written quotations are required. Quotations are assessed on three "
                        "criteria: price 60 %, warranty 25 %, delivery time 15 %.",
                    ],
                ),
                (
                    "4. Panel",
                    [
                        "A panel of three decides by simple majority. The minutes are issued within "
                        "three working days.",
                    ],
                ),
                (
                    "5. Contract",
                    [
                        "The contract is signed within ten working days of the minutes. Its terms must "
                        "match the winning quotation.",
                    ],
                ),
            ],
            "table": None,
            "terms": [],
        },
    },
}

PAGE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 22mm 18mm; }}
  body {{ font: 12pt/1.55 Georgia, "Times New Roman", serif; color: #1c1c1c; }}
  h1 {{ font-size: 19pt; margin: 0 0 4px; }}
  .brand {{ color: #4a5a4f; letter-spacing: .08em; text-transform: uppercase; font-size: 10pt; }}
  .meta {{ margin: 18px 0 22px; border-top: 2px solid #4a5a4f; border-bottom: 1px solid #c9cfc9; }}
  .meta div {{ display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px dotted #dfe4df; }}
  .meta div:last-child {{ border-bottom: 0; }}
  .meta span:first-child {{ color: #5b6b60; }}
  h2 {{ font-size: 13pt; margin: 22px 0 8px; }}
  p {{ margin: 0 0 10px; text-align: justify; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0 16px; font-size: 11pt; }}
  th, td {{ border-bottom: 1px solid #d7ddd7; padding: 7px 6px; text-align: left; }}
  th {{ background: #eef1ee; font-weight: 600; }}
  td.num, th.num {{ text-align: right; white-space: nowrap; }}
  tr.total td {{ font-weight: 700; border-top: 2px solid #4a5a4f; }}
  ul {{ margin: 0 0 12px 18px; padding: 0; }}
  li {{ margin-bottom: 5px; }}
  footer {{ margin-top: 26px; font-size: 9.5pt; color: #6b776e; border-top: 1px solid #dfe4df; padding-top: 8px; }}
</style>
</head>
<body>
<div class="brand">{subtitle}</div>
<h1>{title}</h1>
<div class="meta">{meta}</div>
{sections}
{table}
{terms}
<footer>{footer}</footer>
</body>
</html>
"""


def _render_html(lang: str, document: dict) -> str:
    meta = "".join(f"<div><span>{html.escape(str(k))}</span><span>{html.escape(str(v))}</span></div>" for k, v in document["meta"])
    sections = "".join(
        f"<h2>{html.escape(heading)}</h2>" + "".join(f"<p>{html.escape(text)}</p>" for text in paragraphs)
        for heading, paragraphs in document["sections"]
    )
    table = ""
    data = document.get("table")
    if data:
        head = "".join(f'<th class="num">{html.escape(cell)}</th>' if index else f"<th>{html.escape(cell)}</th>" for index, cell in enumerate(data["head"]))
        rows = []
        for row in data["rows"]:
            cells = "".join(f'<td class="num">{html.escape(cell)}</td>' if index else f"<td>{html.escape(cell)}</td>" for index, cell in enumerate(row))
            rows.append(f"<tr>{cells}</tr>")
        total = data.get("total")
        if total:
            cells = "".join(f'<td class="num">{html.escape(cell)}</td>' if index else f"<td>{html.escape(cell)}</td>" for index, cell in enumerate(total))
            rows.append(f'<tr class="total">{cells}</tr>')
        table = f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    terms = ""
    if document["terms"]:
        items = "".join(f"<li>{html.escape(item)}</li>" for item in document["terms"])
        terms = f"<h2>{'Условия поставки' if lang == 'ru' else 'Terms'}</h2><ul>{items}</ul>"
    footer_note = (
        "Документ подготовлен для демонстрации интерфейса. Все наименования, суммы и условия вымышлены."
        if lang == "ru"
        else "Prepared for an interface demonstration. Every name, amount and term is fictional."
    )
    return PAGE.format(
        lang=lang,
        subtitle=html.escape(document["subtitle"]),
        title=html.escape(document["title"]),
        meta=meta,
        sections=sections,
        table=table,
        terms=terms,
        footer=html.escape(footer_note),
    )


def _chrome() -> str:
    for name in CHROME_CANDIDATES:
        found = subprocess.run(["which", name], capture_output=True, text=True).stdout.strip()
        if found:
            return found
    raise RuntimeError("Headless Chrome is required to build the demo documents")


def render_all(out_dir: Path = DEMO_DIR / "pdfs") -> list[Path]:
    chrome = _chrome()
    written: list[Path] = []
    with tempfile.TemporaryDirectory() as raw:
        temp = Path(raw)
        for lang, documents in DOCUMENTS.items():
            (out_dir / lang).mkdir(parents=True, exist_ok=True)
            for name, document in documents.items():
                source = temp / f"{lang}-{name}.html"
                source.write_text(_render_html(lang, document), encoding="utf-8")
                target = out_dir / lang / name
                subprocess.run(
                    [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                     "--no-pdf-header-footer", f"--print-to-pdf={target}", source.as_uri()],
                    check=True, capture_output=True,
                )
                written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m demo.make_documents")
    parser.add_argument("--out", default=str(DEMO_DIR / "pdfs"))
    args = parser.parse_args(argv)
    for path in render_all(Path(args.out)):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
