# -*- coding: utf-8 -*-
"""
equity.trade.order
==================
Immutable buy/sell trade log linked 1:1 to ``equity.transaction``.

Once a trade reaches ``confirmed`` or ``posted`` state, ``write()`` and
``unlink()`` are blocked for all users except internal workflow calls that
pass ``equity_trade_system_write`` in the environment context.

Administrative amendments to pending trades (``draft`` / ``pending``) are
allowed for Equity Trading managers but always produce a signed read-only row
in ``equity.trade.audit.log`` before the change is applied.
"""

import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import tools

IMMUTABLE_TRADE_STATES = frozenset({'confirmed', 'posted'})
PENDING_TRADE_STATES = frozenset({'draft', 'pending'})
MANAGER_GROUP = 'ics_altahtheeb_equity_trading.group_equity_trading_manager'
SYSTEM_WRITE_CONTEXT_KEY = 'equity_trade_system_write'
AMENDMENT_REASON_CONTEXT_KEY = 'equity_trade_amendment_reason'

TRANSACTION_TO_TRADE_STATE = {
    'draft': 'draft',
    'confirmed': 'confirmed',
    'waiting_signature': 'pending',
    'signed_legal': 'posted',
    'done': 'posted',
    'cancelled': 'cancelled',
}

TRADE_SNAPSHOT_FIELDS = (
    'name',
    'trade_side',
    'state',
    'company_id',
    'equity_transaction_id',
    'marketplace_listing_id',
    'shareholder_id',
    'counterparty_id',
    'share_class_id',
    'quantity',
    'unit_price',
    'amount_total',
    'currency_id',
    'transaction_date',
)


class EquityTradeOrder(models.Model):
    _name = 'equity.trade.order'
    _description = 'Equity Trade Order'
    _inherit = ['mail.thread']
    _order = 'transaction_date desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    equity_transaction_id = fields.Many2one(
        comodel_name='equity.transaction',
        string="Equity Transaction",
        required=True,
        ondelete='restrict',
        index=True,
        copy=False,
        readonly=True,
    )
    marketplace_listing_id = fields.Many2one(
        comodel_name='equity.marketplace.board',
        string="Marketplace Listing",
        readonly=True,
        copy=False,
    )
    trade_side = fields.Selection(
        selection=[
            ('buy', 'Buy'),
            ('sell', 'Sell'),
            ('other', 'Other'),
        ],
        required=True,
        tracking=True,
        readonly=True,
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('pending', 'Pending'),
            ('confirmed', 'Confirmed'),
            ('posted', 'Posted'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft',
        required=True,
        tracking=True,
        copy=False,
    )
    shareholder_id = fields.Many2one(
        comodel_name='res.partner',
        string="Shareholder",
        tracking=True,
        readonly=True,
    )
    counterparty_id = fields.Many2one(
        comodel_name='res.partner',
        string="Counterparty",
        tracking=True,
        readonly=True,
    )
    share_class_id = fields.Many2one(
        comodel_name='equity.security.class',
        string="Share Class",
        tracking=True,
        readonly=True,
    )
    quantity = fields.Float(
        digits=(16, 6),
        tracking=True,
        readonly=True,
    )
    unit_price = fields.Float(
        string="Unit Price",
        digits='Product Price',
        tracking=True,
        readonly=True,
    )
    amount_total = fields.Monetary(
        compute='_compute_amount_total',
        store=True,
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id',
        store=True,
        readonly=True,
    )
    transaction_date = fields.Date(
        string="Trade Date",
        tracking=True,
        readonly=True,
    )
    audit_log_ids = fields.One2many(
        comodel_name='equity.trade.audit.log',
        inverse_name='trade_order_id',
        string="Audit Logs",
        readonly=True,
    )
    audit_log_count = fields.Integer(compute='_compute_audit_log_count')

    _equity_transaction_unique = models.Constraint(
        'UNIQUE(equity_transaction_id)',
        _('Each equity transaction may only have one trade order log.'),
    )

    @api.depends('quantity', 'unit_price')
    def _compute_amount_total(self):
        for order in self:
            order.amount_total = order.quantity * order.unit_price

    @api.depends('audit_log_ids')
    def _compute_audit_log_count(self):
        for order in self:
            order.audit_log_count = len(order.audit_log_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'equity.trade.order'
                ) or _('New')
        return super().create(vals_list)

    def write(self, vals):
        self._raise_if_immutable(vals)
        self._audit_admin_pending_amendment(vals)
        return super().write(vals)

    def unlink(self):
        locked = self.filtered(lambda order: order.state in IMMUTABLE_TRADE_STATES)
        if locked:
            tools.raise_bilingual_user_error(
                "Validated equity trade orders cannot be deleted. "
                "The following records are locked: %(records)s.",
                "لا يمكن حذف أوامر تداول الأسهم المعتمدة. "
                "السجلات التالية مقفلة: %(records)s.",
                records=', '.join(locked.mapped('display_name')),
            )
        return super().unlink()

    # -------------------------------------------------------------------------
    # Immutability helpers
    # -------------------------------------------------------------------------

    def _raise_if_immutable(self, vals):
        if not vals or self.env.context.get(SYSTEM_WRITE_CONTEXT_KEY):
            return
        locked = self.filtered(lambda order: order.state in IMMUTABLE_TRADE_STATES)
        if locked:
            tools.raise_bilingual_user_error(
                "Validated equity trade orders are immutable once confirmed or posted. "
                "Blocked records: %(records)s.",
                "أوامر تداول الأسهم المعتمدة غير قابلة للتعديل بعد التأكيد أو الترحيل. "
                "السجلات المحظورة: %(records)s.",
                records=', '.join(locked.mapped('display_name')),
            )

    def _audit_admin_pending_amendment(self, vals):
        if not vals or self.env.context.get(SYSTEM_WRITE_CONTEXT_KEY):
            return
        if not self.env.user.has_group(MANAGER_GROUP):
            return

        pending = self.filtered(lambda order: order.state in PENDING_TRADE_STATES)
        if not pending:
            return

        audited_changes = self._extract_audited_changes(vals)
        if not audited_changes:
            return

        reason = self.env.context.get(AMENDMENT_REASON_CONTEXT_KEY)
        action = 'override' if reason else 'amendment'
        AuditLog = self.env['equity.trade.audit.log'].sudo()
        for order in pending:
            AuditLog.create({
                'trade_order_id': order.id,
                'user_id': self.env.user.id,
                'action': action,
                'amendment_reason': reason,
                'snapshot_before': order._build_snapshot_json(),
                'changed_fields': json.dumps(audited_changes, default=str, sort_keys=True),
            })

    @api.model
    def _extract_audited_changes(self, vals):
        ignored = {'message_follower_ids', 'message_ids', 'activity_ids'}
        return {
            key: value
            for key, value in vals.items()
            if key not in ignored
        }

    def _build_snapshot_json(self):
        self.ensure_one()
        payload = {}
        for field_name in TRADE_SNAPSHOT_FIELDS:
            field = self._fields[field_name]
            value = self[field_name]
            if field.type == 'many2one':
                payload[field_name] = value.id if value else False
            else:
                payload[field_name] = value
        return json.dumps(payload, default=str, sort_keys=True)

    # -------------------------------------------------------------------------
    # Transaction bridge
    # -------------------------------------------------------------------------

    @api.model
    def _trade_state_from_transaction(self, transaction_state):
        return TRANSACTION_TO_TRADE_STATE.get(transaction_state, 'draft')

    @api.model
    def _trade_side_from_transaction(self, transaction):
        if transaction.transaction_type == 'transfer' and transaction.seller_id:
            return 'sell'
        if transaction.transaction_type in ('issuance', 'exercise'):
            return 'buy'
        return 'other'

    @api.model
    def _prepare_from_transaction(self, transaction):
        shareholder = transaction.seller_id or transaction.subscriber_id
        counterparty = transaction.subscriber_id if transaction.seller_id else transaction.seller_id
        return {
            'company_id': transaction.company_id.id,
            'equity_transaction_id': transaction.id,
            'marketplace_listing_id': transaction.marketplace_listing_id.id,
            'trade_side': self._trade_side_from_transaction(transaction),
            'state': self._trade_state_from_transaction(transaction.state),
            'shareholder_id': shareholder.id if shareholder else False,
            'counterparty_id': counterparty.id if counterparty else False,
            'share_class_id': transaction.security_class_id.id,
            'quantity': transaction.securities,
            'unit_price': transaction.security_price,
            'transaction_date': transaction.date,
        }

    @api.model
    def sync_from_transaction(self, transaction, extra_vals=None):
        """Create or update the trade log mirror for an equity transaction."""
        TradeOrder = self.sudo().with_context(**{SYSTEM_WRITE_CONTEXT_KEY: True})
        order = TradeOrder.search([
            ('equity_transaction_id', '=', transaction.id),
        ], limit=1)
        vals = TradeOrder._prepare_from_transaction(transaction)
        if extra_vals:
            vals.update(extra_vals)
        if order:
            order.write(vals)
            return order
        return TradeOrder.create(vals)

    def action_open_audit_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Trade Audit Logs'),
            'res_model': 'equity.trade.audit.log',
            'view_mode': 'list,form',
            'domain': [('trade_order_id', '=', self.id)],
            'context': {'default_trade_order_id': self.id},
        }
