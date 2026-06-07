# -*- coding: utf-8 -*-
"""
equity.portfolio.revaluation
============================
Period-end fair-value revaluation of equity holdings.

For each active portfolio position the service:

1. Reads live quantity from the equity cap table.
2. Multiplies by the latest ``equity.portfolio.asset`` market price.
3. Compares fair value to the posted GL book-value balance.
4. Creates a **draft** ``account.move`` debiting/crediting the configured
   revaluation and unrealized gain/loss accounts with the fund analytic axis.
"""

import logging
from collections import defaultdict

from odoo import _, api, Command, fields, models
from odoo.tools.float_utils import float_compare, float_is_zero

_logger = logging.getLogger(__name__)

AMOUNT_PRECISION = 2
QTY_PRECISION = 6


class EquityPortfolioRevaluation(models.Model):
    _name = 'equity.portfolio.revaluation'
    _description = 'Equity Portfolio Period-End Revaluation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(
        required=True,
        copy=False,
        default=lambda self: _('New'),
        readonly=True,
    )
    date = fields.Date(
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('posted', 'Journal Created'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft',
        required=True,
        tracking=True,
    )
    move_ids = fields.Many2many(
        comodel_name='account.move',
        relation='equity_portfolio_revaluation_move_rel',
        column1='revaluation_id',
        column2='move_id',
        string="Journal Entries",
        copy=False,
        readonly=True,
    )
    line_ids = fields.One2many(
        comodel_name='equity.portfolio.revaluation.line',
        inverse_name='revaluation_id',
        string="Position Lines",
        copy=False,
    )
    total_market_value = fields.Monetary(
        compute='_compute_totals',
        store=True,
        currency_field='currency_id',
    )
    total_book_value = fields.Monetary(
        compute='_compute_totals',
        store=True,
        currency_field='currency_id',
    )
    total_adjustment = fields.Monetary(
        compute='_compute_totals',
        store=True,
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id',
        store=True,
        readonly=True,
    )

    @api.depends('line_ids.market_value', 'line_ids.book_value', 'line_ids.adjustment_amount')
    def _compute_totals(self):
        for batch in self:
            batch.total_market_value = sum(batch.line_ids.mapped('market_value'))
            batch.total_book_value = sum(batch.line_ids.mapped('book_value'))
            batch.total_adjustment = sum(batch.line_ids.mapped('adjustment_amount'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'equity.portfolio.revaluation'
                ) or _('New')
        return super().create(vals_list)

    @api.model
    def _cron_post_period_end_revaluations(self):
        """Scheduled action: generate draft revaluation entries for every company."""
        revaluation_date = fields.Date.context_today(self)
        stats = {'batches': 0, 'moves': 0, 'lines': 0, 'skipped': 0}

        for company in self.env['res.company'].search([]):
            try:
                batch_stats = self.with_company(company)._generate_period_end_revaluations(
                    revaluation_date,
                )
                for key in stats:
                    stats[key] += batch_stats.get(key, 0)
            except Exception:
                _logger.exception(
                    "Period-end equity revaluation failed for company %s.",
                    company.display_name,
                )

        _logger.info(
            "Period-end equity revaluation cron finished: "
            "%(batches)s batch(es), %(moves)s draft move(s), "
            "%(lines)s line(s), %(skipped)s position(s) skipped.",
            stats,
        )
        return stats

    @api.model
    def _generate_period_end_revaluations(self, revaluation_date):
        """Build revaluation lines and draft journal entries for the current company."""
        PortfolioAsset = self.env['equity.portfolio.asset']
        assets = PortfolioAsset.search([
            ('company_id', '=', self.env.company.id),
            ('active', '=', True),
            ('investment_fund_id', '!=', False),
            ('market_price', '>', 0),
        ])

        position_payloads = []
        skipped = 0
        for asset in assets:
            payload = asset._prepare_revaluation_payload(revaluation_date)
            if not payload:
                skipped += 1
                continue
            if float_is_zero(payload['adjustment_amount'], precision_digits=AMOUNT_PRECISION):
                skipped += 1
                continue
            position_payloads.append(payload)

        if not position_payloads:
            return {'batches': 0, 'moves': 0, 'lines': 0, 'skipped': skipped}

        batch = self.create({
            'date': revaluation_date,
            'company_id': self.env.company.id,
        })
        self.env['equity.portfolio.revaluation.line'].create([
            {'revaluation_id': batch.id, **payload}
            for payload in position_payloads
        ])

        moves = batch._create_draft_revaluation_moves()
        batch.write({
            'state': 'posted',
            'move_ids': [Command.set(moves.ids)],
        })

        return {
            'batches': 1,
            'moves': len(moves),
            'lines': len(position_payloads),
            'skipped': skipped,
        }

    def _create_draft_revaluation_moves(self):
        """Create one draft account.move per investment fund."""
        self.ensure_one()
        Move = self.env['account.move']

        lines_by_fund = defaultdict(list)
        for line in self.line_ids:
            lines_by_fund[line.investment_fund_id].append(line)

        moves = Move
        for fund, fund_lines in lines_by_fund.items():
            move_lines = []
            for line in fund_lines:
                move_lines.extend(line._prepare_move_line_commands(fund))

            if not move_lines:
                continue

            move = Move.create({
                'move_type': 'entry',
                'journal_id': fund.journal_id.id,
                'date': self.date,
                'ref': _('Equity revaluation %(fund)s — %(batch)s', fund=fund.code, batch=self.name),
                'company_id': self.company_id.id,
                'line_ids': move_lines,
            })
            moves |= move
            move.message_post(body=_(
                "Draft fair-value revaluation generated from %(batch)s on %(date)s.",
                batch=self.name,
                date=self.date,
            ))

        return moves

    def action_open_journal_entries(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Revaluation Journal Entries'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.move_ids.ids)],
        }


class EquityPortfolioRevaluationLine(models.Model):
    _name = 'equity.portfolio.revaluation.line'
    _description = 'Equity Portfolio Revaluation Line'
    _order = 'investment_fund_id, share_class_id, id'

    revaluation_id = fields.Many2one(
        comodel_name='equity.portfolio.revaluation',
        required=True,
        ondelete='cascade',
        index=True,
    )
    portfolio_asset_id = fields.Many2one(
        comodel_name='equity.portfolio.asset',
        required=True,
        ondelete='restrict',
    )
    investment_fund_id = fields.Many2one(
        comodel_name='equity.investment.fund',
        required=True,
        index=True,
    )
    share_class_id = fields.Many2one(
        comodel_name='equity.security.class',
        required=True,
    )
    quantity = fields.Float(digits=(16, QTY_PRECISION))
    market_price = fields.Float(digits='Product Price')
    market_value = fields.Monetary(currency_field='currency_id')
    book_value = fields.Monetary(currency_field='currency_id')
    adjustment_amount = fields.Monetary(
        string="Fair Value Adjustment",
        currency_field='currency_id',
        help="Market value minus book value. Positive = unrealized gain.",
    )
    currency_id = fields.Many2one(
        related='revaluation_id.currency_id',
        store=True,
        readonly=True,
    )

    def _prepare_move_line_commands(self, fund):
        """
        Build balanced debit/credit commands for this line's adjustment.

        Gain (adjustment > 0): Dr Revaluation / Cr Unrealized G/L
        Loss (adjustment < 0): Dr Unrealized G/L / Cr Revaluation
        """
        self.ensure_one()
        adjustment = self.adjustment_amount
        if float_is_zero(adjustment, precision_digits=AMOUNT_PRECISION):
            return []

        amount = abs(adjustment)
        analytic_distribution = fund._analytic_distribution()
        label = _(
            "%(fund)s / %(share_class)s fair-value adjustment",
            fund=fund.code,
            share_class=self.share_class_id.display_name,
        )

        if float_compare(adjustment, 0.0, precision_digits=AMOUNT_PRECISION) > 0:
            debit_account = fund.revaluation_account_id
            credit_account = fund.unrealized_gain_loss_account_id
        else:
            debit_account = fund.unrealized_gain_loss_account_id
            credit_account = fund.revaluation_account_id

        return [
            Command.create({
                'name': label,
                'account_id': debit_account.id,
                'debit': amount,
                'credit': 0.0,
                'analytic_distribution': analytic_distribution,
            }),
            Command.create({
                'name': label,
                'account_id': credit_account.id,
                'debit': 0.0,
                'credit': amount,
                'analytic_distribution': analytic_distribution,
            }),
        ]
