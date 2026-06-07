# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class EquityInvestmentFund(models.Model):
    _name = 'equity.investment.fund'
    _description = 'Equity Investment Fund'
    _inherit = ['mail.thread']
    _order = 'company_id, name'

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name='res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id',
        store=True,
        readonly=True,
    )
    analytic_account_id = fields.Many2one(
        comodel_name='account.analytic.account',
        string="Analytic Account",
        required=True,
        check_company=True,
        tracking=True,
        help="Analytical axis used on all revaluation journal items for this fund.",
    )
    journal_id = fields.Many2one(
        comodel_name='account.journal',
        string="Revaluation Journal",
        required=True,
        domain="[('company_id', '=', company_id), ('type', '=', 'general')]",
        check_company=True,
        tracking=True,
    )
    book_value_account_id = fields.Many2one(
        comodel_name='account.account',
        string="Book Value Account",
        required=True,
        check_company=True,
        tracking=True,
        help="GL account carrying the historical cost / book value of fund holdings.",
    )
    revaluation_account_id = fields.Many2one(
        comodel_name='account.account',
        string="Asset Revaluation Account",
        required=True,
        check_company=True,
        tracking=True,
        help="Balance-sheet account recording accumulated fair-value adjustments.",
    )
    unrealized_gain_loss_account_id = fields.Many2one(
        comodel_name='account.account',
        string="Unrealized Gain/Loss Account",
        required=True,
        check_company=True,
        tracking=True,
        help="P&L or OCI account for unrealized fair-value movements.",
    )
    financial_tag_ids = fields.Many2many(
        comodel_name='account.account.tag',
        string="Financial Reporting Tags",
        help="Configure matching tags on the linked GL accounts for statutory reporting.",
    )
    holder_ids = fields.Many2many(
        comodel_name='res.partner',
        string="Cap Table Holders",
        help="Optional filter: only count shares held by these partners. "
             "Leave empty to include all holders for each share class.",
    )
    portfolio_asset_ids = fields.One2many(
        comodel_name='equity.portfolio.asset',
        inverse_name='investment_fund_id',
        string="Portfolio Assets",
    )

    _code_company_unique = models.Constraint(
        'UNIQUE(code, company_id)',
        _('Fund code must be unique per company.'),
    )

    @api.constrains(
        'book_value_account_id',
        'revaluation_account_id',
        'unrealized_gain_loss_account_id',
    )
    def _check_distinct_accounts(self):
        for fund in self:
            accounts = {
                fund.book_value_account_id.id,
                fund.revaluation_account_id.id,
                fund.unrealized_gain_loss_account_id.id,
            }
            if len(accounts) != 3:
                raise ValidationError(_(
                    "Book value, revaluation, and unrealized gain/loss accounts "
                    "must be three different accounts for fund %(fund)s.",
                    fund=fund.display_name,
                ))

    def _analytic_distribution(self):
        """Return Odoo 19 analytic distribution dict for this fund."""
        self.ensure_one()
        return {str(self.analytic_account_id.id): 100.0}
