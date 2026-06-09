# -*- coding: utf-8 -*-

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    saudi_cjsc_min_shareholders = fields.Integer(
        related='company_id.saudi_cjsc_min_shareholders',
        readonly=False,
    )
    equity_rofr_window_days = fields.Integer(
        related='company_id.equity_rofr_window_days',
        readonly=False,
    )
    equity_max_ownership_pct = fields.Float(
        related='company_id.equity_max_ownership_pct',
        readonly=False,
    )
    equity_max_voting_power_pct = fields.Float(
        related='company_id.equity_max_voting_power_pct',
        readonly=False,
    )
    equity_max_votes_per_share = fields.Integer(
        related='company_id.equity_max_votes_per_share',
        readonly=False,
    )
