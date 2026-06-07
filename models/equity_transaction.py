# -*- coding: utf-8 -*-
"""
equity_transaction.py
=====================
Extends the native ``equity.transaction`` model with:

* Saudi CJSC statutory compliance checks (ROFR, shareholder minimums,
  ownership/voting concentration).
* Legal e-signature flow that spawns a sequential ``sign.request``.
* **Stale signature expiration** – the nightly cron
  ``_cron_expire_stale_signature_requests`` finds any transaction stuck in
  ``waiting_signature`` whose linked ``sign.request`` is older than the
  configurable system-parameter threshold (default 5 days), then:

    1. Cancels the Odoo Sign envelope via the native ``action_cancel()`` API.
    2. Reverts the linked ``equity.marketplace.board`` listing back to
       ``published`` and clears the matched counterparty so other partners
       can place new bids.
    3. Moves the transaction itself to ``cancelled``.
    4. Posts a bilingual (EN/AR) chatter note on both the transaction and
       the sign envelope, notifying all trade parties and scheduling a
       warning activity for equity trading managers.
"""

import logging
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare

from . import tools
from .equity_trade_order import (
    IMMUTABLE_TRADE_STATES,
    MANAGER_GROUP,
    PENDING_TRADE_STATES,
    SYSTEM_WRITE_CONTEXT_KEY,
)

_logger = logging.getLogger(__name__)

TRANSACTION_AUDITED_FIELDS = frozenset({
    'company_id',
    'date',
    'partner_id',
    'rofr_notice_date',
    'rofr_waiver_approved',
    'securities',
    'security_class_id',
    'security_price',
    'seller_id',
    'subscriber_id',
    'transaction_type',
})

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

SIGN_TEMPLATE_NAME = 'Al Tahtheeb Share Transfer Agreement'
LEGAL_FLOW_READY_STATE = 'confirmed'
LEGAL_SIGNATURE_CONFIRMED_MESSAGE_EN = (
    "Cryptographic identity verification verified. "
    "Equity ledger balances updated securely."
)
LEGAL_SIGNATURE_CONFIRMED_MESSAGE_AR = (
    "تم التحقق من الهوية الرقمية بنجاح. "
    "تم تحديث أرصدة سجل الأسهم بشكل آمن."
)
SHARES_PRECISION = 6

# System-parameter key and fallback for the stale-signature timeout window.
STALE_SIGNATURE_DAYS_PARAM = 'ics_altahtheeb_equity_trading.stale_signature_days'
DEFAULT_STALE_SIGNATURE_DAYS = 5


class EquityTransaction(models.Model):
    _inherit = 'equity.transaction'

    # -------------------------------------------------------------------------
    # Fields
    # -------------------------------------------------------------------------

    company_id = fields.Many2one(
        comodel_name='res.company',
        string="Odoo Company",
        default=lambda self: self.env.company,
        required=True,
        index=True,
    )
    sign_request_id = fields.Many2one(
        comodel_name='sign.request',
        string="Signature Request",
        copy=False,
        readonly=True,
        tracking=True,
        help="Odoo Sign envelope bound to this equity transfer agreement.",
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            (LEGAL_FLOW_READY_STATE, 'Confirmed'),
            ('waiting_signature', 'Waiting Signature'),
            ('signed_legal', 'Legally Signed'),
            ('done', 'Done'),
            ('cancelled', 'Cancelled'),
        ],
        string="Status",
        default='draft',
        required=True,
        tracking=True,
        copy=False,
    )
    rofr_notice_date = fields.Date(
        string="ROFR Notice Date",
        tracking=True,
        help=(
            "Date the share block / transfer notice was logged to existing shareholders "
            "to start the statutory right-of-first-refusal window."
        ),
    )
    rofr_waiver_approved = fields.Boolean(
        string="ROFR Waived by Governance",
        tracking=True,
        help="Board-approved waiver of the right-of-first-refusal window for this transaction.",
    )
    marketplace_listing_id = fields.Many2one(
        comodel_name='equity.marketplace.board',
        string="Marketplace Listing",
        copy=False,
        readonly=True,
        index=True,
        help="Source marketplace listing that originated this equity transaction.",
    )
    lot_allocation_ids = fields.One2many(
        comodel_name='equity.lot.allocation',
        inverse_name='sell_transaction_id',
        string="FIFO Lot Allocations",
        copy=False,
        readonly=True,
    )
    disposal_processed = fields.Boolean(
        string="FIFO Disposal Processed",
        copy=False,
        readonly=True,
    )
    disposal_cost_basis = fields.Monetary(
        string="Disposal Cost Basis",
        currency_field='equity_currency_id',
        copy=False,
        readonly=True,
    )
    disposal_proceeds = fields.Monetary(
        string="Disposal Proceeds",
        currency_field='equity_currency_id',
        copy=False,
        readonly=True,
    )
    realized_gain_loss = fields.Monetary(
        string="Realized Gain/Loss",
        currency_field='equity_currency_id',
        copy=False,
        readonly=True,
        tracking=True,
    )
    trade_order_ids = fields.One2many(
        comodel_name='equity.trade.order',
        inverse_name='equity_transaction_id',
        string="Trade Order Logs",
        readonly=True,
        copy=False,
    )

    # -------------------------------------------------------------------------
    # Trade-order immutability bridge
    # -------------------------------------------------------------------------

    def _ensure_trade_orders(self):
        TradeOrder = self.env['equity.trade.order']
        missing = self.filtered(lambda tx: not tx.trade_order_ids)
        for transaction in missing:
            TradeOrder.sync_from_transaction(transaction)

    def _check_trade_log_immutability(self, vals):
        if not vals or self.env.context.get(SYSTEM_WRITE_CONTEXT_KEY):
            return
        self._ensure_trade_orders()
        locked = self.filtered(
            lambda tx: tx.trade_order_ids[:1].state in IMMUTABLE_TRADE_STATES
        )
        if locked:
            tools.raise_bilingual_user_error(
                "Validated equity transactions are immutable once their trade log "
                "reaches confirmed or posted state. Blocked records: %(records)s.",
                "معاملات الأسهم المعتمدة غير قابلة للتعديل بعد وصول سجل التداول "
                "إلى حالة مؤكدة أو مرحّلة. السجلات المحظورة: %(records)s.",
                records=', '.join(locked.mapped('display_name')),
            )

    def _audit_trade_log_amendment(self, vals):
        if not vals or self.env.context.get(SYSTEM_WRITE_CONTEXT_KEY):
            return
        if not self.env.user.has_group(MANAGER_GROUP):
            return
        audited_changes = {
            key: value
            for key, value in vals.items()
            if key in TRANSACTION_AUDITED_FIELDS
        }
        if not audited_changes:
            return
        self._ensure_trade_orders()
        for transaction in self:
            order = transaction.trade_order_ids[:1]
            if order and order.state in PENDING_TRADE_STATES:
                order._audit_admin_pending_amendment(audited_changes)

    @api.model_create_multi
    def create(self, vals_list):
        transactions = super().create(vals_list)
        for transaction in transactions:
            self.env['equity.trade.order'].sync_from_transaction(transaction)
        return transactions

    def write(self, vals):
        self._check_trade_log_immutability(vals)
        self._audit_trade_log_amendment(vals)
        result = super(EquityTransaction, self).write(vals)
        sync_fields = {
            'company_id', 'date', 'marketplace_listing_id', 'securities',
            'security_class_id', 'security_price', 'seller_id', 'state',
            'subscriber_id',
        }
        if sync_fields.intersection(vals):
            for transaction in self:
                self.env['equity.trade.order'].sync_from_transaction(transaction)
        if vals.get('state') in ('signed_legal', 'done'):
            self._process_fifo_disposal_if_sell()
        return result

    def unlink(self):
        if not self.env.context.get(SYSTEM_WRITE_CONTEXT_KEY):
            self._ensure_trade_orders()
            locked = self.filtered(
                lambda tx: tx.trade_order_ids[:1].state in IMMUTABLE_TRADE_STATES
            )
            if locked:
                tools.raise_bilingual_user_error(
                    "Validated equity transactions cannot be deleted once their trade "
                    "log reaches confirmed or posted state. Blocked records: %(records)s.",
                    "لا يمكن حذف معاملات الأسهم المعتمدة بعد وصول سجل التداول "
                    "إلى حالة مؤكدة أو مرحّلة. السجلات المحظورة: %(records)s.",
                    records=', '.join(locked.mapped('display_name')),
                )
        return super().unlink()

    # -------------------------------------------------------------------------
    # Constraints
    # -------------------------------------------------------------------------

    @api.constrains('state', 'sign_request_id')
    def _check_sign_request_state(self):
        """Enforce sign-request / state coherence."""
        for transaction in self:
            if transaction.state == 'waiting_signature' and not transaction.sign_request_id:
                raise ValidationError(_(
                    "A signature request is required while waiting for signatures."
                ))
            if transaction.sign_request_id and transaction.state in ('draft', 'cancelled'):
                raise ValidationError(_(
                    "Linked signature requests must be cleared before a transaction "
                    "returns to draft or cancelled."
                ))

    @api.constrains('sign_request_id')
    def _check_unique_sign_request(self):
        """Prevent two equity transactions from sharing the same sign.request."""
        for transaction in self.filtered('sign_request_id'):
            duplicates = self.search([
                ('sign_request_id', '=', transaction.sign_request_id.id),
                ('id', '!=', transaction.id),
            ])
            if duplicates:
                tools.raise_bilingual_validation(
                    "Sign request \"%(request)s\" is already linked to another equity "
                    "transaction. Each sign envelope must be unique.",
                    "طلب التوقيع \"%(request)s\" مرتبط بالفعل بمعاملة أسهم أخرى. "
                    "يجب أن يكون كل مظروف توقيع فريداً.",
                    request=transaction.sign_request_id.display_name,
                )

    # -------------------------------------------------------------------------
    # FIFO disposal integration
    # -------------------------------------------------------------------------

    def _is_sell_disposal_candidate(self):
        """True when this transaction is a share transfer sale eligible for FIFO."""
        self.ensure_one()
        return (
            self.transaction_type == 'transfer'
            and self.seller_id
            and self.securities_type == 'shares'
            and float_compare(self.securities, 0.0, precision_digits=SHARES_PRECISION) > 0
        )

    def _process_fifo_disposal_if_sell(self):
        """Run FIFO lot allocation for finalized sell transfers."""
        Disposal = self.env['equity.transaction.disposal']
        for transaction in self.filtered(
            lambda tx: tx._is_sell_disposal_candidate()
            and tx.state in ('signed_legal', 'done')
            and not tx.disposal_processed
        ):
            Disposal.process_sell_transaction(transaction)

    def action_open_trade_order(self):
        self.ensure_one()
        order = self.trade_order_ids[:1]
        if not order:
            order = self.env['equity.trade.order'].sync_from_transaction(self)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Trade Order Log'),
            'res_model': 'equity.trade.order',
            'res_id': order.id,
            'view_mode': 'form',
        }

    def action_open_trade_audit_logs(self):
        self.ensure_one()
        order = self.trade_order_ids[:1]
        if not order:
            order = self.env['equity.trade.order'].sync_from_transaction(self)
        return order.action_open_audit_logs()

    def action_open_fifo_disposal_wizard(self):
        """Open the FIFO disposal wizard for a finalized sell transaction."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('FIFO Disposal'),
            'res_model': 'equity.transaction.disposal',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_sell_transaction_id': self.id,
            },
        }

    # -------------------------------------------------------------------------
    # Cap-table helpers
    # -------------------------------------------------------------------------

    def _cap_table_exclude_id(self):
        """Return a persisted id for cap-table simulation, avoiding transient NewId values."""
        self.ensure_one()
        origin_id = self._origin.id
        if origin_id:
            return origin_id
        if isinstance(self.id, int):
            return self.id
        return None

    def _cap_table_context(self):
        self.ensure_one()
        context = {}
        exclude_id = self._cap_table_exclude_id()
        if exclude_id:
            context['current_transaction_id'] = exclude_id
        return context

    # -------------------------------------------------------------------------
    # Governance helpers
    # -------------------------------------------------------------------------

    def _get_governance_company(self):
        self.ensure_one()
        return self.company_id or self.env.company

    def _get_transaction_buyer(self):
        """Return the acquirer / subscriber party for ownership checks."""
        self.ensure_one()
        if self.transaction_type in ('transfer', 'issuance', 'exercise', 'cancellation'):
            return self.subscriber_id
        return self.env['res.partner']

    def _has_external_buyer(self):
        """True when the counterparty is not an existing shareholder in the cap table."""
        self.ensure_one()
        buyer = self._get_transaction_buyer()
        if not buyer or self.securities_type != 'shares':
            return False
        if self.transaction_type not in ('transfer', 'issuance'):
            return False

        existing_entry = self.env['equity.cap.table'].with_context(
            **self._cap_table_context(),
        ).search([
            ('partner_id', '=', self.partner_id.id),
            ('holder_id', '=', buyer.id),
            ('securities_type', '=', 'shares'),
            ('securities', '>', 0),
        ], limit=1)
        return not existing_entry

    # -------------------------------------------------------------------------
    # Saudi statutory compliance checks
    # -------------------------------------------------------------------------

    def _check_saudi_rofr_window(self):
        """Rule 1: enforce ROFR waiting period for external share transfers."""
        for transaction in self:
            if not transaction._has_external_buyer() or transaction.rofr_waiver_approved:
                continue

            company = transaction._get_governance_company()
            rofr_days = company.equity_rofr_window_days or 15

            if not transaction.rofr_notice_date:
                tools.raise_bilingual_validation(
                    "Right of First Refusal (ROFR) compliance failure: an external buyer is "
                    "involved but no share block notice date has been logged. Log the ROFR "
                    "notice date and wait %(days)s days before initiating signatures.",
                    "مخالفة لحق الأولوية في الشراء (ROFR): يوجد مشترٍ خارجي ولكن لم "
                    "يتم تسجيل تاريخ إشعار عرض الحصص. يرجى تسجيل تاريخ الإشعار "
                    "والانتظار %(days)s يوماً قبل بدء التوقيعات.",
                    days=rofr_days,
                )

            rofr_deadline = transaction.rofr_notice_date + timedelta(days=rofr_days)
            if fields.Date.context_today(transaction) < rofr_deadline:
                tools.raise_bilingual_validation(
                    "Right of First Refusal (ROFR) compliance failure: the mandatory "
                    "%(days)s-day window has not elapsed since the share block notice "
                    "dated %(notice_date)s. Earliest eligible date: %(deadline)s.",
                    "مخالفة لحق الأولوية في الشراء (ROFR): لم تنتهِ مدة "
                    "%(days)s يوماً الإلزامية منذ إشعار عرض الحصص بتاريخ "
                    "%(notice_date)s. أقرب تاريخ مسموح: %(deadline)s.",
                    days=rofr_days,
                    notice_date=transaction.rofr_notice_date,
                    deadline=rofr_deadline,
                )

    def _get_simulated_share_holdings(self):
        """Return {holder_id: {security_class_id: shares}} after executing this transaction."""
        self.ensure_one()
        holdings = defaultdict(lambda: defaultdict(float))

        cap_entries = self.env['equity.cap.table'].with_context(
            **self._cap_table_context(),
        ).search([
            ('partner_id', '=', self.partner_id.id),
            ('securities_type', '=', 'shares'),
        ])
        for entry in cap_entries:
            if float_compare(entry.securities, 0.0, precision_digits=SHARES_PRECISION) > 0:
                holdings[entry.holder_id.id][entry.security_class_id.id] += entry.securities

        if self.securities_type != 'shares':
            return holdings

        class_id = self.security_class_id.id
        if self.transaction_type == 'transfer':
            if self.seller_id:
                holdings[self.seller_id.id][class_id] -= self.securities
            if self.subscriber_id:
                holdings[self.subscriber_id.id][class_id] += self.securities
        elif self.transaction_type == 'issuance' and self.subscriber_id:
            holdings[self.subscriber_id.id][class_id] += self.securities
        elif self.transaction_type == 'cancellation' and self.subscriber_id:
            holdings[self.subscriber_id.id][class_id] -= self.securities
        elif self.transaction_type == 'exercise' and self.subscriber_id:
            dest_class_id = self.destination_class_id.id
            holdings[self.subscriber_id.id][class_id] -= self.securities
            holdings[self.subscriber_id.id][dest_class_id] += self.securities

        return holdings

    @api.model
    def _count_active_shareholders(self, holdings):
        """Count distinct holders with a strictly positive share balance."""
        active_holders = set()
        for holder_id, class_map in holdings.items():
            total_shares = sum(class_map.values())
            if float_compare(total_shares, 0.0, precision_digits=SHARES_PRECISION) > 0:
                active_holders.add(holder_id)
        return len(active_holders)

    def _check_saudi_minimum_shareholder_count(self):
        """Rule 2: CJSC must maintain the statutory minimum shareholder count."""
        for transaction in self:
            if transaction.securities_type != 'shares':
                continue
            if transaction.transaction_type not in ('transfer', 'cancellation', 'exercise'):
                continue

            company = transaction._get_governance_company()
            min_shareholders = company.saudi_cjsc_min_shareholders or 2
            projected_count = self._count_active_shareholders(
                transaction._get_simulated_share_holdings()
            )

            if projected_count < min_shareholders:
                tools.raise_bilingual_validation(
                    "Saudi Closed Joint Stock Company (CJSC) compliance failure: executing "
                    "this transaction would reduce the shareholder count to %(projected)s, "
                    "below the MOCI statutory minimum of %(minimum)s shareholders for a "
                    "شركة مساهمة مقفلة.",
                    "مخالفة نظام الشركات السعودي للشركة المساهمة المقفلة: تنفيذ "
                    "هذه العملية سيخفض عدد المساهمين إلى %(projected)s، "
                    "أقل من الحد الأدنى النظامي البالغ %(minimum)s مساهماً "
                    "وفقاً لمتطلبات وزارة التجارة.",
                    projected=projected_count,
                    minimum=min_shareholders,
                )

    def _check_governance_concentration_limits(self):
        """Rule 3: ownership and multi-class voting concentration under company bylaws."""
        SecurityClass = self.env['equity.security.class']
        Partner = self.env['res.partner']

        for transaction in self:
            if transaction.securities_type != 'shares':
                continue
            if transaction.transaction_type not in ('transfer', 'issuance', 'exercise'):
                continue

            company = transaction._get_governance_company()
            max_ownership = company.equity_max_ownership_pct or 49.0
            max_voting = company.equity_max_voting_power_pct or 49.0
            max_votes_per_share = company.equity_max_votes_per_share or 1

            holdings = transaction._get_simulated_share_holdings()
            class_ids = {
                class_id
                for class_map in holdings.values()
                for class_id, qty in class_map.items()
                if float_compare(qty, 0.0, precision_digits=SHARES_PRECISION) > 0
            }
            if not class_ids:
                continue

            security_classes = SecurityClass.browse(list(class_ids))
            votes_per_share = {sc.id: sc.share_votes or 0 for sc in security_classes}

            for security_class in security_classes:
                if security_class.share_votes > max_votes_per_share:
                    tools.raise_bilingual_validation(
                        "Internal governance policy violation: security class "
                        "\"%(class_name)s\" grants %(votes)s votes per share, exceeding "
                        "the bylaws limit of %(limit)s vote(s) per share.",
                        "مخالفة لسياسة حوكمة الشركة: فئة الأسهم \"%(class_name)s\" "
                        "تمنح %(votes)s أصوات لكل سهم، وهذا يتجاوز "
                        "حد النظام الأساسي البالغ %(limit)s صوت لكل سهم.",
                        class_name=security_class.display_name,
                        votes=security_class.share_votes,
                        limit=max_votes_per_share,
                    )

            total_shares = sum(
                qty
                for class_map in holdings.values()
                for qty in class_map.values()
                if float_compare(qty, 0.0, precision_digits=SHARES_PRECISION) > 0
            )
            total_votes = sum(
                qty * votes_per_share.get(class_id, 0)
                for class_map in holdings.values()
                for class_id, qty in class_map.items()
                if float_compare(qty, 0.0, precision_digits=SHARES_PRECISION) > 0
            )
            if float_compare(total_shares, 0.0, precision_digits=SHARES_PRECISION) <= 0:
                continue

            for holder_id, class_map in holdings.items():
                holder_shares = sum(
                    qty for qty in class_map.values()
                    if float_compare(qty, 0.0, precision_digits=SHARES_PRECISION) > 0
                )
                if float_compare(holder_shares, 0.0, precision_digits=SHARES_PRECISION) <= 0:
                    continue

                holder_votes = sum(
                    qty * votes_per_share.get(class_id, 0)
                    for class_id, qty in class_map.items()
                    if float_compare(qty, 0.0, precision_digits=SHARES_PRECISION) > 0
                )
                ownership_pct = (holder_shares / total_shares) * 100.0
                voting_pct = (holder_votes / total_votes) * 100.0 if total_votes else 0.0
                holder_name = Partner.browse(holder_id).display_name

                if float_compare(ownership_pct, max_ownership, precision_digits=2) > 0:
                    tools.raise_bilingual_validation(
                        "Internal governance policy violation: shareholder "
                        "\"%(holder)s\" would hold %(pct).2f%% of issued share capital, "
                        "exceeding the maximum permitted concentration of %(limit).2f%%.",
                        "مخالفة لسياسة حوكمة الشركة: المساهم \"%(holder)s\" "
                        "سيملك %(pct).2f%% من رأس المال المصدر، "
                        "وهذا يتجاوز حد التركز المسموح البالغ %(limit).2f%%.",
                        holder=holder_name,
                        pct=ownership_pct,
                        limit=max_ownership,
                    )

                if float_compare(voting_pct, max_voting, precision_digits=2) > 0:
                    tools.raise_bilingual_validation(
                        "Internal governance policy violation: shareholder "
                        "\"%(holder)s\" would control %(pct).2f%% of aggregate voting power "
                        "after multi-class weighting, exceeding the bylaws limit of "
                        "%(limit).2f%%.",
                        "مخالفة لسياسة حوكمة الشركة: المساهم \"%(holder)s\" "
                        "سيتحكم في %(pct).2f%% من إجمالي قوة التصويت "
                        "بعد أوزان فئات الأسهم، وهذا يتجاوز "
                        "حد النظام الأساسي البالغ %(limit).2f%%.",
                        holder=holder_name,
                        pct=voting_pct,
                        limit=max_voting,
                    )

    def check_saudi_statutory_bounds(self):
        """Validate Saudi MOCI CJSC statutory and internal governance boundaries."""
        for transaction in self:
            transaction._check_saudi_rofr_window()
            transaction._check_saudi_minimum_shareholder_count()
            transaction._check_governance_concentration_limits()
        return True

    # -------------------------------------------------------------------------
    # Legal signature flow
    # -------------------------------------------------------------------------

    @api.model
    def _get_sign_template(self):
        """Return the Al Tahtheeb Share Transfer Agreement Sign template."""
        sign_template = self.env['sign.template'].search([
            ('name', '=', SIGN_TEMPLATE_NAME),
        ], limit=1)
        if not sign_template:
            tools.raise_bilingual_user_error(
                "The Sign template \"%(template)s\" was not found. "
                "Please create it in the Sign application before initiating the legal flow.",
                "لم يتم العثور على قالب التوقيع \"%(template)s\". "
                "يرجى إنشاؤه في تطبيق Sign قبل بدء الإجراء القانوني.",
                template=SIGN_TEMPLATE_NAME,
            )
        return sign_template

    @api.model
    def _get_template_signer_roles(self, sign_template):
        """Return exactly two ordered signer roles from the Sign template."""
        roles = sign_template.sign_item_ids.responsible_id.sorted()
        if len(roles) != 2:
            tools.raise_bilingual_user_error(
                "The Sign template \"%(template)s\" must define exactly two signer roles "
                "(shareholder and corporate authority). Found %(count)s role(s).",
                "يجب أن يحدد قالب التوقيع \"%(template)s\" دوري موقعين بالضبط "
                "(المساهم والمفوض). العدد الحالي: %(count)s.",
                template=sign_template.name,
                count=len(roles),
            )
        return roles

    def _get_legal_flow_shareholder(self):
        """Return the shareholder partner who must sign first."""
        self.ensure_one()
        shareholder = self.seller_id or self.subscriber_id or self.partner_id
        if not shareholder:
            tools.raise_bilingual_user_error(
                "Cannot initiate the legal flow: no shareholder is set on this transaction.",
                "لا يمكن بدء الإجراء القانوني: لم يتم تحديد مساهم في هذه المعاملة.",
            )
        return shareholder

    def _get_corporate_authority_partner(self):
        """Return Al Tahtheeb's corporate authority representative partner."""
        self.ensure_one()
        corporate_partner = self.company_id.partner_id
        if not corporate_partner:
            tools.raise_bilingual_user_error(
                "Cannot initiate the legal flow: the Odoo company has no linked partner.",
                "لا يمكن بدء الإجراء القانوني: لا يوجد شريك مرتبط بشركة Odoo.",
            )
        return corporate_partner

    def _validate_signer_partner(self, partner, role_label):
        """Ensure a signer partner is eligible for the Sign request."""
        if not partner.email:
            tools.raise_bilingual_user_error(
                "%(role)s \"%(partner)s\" must have an email address before "
                "the signature request can be sent.",
                "يجب أن يمتلك %(role)s \"%(partner)s\" عنوان بريد إلكتروني "
                "قبل إرسال طلب التوقيع.",
                role=role_label,
                partner=partner.display_name,
            )

    def action_initiate_legal_flow(self):
        """Create a sequential Sign request and move the transaction to waiting_signature."""
        self.ensure_one()
        self.check_access('write')
        self.check_saudi_statutory_bounds()

        if self.state != LEGAL_FLOW_READY_STATE:
            tools.raise_bilingual_user_error(
                "The legal signature flow can only be started when the transaction "
                "is confirmed and ready for processing.",
                "يمكن بدء الإجراء القانوني للتوقيع فقط عندما تكون المعاملة "
                "مؤكدة وجاهزة للمعالجة.",
            )
        if self.sign_request_id:
            tools.raise_bilingual_user_error(
                "A signature request is already linked to this transaction.",
                "يوجد بالفعل طلب توقيع مرتبط بهذه المعاملة.",
            )

        sign_template = self._get_sign_template()
        roles = self._get_template_signer_roles(sign_template)
        shareholder = self._get_legal_flow_shareholder()
        corporate_partner = self._get_corporate_authority_partner()

        self._validate_signer_partner(shareholder, _('Shareholder'))
        self._validate_signer_partner(corporate_partner, _('Corporate Authority Representative'))

        reference = _('Share Transfer - %s', self.display_name)
        subject = _('Signature Request - %(document)s', document=reference)

        sign_request = self.env['sign.request'].create({
            'template_id': sign_template.id,
            'reference': reference,
            'subject': subject,
            'reference_doc': f'{self._name},{self.id}',
            'communication_company_id': self.company_id.id,
            'request_item_ids': [
                Command.create({
                    'partner_id': shareholder.id,
                    'role_id': roles[0].id,
                    'mail_sent_order': 1,
                }),
                Command.create({
                    'partner_id': corporate_partner.id,
                    'role_id': roles[1].id,
                    'mail_sent_order': 2,
                }),
            ],
        })

        self.with_context(**{SYSTEM_WRITE_CONTEXT_KEY: True}).write({
            'sign_request_id': sign_request.id,
            'state': 'waiting_signature',
        })

        if self.marketplace_listing_id:
            self.marketplace_listing_id.write({'state': 'sign_process'})

        _logger.info(
            "Legal signature flow initiated for equity.transaction %s; sign.request %s created.",
            self.id,
            sign_request.id,
        )

        self.message_post(body=_(
            "%(english)s\n\n%(arabic)s",
            english=_(
                "Legal signature request initiated. "
                "The shareholder will sign first, followed by the corporate authority. "
                "Track progress here: %s",
                sign_request._get_html_link(),
            ),
            arabic=_(
                "تم بدء طلب التوقيع القانوني. "
                "يوقع المساهم أولاً يليه المفوض بالتوقيع. "
                "تابع التقدم هنا: %s",
                sign_request._get_html_link(),
            ),
        ))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Signature Request'),
            'res_model': 'sign.request',
            'res_id': sign_request.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # -------------------------------------------------------------------------
    # Post-signature finalisation
    # -------------------------------------------------------------------------

    def _finalize_after_legal_signature(self):
        """Lock legally signed transactions into the native equity cap table ledger."""
        waiting_transactions = self.filtered(
            lambda t: t.state == 'waiting_signature'
        )
        if not waiting_transactions:
            return waiting_transactions

        waiting_transactions.with_context(**{SYSTEM_WRITE_CONTEXT_KEY: True}).write({
            'state': 'signed_legal',
        })
        waiting_transactions.flush_model(['state'])
        self.env['equity.cap.table'].invalidate_model()
        waiting_transactions._process_fifo_disposal_if_sell()

        message = _(
            "%(english)s\n\n%(arabic)s",
            english=_(LEGAL_SIGNATURE_CONFIRMED_MESSAGE_EN),
            arabic=_(LEGAL_SIGNATURE_CONFIRMED_MESSAGE_AR),
        )
        for transaction in waiting_transactions:
            transaction.message_post(body=message)

        _logger.info(
            "Equity transactions %s finalized to signed_legal; cap table cache invalidated.",
            waiting_transactions.ids,
        )
        return waiting_transactions

    def _portal_get_sign_item_for_partner(self, partner):
        """Return the sign.request.item assigned to the portal partner, if any."""
        self.ensure_one()
        sign_request = self.sign_request_id
        if not sign_request or sign_request.state != 'sent':
            return self.env['sign.request.item']

        return sign_request.request_item_ids.filtered(
            lambda item: item.partner_id == partner
        )[:1]

    # =========================================================================
    # Stale signature expiration — nightly cron
    # =========================================================================

    @api.model
    def _get_stale_signature_days(self):
        """
        Read the stale signature timeout (in days) from System Parameters.

        Key : ``ics_altahtheeb_equity_trading.stale_signature_days``
        Default: ``5``

        Navigate to Settings → Technical → Parameters → System Parameters to
        adjust the value without a code deployment.
        """
        icp = self.env['ir.config_parameter'].sudo()
        raw_value = icp.get_param(
            STALE_SIGNATURE_DAYS_PARAM,
            str(DEFAULT_STALE_SIGNATURE_DAYS),
        )
        try:
            days = int(raw_value)
        except (TypeError, ValueError):
            _logger.warning(
                "System parameter '%s' has a non-integer value '%s'; "
                "falling back to default of %s day(s).",
                STALE_SIGNATURE_DAYS_PARAM,
                raw_value,
                DEFAULT_STALE_SIGNATURE_DAYS,
            )
            days = DEFAULT_STALE_SIGNATURE_DAYS
        # Guard against accidental zero/negative configuration.
        return max(days, 1)

    @api.model
    def _cron_expire_stale_signature_requests(self):
        """
        **Nightly cron entry point** — expire sign requests that are stuck.

        Searches across all companies for ``equity.transaction`` records in
        state ``waiting_signature`` whose linked ``sign.request`` was created
        more than N days ago (N = system parameter, default 5).

        For each stale record the method delegates to
        ``_expire_stale_signature_request`` which:

        1. Calls ``sign.request.action_cancel()`` — the native Odoo 19 Sign
           cancellation API.
        2. Reverts the linked ``equity.marketplace.board`` back to
           ``published`` and clears ``matched_partner_id`` so the listing is
           visible and biddable again.
        3. Cancels the ``equity.transaction`` itself.
        4. Posts bilingual chatter notifications and schedules manager
           warning activities on both the transaction and the listing.

        Runs in a ``with_company``-agnostic context so multi-company setups
        are handled without additional filters.
        """
        stale_days = self._get_stale_signature_days()
        cutoff = fields.Datetime.now() - timedelta(days=stale_days)

        stale_transactions = self.search([
            ('state', '=', 'waiting_signature'),
            ('sign_request_id', '!=', False),
            # Only target envelopes still awaiting signatures; already-cancelled
            # or signed envelopes are excluded automatically.
            ('sign_request_id.state', '=', 'sent'),
            # The sign.request create_date marks when the envelope was dispatched.
            ('sign_request_id.create_date', '<=', cutoff),
        ])

        if not stale_transactions:
            _logger.info(
                "Stale signature cron: no equity.transaction records exceeded %s day(s).",
                stale_days,
            )
            return True

        _logger.info(
            "Stale signature cron: found %s transaction(s) exceeding %s day(s). "
            "Processing expiration now.",
            len(stale_transactions),
            stale_days,
        )

        expired_count = 0
        for transaction in stale_transactions.with_context(ics_expiring_stale_signature=True):
            try:
                if transaction._expire_stale_signature_request(stale_days):
                    expired_count += 1
            except Exception:
                # Log and continue so one bad record does not abort the entire batch.
                _logger.exception(
                    "Stale signature cron: unexpected error while expiring "
                    "equity.transaction %s — skipping.",
                    transaction.id,
                )

        _logger.info(
            "Stale signature cron completed: voided %s / %s equity.transaction record(s) "
            "after %s day(s).",
            expired_count,
            len(stale_transactions),
            stale_days,
        )
        return True

    # -------------------------------------------------------------------------
    # Stale expiration helpers (called per-record from the cron)
    # -------------------------------------------------------------------------

    def _get_trade_notification_partners(self):
        """
        Collect all stakeholder partners who must be notified about a voided trade.

        Includes the seller, subscriber, cap-table company partner, and both
        sides of the linked marketplace listing.
        """
        self.ensure_one()
        partners = self.env['res.partner']
        if self.seller_id:
            partners |= self.seller_id
        if self.subscriber_id:
            partners |= self.subscriber_id
        if self.partner_id:
            partners |= self.partner_id
        listing = self.marketplace_listing_id
        if listing:
            if listing.shareholder_id:
                partners |= listing.shareholder_id
            if listing.matched_partner_id:
                partners |= listing.matched_partner_id
        # Filter to records with real database IDs to avoid portal ghost records.
        return partners.filtered('id')

    def _schedule_stale_signature_admin_activity(self, summary):
        """
        Schedule a warning activity for every user in the equity trading
        manager group so the team can review the automatic void.

        Falls back to the ``base.user_root`` (OdooBot) if the security group
        is not found, ensuring the activity is never silently dropped.
        """
        activity_type = self.env.ref(
            'mail.mail_activity_data_warning', raise_if_not_found=False
        )
        if not activity_type:
            activity_type = self.env.ref('mail.mail_activity_data_todo')

        manager_group = self.env.ref(
            'ics_altahtheeb_equity_trading.group_equity_trading_manager',
            raise_if_not_found=False,
        )
        users = manager_group.users if manager_group else self.env.ref('base.user_root')
        for user in users:
            self.activity_schedule(
                activity_type_id=activity_type.id,
                summary=summary,
                user_id=user.id,
            )

    def _log_stale_signature_void(self, stale_days, sign_request, listing):
        """
        Post bilingual chatter notes on the transaction, the sign envelope,
        and the marketplace listing to ensure both parties and administrators
        receive a clear, auditable explanation for the automatic cancellation.

        :param int stale_days:          Configured timeout that triggered expiry.
        :param sign_request:            The ``sign.request`` recordset (may be
                                        already cancelled but still in DB).
        :param listing:                 The linked ``equity.marketplace.board``
                                        record or an empty recordset.
        """
        self.ensure_one()
        parties = self._get_trade_notification_partners()

        void_message = _(
            "%(english)s\n\n%(arabic)s",
            english=_(
                "⚠️ This equity trade was automatically voided by the system because "
                "the linked Sign request remained incomplete for more than %(days)s day(s). "
                "The Sign envelope has been cancelled and any linked marketplace listing "
                "has been released back to published status so other partners may bid again. "
                "Please create a new trade if you wish to proceed.",
                days=stale_days,
            ),
            arabic=_(
                "⚠️ تم إلغاء صفقة الأسهم هذه تلقائياً من قِبَل النظام لأن طلب التوقيع "
                "المرتبط لم يكتمل لأكثر من %(days)s يوماً. تم إلغاء مظروف التوقيع "
                "وإعادة إعلان السوق المرتبط إلى حالة المنشور ليتمكن الشركاء الآخرون من "
                "تقديم عروض جديدة. يرجى إنشاء صفقة جديدة إذا كنت ترغب في المتابعة.",
                days=stale_days,
            ),
        )

        # 1. Chatter note on the transaction itself, mentioning all parties.
        self.message_post(body=void_message, partner_ids=parties.ids)

        # 2. Schedule a warning activity for equity trading managers.
        self._schedule_stale_signature_admin_activity(_(
            "Stale signature void — review required: %s",
            self.display_name,
        ))

        # 3. Post on the sign envelope so the audit trail is complete there too.
        if sign_request.exists():
            sign_request.message_post(
                body=_(
                    "%(english)s\n\n%(arabic)s",
                    english=_(
                        "Sign request automatically cancelled after %(days)s day(s) "
                        "without completion for equity transaction %(transaction)s.",
                        days=stale_days,
                        transaction=self.display_name,
                    ),
                    arabic=_(
                        "تم إلغاء طلب التوقيع تلقائياً بعد %(days)s يوماً دون "
                        "إتمام للمعاملة %(transaction)s.",
                        days=stale_days,
                        transaction=self.display_name,
                    ),
                ),
                partner_ids=parties.ids,
            )

        # 4. Schedule a separate activity on the listing for full traceability.
        if listing:
            listing._schedule_stale_signature_admin_activity(_(
                "Marketplace listing %s released after stale signature timeout — review required",
                listing.name,
            ))

    def _cancel_linked_sign_request(self):
        """
        Cancel the Odoo Sign envelope linked to this transaction.

        Uses the **native ``sign.request.action_cancel()``** method introduced
        in Odoo 16 and preserved through Odoo 19. This is the correct public
        API; calling the lower-level ``cancel()`` method directly bypasses the
        Sign module's own state-machine guards and mail notifications.

        A ``UserError`` is silently logged instead of re-raised so that a
        Sign envelope that has already been cancelled (e.g., by an operator
        manually) does not abort the broader expiration transaction.

        :returns: ``True`` if the envelope was cancelled, ``False`` otherwise.
        """
        self.ensure_one()
        sign_request = self.sign_request_id
        if not sign_request:
            return False
        if sign_request.state != 'sent':
            # Already cancelled, signed, or in another terminal state — nothing to do.
            _logger.info(
                "equity.transaction %s: sign.request %s is in state '%s'; "
                "skipping action_cancel.",
                self.id,
                sign_request.id,
                sign_request.state,
            )
            return False
        try:
            # action_cancel() is the public Odoo 19 Sign API.  It transitions
            # the envelope to 'canceled', sends cancellation notifications to
            # all signers, and is safe to call from server-side code.
            sign_request.action_cancel()
        except UserError as exc:
            # Log but do not propagate; stale expiration must continue even if
            # the Sign module raises (e.g., the template was archived).
            _logger.warning(
                "equity.transaction %s: action_cancel() on sign.request %s raised "
                "UserError — %s. Continuing with transaction cancellation.",
                self.id,
                sign_request.id,
                exc,
            )
            return False
        return True

    def _expire_stale_signature_request(self, stale_days):
        """
        Void a single stale trade in one atomic savepoint.

        Execution order (must be preserved for constraint safety):

        1. **Cancel the Sign envelope** — ``action_cancel()`` transitions the
           ``sign.request`` to ``canceled`` while the ``sign_request_id`` FK is
           still set on the transaction.  This avoids the
           ``_check_sign_request_state`` constraint which forbids a ``cancelled``
           transaction from carrying a sign request FK.

        2. **Cancel the transaction** and **clear ``sign_request_id``** in one
           ``write()`` call.  The constraint evaluates both fields together after
           the write, so both reach their final values simultaneously.

        3. **Release the marketplace listing** — revert to ``published``, clear
           ``matched_partner_id`` and ``equity_transaction_id`` so new bids are
           accepted.

        4. **Notify all parties** — bilingual chatter + manager activity.

        :param int stale_days: Timeout value used in notification messages.
        :returns: ``True`` if the record was successfully expired.
        """
        self.ensure_one()
        if self.state != 'waiting_signature' or not self.sign_request_id:
            return False

        sign_request = self.sign_request_id  # capture before clearing FK
        listing = self.marketplace_listing_id  # capture before clearing FK

        with self.env.cr.savepoint():
            # Step 1 — cancel the Odoo Sign envelope via the public API.
            self._cancel_linked_sign_request()

            # Step 2 — cancel the transaction and clear the sign FK atomically.
            # Both fields are written in a single call so the
            # _check_sign_request_state constraint sees the correct final state.
            self.with_context(**{SYSTEM_WRITE_CONTEXT_KEY: True}).write({
                'state': 'cancelled',
                'sign_request_id': False,
            })

            # Invalidate the cap-table cache so any downstream views reflect
            # that this transfer is no longer in progress.
            self.env['equity.cap.table'].invalidate_model()

            # Step 3 — release the marketplace listing back to published state.
            if listing:
                listing._release_after_stale_signature(
                    stale_days=stale_days,
                    transaction=self,
                )

            # Step 4 — post notifications and schedule manager activities.
            self._log_stale_signature_void(stale_days, sign_request, listing)

        _logger.info(
            "equity.transaction %s expired after %s day(s): "
            "sign.request %s cancelled, state -> cancelled%s.",
            self.id,
            stale_days,
            sign_request.id,
            f", marketplace listing {listing.name} released" if listing else "",
        )
        return True

    # -------------------------------------------------------------------------
    # Manual sign-request cancellation (UI action)
    # -------------------------------------------------------------------------

    def action_cancel_sign_request(self):
        """
        Allow an authorised backend user to manually cancel a pending sign
        request and return the transaction to the confirmed state for re-processing.

        This intentionally does **not** cancel the marketplace listing because a
        manual operator action implies the parties still intend to trade — the
        listing should only be released automatically after the stale timeout.
        """
        self.ensure_one()
        if not self.sign_request_id:
            tools.raise_bilingual_user_error(
                "No signature request is linked to this transaction.",
                "لا يوجد طلب توقيع مرتبط بهذه المعاملة.",
            )
        # Use the public action_cancel() API — same as the cron, for consistency.
        self._cancel_linked_sign_request()
        self.with_context(**{SYSTEM_WRITE_CONTEXT_KEY: True}).write({
            'state': LEGAL_FLOW_READY_STATE,
            'sign_request_id': False,
        })
        # Revert listing to approved so it can be re-submitted for signature
        # by the operator without re-matching from scratch.
        if self.marketplace_listing_id:
            self.marketplace_listing_id.write({'state': 'approved'})
        self.message_post(body=_(
            "%(english)s\n\n%(arabic)s",
            english=_(
                "Signature request manually cancelled by %(user)s. "
                "Transaction returned to confirmed state for re-processing.",
                user=self.env.user.display_name,
            ),
            arabic=_(
                "تم إلغاء طلب التوقيع يدوياً بواسطة %(user)s. "
                "أُعيدت المعاملة إلى الحالة المؤكدة لإعادة المعالجة.",
                user=self.env.user.display_name,
            ),
        ))
        return True
