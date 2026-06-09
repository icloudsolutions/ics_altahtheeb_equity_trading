# -*- coding: utf-8 -*-
"""
Extend native ``equity.security.class`` with GAZT/ZATCA Zakat classification.

Tadawul-listed holdings are categorized as either:

* **Trading (short-term)** — fair market value is included in the Zakat base.
* **Strategic / long-term** — deductible from the Zakat base when investee
  zakat conditions are met (handled separately on the zakat return).
"""

from odoo import fields, models

ZAKAT_CLASSIFICATION_SELECTION = [
    ('short_term_trading', 'Trading (Short-term)'),
    ('long_term_strategic', 'Strategic / Long-term'),
]


class EquitySecurityClass(models.Model):
    _inherit = 'equity.security.class'

    zakat_classification = fields.Selection(
        selection=ZAKAT_CLASSIFICATION_SELECTION,
        string="Zakat Classification",
        default='short_term_trading',
        required=True,
        help=(
            "GAZT/ZATCA treatment for listed share holdings:\n"
            "• Trading — included in the Zakat base at year-end fair market value.\n"
            "• Strategic / Long-term — not added to the Zakat base; may be "
            "deducted when the investee company meets zakat regulatory conditions."
        ),
    )
