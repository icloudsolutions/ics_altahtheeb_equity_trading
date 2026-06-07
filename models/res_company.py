# -*- coding: utf-8 -*-

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    saudi_cjsc_min_shareholders = fields.Integer(
        string="Saudi CJSC Minimum Shareholders",
        default=2,
        help="Statutory minimum number of shareholders for a Saudi closed joint stock company "
             "(شركة مساهمة مقفلة) under MOCI bylaws.",
    )
    equity_rofr_window_days = fields.Integer(
        string="ROFR Window (Days)",
        default=15,
        help="Mandatory right-of-first-refusal notice period for existing shareholders "
             "before an external transfer may proceed.",
    )
    equity_max_ownership_pct = fields.Float(
        string="Max Ownership Concentration (%)",
        default=49.0,
        help="Maximum percentage of issued share capital any single shareholder may hold "
             "under internal governance policies.",
    )
    equity_max_voting_power_pct = fields.Float(
        string="Max Voting Power Concentration (%)",
        default=49.0,
        help="Maximum percentage of aggregate voting rights any single shareholder may control.",
    )
    equity_max_votes_per_share = fields.Integer(
        string="Max Votes per Share (Bylaws)",
        default=1,
        help="Highest permitted votes-per-share weight for any equity class under company bylaws.",
    )
