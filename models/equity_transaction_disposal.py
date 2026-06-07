# -*- coding: utf-8 -*-
"""
equity.transaction.disposal
===========================
FIFO disposal service and wizard for realized capital gain/loss on share sales.

When a sell transfer is finalized, this layer:

1. Loads remaining buy lots for the disposing holder (oldest first).
2. Sequentially allocates the sold quantity against those lots.
3. Persists buy/sell pairings on ``equity.lot.allocation``.
4. Returns aggregate cost basis, proceeds, and realized gain/loss.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero

from .equity_lot_allocation import SHARES_PRECISION

_logger = logging.getLogger(__name__)

LEDGER_ELIGIBLE_STATES = ('signed_legal', 'done')


class EquityTransactionDisposal(models.TransientModel):
    _name = 'equity.transaction.disposal'
    _description = 'Equity Transaction FIFO Disposal'

    sell_transaction_id = fields.Many2one(
        comodel_name='equity.transaction',
        string="Sell Transaction",
        required=True,
        readonly=True,
    )
    holder_id = fields.Many2one(
        comodel_name='res.partner',
        related='sell_transaction_id.seller_id',
        readonly=True,
    )
    security_class_id = fields.Many2one(
        comodel_name='equity.security.class',
        related='sell_transaction_id.security_class_id',
        readonly=True,
    )
    sell_qty = fields.Float(
        related='sell_transaction_id.securities',
        readonly=True,
    )
    sell_unit_price = fields.Float(
        related='sell_transaction_id.security_price',
        readonly=True,
    )
    cost_basis_total = fields.Monetary(
        string="Total Cost Basis",
        readonly=True,
        currency_field='currency_id',
    )
    proceeds_total = fields.Monetary(
        string="Total Proceeds",
        readonly=True,
        currency_field='currency_id',
    )
    realized_gain_loss = fields.Monetary(
        string="Realized Gain/Loss",
        readonly=True,
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        related='sell_transaction_id.equity_currency_id',
        readonly=True,
    )
    allocation_ids = fields.Many2many(
        comodel_name='equity.lot.allocation',
        string="Lot Allocations",
        readonly=True,
    )

    @api.model
    def process_sell_transaction(self, sell_transaction, force=False):
        """
        FIFO-process a sell equity.transaction and persist lot allocations.

        :returns: dict with totals and created equity.lot.allocation records.
        """
        sell_transaction.ensure_one()
        self._validate_sell_transaction(sell_transaction)

        LotAllocation = self.env['equity.lot.allocation']
        existing = LotAllocation.search([('sell_transaction_id', '=', sell_transaction.id)])
        if existing and not force:
            return self._build_result_from_allocations(existing)

        if existing and force:
            existing.unlink()

        fifo_lines = self._compute_fifo_lines(sell_transaction)
        allocations = LotAllocation.create(fifo_lines)
        result = self._build_result_from_allocations(allocations)

        sell_transaction.with_context(**{
            'equity_trade_system_write': True,
        }).write({
            'disposal_processed': True,
            'realized_gain_loss': result['realized_gain_loss'],
            'disposal_cost_basis': result['cost_basis_total'],
            'disposal_proceeds': result['proceeds_total'],
        })

        _logger.info(
            "FIFO disposal processed for equity.transaction %s: gain/loss=%s, allocations=%s",
            sell_transaction.id,
            result['realized_gain_loss'],
            allocations.ids,
        )
        return result

    @api.model
    def preview_sell_transaction(self, sell_transaction):
        """Return FIFO allocation lines without persisting (wizard preview)."""
        sell_transaction.ensure_one()
        self._validate_sell_transaction(sell_transaction)
        lines = self._compute_fifo_lines(sell_transaction)
        totals = self._summarize_fifo_lines(lines)
        return {**totals, 'lines': lines}

    def action_process_disposal(self):
        """Wizard button: persist FIFO allocations for the selected sell."""
        self.ensure_one()
        result = self.process_sell_transaction(self.sell_transaction_id)
        self.write({
            'cost_basis_total': result['cost_basis_total'],
            'proceeds_total': result['proceeds_total'],
            'realized_gain_loss': result['realized_gain_loss'],
            'allocation_ids': [(6, 0, result['allocation_ids'])],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('FIFO Disposal Result'),
            'res_model': 'equity.transaction.disposal',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    @api.model
    def _validate_sell_transaction(self, sell_transaction):
        if sell_transaction.transaction_type != 'transfer':
            raise UserError(_("FIFO disposal only applies to share transfer (sell) transactions."))
        if not sell_transaction.seller_id:
            raise UserError(_("A seller is required to process a FIFO disposal."))
        if sell_transaction.securities_type != 'shares':
            raise UserError(_("FIFO disposal only applies to share transactions."))
        if float_compare(sell_transaction.securities, 0.0, precision_digits=SHARES_PRECISION) <= 0:
            raise ValidationError(_("Sell quantity must be strictly positive."))
        if hasattr(sell_transaction, 'state') and sell_transaction.state not in LEDGER_ELIGIBLE_STATES:
            raise UserError(_(
                "The sell transaction must be in a finalized ledger state "
                "(%(states)s) before FIFO disposal.",
                states=', '.join(LEDGER_ELIGIBLE_STATES),
            ))

    @api.model
    def _compute_fifo_lines(self, sell_transaction):
        """Build allocation dicts without writing to the database."""
        holder = sell_transaction.seller_id
        company_partner = sell_transaction.partner_id
        security_class = sell_transaction.security_class_id
        sell_qty = sell_transaction.securities
        sell_price = sell_transaction.security_price

        buy_lots = self._get_remaining_buy_lots(
            holder=holder,
            company_partner=company_partner,
            security_class=security_class,
            before_date=sell_transaction.date,
            exclude_transaction_id=sell_transaction.id,
        )

        remaining = sell_qty
        lines = []
        for lot in buy_lots:
            if float_is_zero(remaining, precision_digits=SHARES_PRECISION):
                break

            available = lot['remaining_qty']
            if float_compare(available, 0.0, precision_digits=SHARES_PRECISION) <= 0:
                continue

            take_qty = min(remaining, available)
            lines.append({
                'sell_transaction_id': sell_transaction.id,
                'buy_transaction_id': lot['transaction'].id,
                'holder_id': holder.id,
                'company_partner_id': company_partner.id,
                'security_class_id': security_class.id,
                'qty': take_qty,
                'buy_unit_price': lot['transaction'].security_price,
                'sell_unit_price': sell_price,
            })
            remaining -= take_qty

        if float_compare(remaining, 0.0, precision_digits=SHARES_PRECISION) > 0:
            raise ValidationError(_(
                "Insufficient undisposed buy lots for holder %(holder)s on "
                "share class %(share_class)s. Shortfall: %(shortfall)s share(s).",
                holder=holder.display_name,
                share_class=security_class.display_name,
                shortfall=remaining,
            ))
        return lines

    @api.model
    def _get_remaining_buy_lots(self, holder, company_partner, security_class,
                                before_date, exclude_transaction_id=None):
        """Return buy lots with remaining undisposed quantity, oldest first."""
        Transaction = self.env['equity.transaction']
        LotAllocation = self.env['equity.lot.allocation']

        domain = [
            ('partner_id', '=', company_partner.id),
            ('securities_type', '=', 'shares'),
            ('date', '<=', before_date),
            ('state', 'in', LEDGER_ELIGIBLE_STATES),
            ('id', '!=', exclude_transaction_id or 0),
        ]
        candidates = Transaction.search(domain, order='date asc, id asc')
        buy_transactions = candidates.filtered(
            lambda tx: self._is_buy_lot_for_holder(tx, holder, security_class)
        )

        allocated_by_buy = self._get_allocated_qty_by_buy(buy_transactions.ids)

        lots = []
        for buy_tx in buy_transactions:
            allocated = allocated_by_buy.get(buy_tx.id, 0.0)
            remaining = buy_tx.securities - allocated
            if float_compare(remaining, 0.0, precision_digits=SHARES_PRECISION) > 0:
                lots.append({
                    'transaction': buy_tx,
                    'remaining_qty': remaining,
                })
        return lots

    @api.model
    def _get_allocated_qty_by_buy(self, buy_transaction_ids):
        """Return {buy_transaction_id: allocated_qty} for the given buy lots."""
        totals = {buy_id: 0.0 for buy_id in buy_transaction_ids}
        if not buy_transaction_ids:
            return totals
        for allocation in self.env['equity.lot.allocation'].search([
            ('buy_transaction_id', 'in', buy_transaction_ids),
        ]):
            totals[allocation.buy_transaction_id.id] += allocation.qty
        return totals

    @staticmethod
    def _is_buy_lot_for_holder(transaction, holder, security_class):
        """True when the transaction increased the holder's position in the class."""
        if transaction.transaction_type == 'issuance':
            return (
                transaction.subscriber_id == holder
                and transaction.security_class_id == security_class
            )
        if transaction.transaction_type == 'transfer':
            return (
                transaction.subscriber_id == holder
                and transaction.security_class_id == security_class
            )
        if transaction.transaction_type == 'exercise':
            return (
                transaction.subscriber_id == holder
                and transaction.destination_class_id == security_class
            )
        return False

    @api.model
    def _summarize_fifo_lines(self, lines):
        cost_basis = sum(line['qty'] * line['buy_unit_price'] for line in lines)
        proceeds = sum(line['qty'] * line['sell_unit_price'] for line in lines)
        return {
            'cost_basis_total': cost_basis,
            'proceeds_total': proceeds,
            'realized_gain_loss': proceeds - cost_basis,
        }

    @api.model
    def _build_result_from_allocations(self, allocations):
        cost_basis = sum(allocations.mapped('cost_basis'))
        proceeds = sum(allocations.mapped('proceeds'))
        realized = sum(allocations.mapped('realized_gain_loss'))
        return {
            'allocation_ids': allocations.ids,
            'allocations': allocations,
            'cost_basis_total': cost_basis,
            'proceeds_total': proceeds,
            'realized_gain_loss': realized,
        }
