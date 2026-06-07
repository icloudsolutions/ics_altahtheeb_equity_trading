# -*- coding: utf-8 -*-

import hashlib
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

AUDIT_LOG_SIGNATURE_PARAM = 'ics_altahtheeb_equity_trading.audit_log_signature_secret'


class EquityTradeAuditLog(models.Model):
    _name = 'equity.trade.audit.log'
    _description = 'Equity Trade Audit Log'
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

    display_name = fields.Char(compute='_compute_display_name', store=True)
    name = fields.Char(required=True, readonly=True, copy=False, default=lambda self: _('New'))
    trade_order_id = fields.Many2one(
        comodel_name='equity.trade.order',
        required=True,
        ondelete='restrict',
        index=True,
        readonly=True,
    )
    equity_transaction_id = fields.Many2one(
        related='trade_order_id.equity_transaction_id',
        store=True,
        readonly=True,
    )
    user_id = fields.Many2one(
        comodel_name='res.users',
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
    )
    action = fields.Selection(
        selection=[
            ('amendment', 'Amendment'),
            ('override', 'Administrative Override'),
        ],
        required=True,
        readonly=True,
    )
    amendment_reason = fields.Text(readonly=True)
    snapshot_before = fields.Text(
        string="Snapshot Before Change",
        readonly=True,
        help="Read-only JSON snapshot of the trade order before the amendment.",
    )
    changed_fields = fields.Text(
        readonly=True,
        help="JSON map of field names to proposed new values.",
    )
    content_signature = fields.Char(
        string="Integrity Signature",
        readonly=True,
        copy=False,
        help="SHA-256 digest of the snapshot payload for tamper detection.",
    )
    company_id = fields.Many2one(
        related='trade_order_id.company_id',
        store=True,
        readonly=True,
    )

    @api.depends('name', 'trade_order_id', 'action')
    def _compute_display_name(self):
        for log in self:
            trade_name = log.trade_order_id.display_name or ''
            log.display_name = f'{log.name} — {trade_name} ({log.action})'

    @api.model_create_multi
    def create(self, vals_list):
        AuditLog = self.env['equity.trade.audit.log']
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'equity.trade.audit.log'
                ) or _('New')
            snapshot = vals.get('snapshot_before') or '{}'
            vals['content_signature'] = AuditLog._sign_snapshot(snapshot)
        return super().create(vals_list)

    def write(self, vals):
        raise UserError(_(
            "Equity trade audit logs are immutable historical records and cannot be modified."
        ))

    def unlink(self):
        raise UserError(_(
            "Equity trade audit logs cannot be deleted."
        ))

    @api.model
    def _sign_snapshot(self, snapshot_text):
        secret = self.env['ir.config_parameter'].sudo().get_param(
            AUDIT_LOG_SIGNATURE_PARAM,
            self.env.cr.dbname,
        )
        payload = snapshot_text or '{}'
        return hashlib.sha256(f'{secret}{payload}'.encode()).hexdigest()

    @api.model
    def verify_signature(self, snapshot_text, signature):
        """Return True when the stored signature matches the snapshot payload."""
        if not signature:
            return False
        return signature == self._sign_snapshot(snapshot_text)
