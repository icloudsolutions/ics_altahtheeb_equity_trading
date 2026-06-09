# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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

    @api.constrains(
        'saudi_cjsc_min_shareholders',
        'equity_rofr_window_days',
        'equity_max_ownership_pct',
        'equity_max_voting_power_pct',
        'equity_max_votes_per_share',
    )
    def _check_equity_governance_values(self):
        for company in self:
            if company.saudi_cjsc_min_shareholders < 2:
                raise ValidationError(_(
                    "Saudi CJSC minimum shareholders must be at least 2."
                ))
            if company.equity_rofr_window_days <= 0:
                raise ValidationError(_(
                    "The ROFR window must be a strictly positive number of days."
                ))
            if not 0 < company.equity_max_ownership_pct <= 100:
                raise ValidationError(_(
                    "Maximum ownership concentration must be between 0 and 100."
                ))
            if not 0 < company.equity_max_voting_power_pct <= 100:
                raise ValidationError(_(
                    "Maximum voting power concentration must be between 0 and 100."
                ))
            if company.equity_max_votes_per_share <= 0:
                raise ValidationError(_(
                    "Maximum votes per share must be strictly positive."
                ))
