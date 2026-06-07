# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare

SHARES_PRECISION = 6


class EquityLotAllocation(models.Model):
    _name = 'equity.lot.allocation'
    _description = 'Equity Lot Allocation (FIFO)'
    _order = 'sell_transaction_id, buy_transaction_date, id'

    sell_transaction_id = fields.Many2one(
        comodel_name='equity.transaction',
        string="Sell Transaction",
        required=True,
        ondelete='cascade',
        index=True,
    )
    buy_transaction_id = fields.Many2one(
        comodel_name='equity.transaction',
        string="Buy Lot",
        required=True,
        ondelete='restrict',
        index=True,
    )
    buy_transaction_date = fields.Date(
        related='buy_transaction_id.date',
        store=True,
        readonly=True,
    )
    holder_id = fields.Many2one(
        comodel_name='res.partner',
        string="Holder",
        required=True,
        index=True,
    )
    company_partner_id = fields.Many2one(
        comodel_name='res.partner',
        string="Equity Company",
        required=True,
        index=True,
    )
    security_class_id = fields.Many2one(
        comodel_name='equity.security.class',
        string="Share Class",
        required=True,
        index=True,
    )
    qty = fields.Float(
        string="Allocated Quantity",
        required=True,
        digits=(16, SHARES_PRECISION),
    )
    buy_unit_price = fields.Float(
        string="Buy Unit Price",
        required=True,
        digits='Product Price',
    )
    sell_unit_price = fields.Float(
        string="Sell Unit Price",
        required=True,
        digits='Product Price',
    )
    cost_basis = fields.Monetary(
        string="Cost Basis",
        compute='_compute_amounts',
        store=True,
        currency_field='currency_id',
    )
    proceeds = fields.Monetary(
        string="Proceeds",
        compute='_compute_amounts',
        store=True,
        currency_field='currency_id',
    )
    realized_gain_loss = fields.Monetary(
        string="Realized Gain/Loss",
        compute='_compute_amounts',
        store=True,
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        string="Currency",
        related='sell_transaction_id.equity_currency_id',
        store=True,
        readonly=True,
    )

    @api.depends('qty', 'buy_unit_price', 'sell_unit_price')
    def _compute_amounts(self):
        for allocation in self:
            allocation.cost_basis = allocation.qty * allocation.buy_unit_price
            allocation.proceeds = allocation.qty * allocation.sell_unit_price
            allocation.realized_gain_loss = allocation.proceeds - allocation.cost_basis

    @api.constrains('qty')
    def _check_qty_positive(self):
        for allocation in self:
            if float_compare(allocation.qty, 0.0, precision_digits=SHARES_PRECISION) <= 0:
                raise ValidationError(_("Allocated quantity must be strictly positive."))
