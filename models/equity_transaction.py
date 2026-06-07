# -*- coding: utf-8 -*-

import logging
from collections import defaultdict
from datetime import timedelta

from odoo import _, Command, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)

SIGN_TEMPLATE_NAME = 'Al Tahtheeb Share Transfer Agreement'
LEGAL_FLOW_READY_STATE = 'confirmed'
LEGAL_SIGNATURE_CONFIRMED_MESSAGE = (
    "Cryptographic identity verification verified. "
    "Equity ledger balances updated securely."
)
SHARES_PRECISION = 6


class EquityTransaction(models.Model):
    _inherit = 'equity.transaction'

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
        help="Date the share block / transfer notice was logged to existing shareholders "
             "to start the statutory right-of-first-refusal window.",
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
    )

    # -------------------------------------------------------------------------
    # Saudi MOCI / CJSC statutory compliance
    # -------------------------------------------------------------------------

    def _raise_saudi_compliance_error(self, en_message, ar_message):
        """Raise a bilingual ValidationError for Saudi statutory compliance failures."""
        raise ValidationError(_(
            "%(english)s\n\n%(arabic)s",
            english=en_message,
            arabic=ar_message,
        ))

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
            current_transaction_id=self.id,
        ).search([
            ('partner_id', '=', self.partner_id.id),
            ('holder_id', '=', buyer.id),
            ('securities_type', '=', 'shares'),
            ('securities', '>', 0),
        ], limit=1)
        return not existing_entry

    def _check_saudi_rofr_window(self):
        """Rule 1: enforce ROFR waiting period for external share transfers."""
        for transaction in self:
            if not transaction._has_external_buyer():
                continue
            if transaction.rofr_waiver_approved:
                continue

            company = transaction._get_governance_company()
            rofr_days = company.equity_rofr_window_days or 15

            if not transaction.rofr_notice_date:
                transaction._raise_saudi_compliance_error(
                    _(
                        "Right of First Refusal (ROFR) compliance failure: an external buyer is "
                        "involved but no share block notice date has been logged. Log the ROFR "
                        "notice date and wait %(days)s days before initiating signatures.",
                        days=rofr_days,
                    ),
                    _(
                        "مخالفة لحق الأولوية في الشراء (ROFR): يوجد مشترٍ خارجي ولكن لم "
                        "يتم تسجيل تاريخ إشعار عرض الحصص. يرجى تسجيل تاريخ الإشعار "
                        "والانتظار %(days)s يوماً قبل بدء التوقيعات.",
                        days=rofr_days,
                    ),
                )

            rofr_deadline = transaction.rofr_notice_date + timedelta(days=rofr_days)
            if fields.Date.context_today(transaction) < rofr_deadline:
                transaction._raise_saudi_compliance_error(
                    _(
                        "Right of First Refusal (ROFR) compliance failure: the mandatory "
                        "%(days)s-day window has not elapsed since the share block notice "
                        "dated %(notice_date)s. Earliest eligible date: %(deadline)s.",
                        days=rofr_days,
                        notice_date=transaction.rofr_notice_date,
                        deadline=rofr_deadline,
                    ),
                    _(
                        "مخالفة لحق الأولوية في الشراء (ROFR): لم تنتهِ مدة "
                        "%(days)s يوماً الإلزامية منذ إشعار عرض الحصص بتاريخ "
                        "%(notice_date)s. أقرب تاريخ مسموح: %(deadline)s.",
                        days=rofr_days,
                        notice_date=transaction.rofr_notice_date,
                        deadline=rofr_deadline,
                    ),
                )

    def _get_simulated_share_holdings(self):
        """Return {holder_id: {security_class_id: shares}} after executing this transaction."""
        self.ensure_one()
        holdings = defaultdict(lambda: defaultdict(float))

        cap_entries = self.env['equity.cap.table'].with_context(
            current_transaction_id=self.id,
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
            projected_count = transaction._count_active_shareholders(
                transaction._get_simulated_share_holdings()
            )

            if projected_count < min_shareholders:
                transaction._raise_saudi_compliance_error(
                    _(
                        "Saudi Closed Joint Stock Company (CJSC) compliance failure: executing "
                        "this transaction would reduce the shareholder count to %(projected)s, "
                        "below the MOCI statutory minimum of %(minimum)s shareholders for a "
                        "شركة مساهمة مقفلة.",
                        projected=projected_count,
                        minimum=min_shareholders,
                    ),
                    _(
                        "مخالفة نظام الشركات السعودي للشركة المساهمة المقفلة: تنفيذ "
                        "هذه العملية سيخفض عدد المساهمين إلى %(projected)s، "
                        "أقل من الحد الأدنى النظامي البالغ %(minimum)s مساهماً "
                        "وفقاً لمتطلبات وزارة التجارة.",
                        projected=projected_count,
                        minimum=min_shareholders,
                    ),
                )

    def _check_governance_concentration_limits(self):
        """Rule 3: ownership and multi-class voting concentration under company bylaws."""
        SecurityClass = self.env['equity.security.class']
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
            votes_per_share = {
                sc.id: sc.share_votes or 0 for sc in security_classes
            }

            for security_class in security_classes:
                if security_class.share_votes > max_votes_per_share:
                    transaction._raise_saudi_compliance_error(
                        _(
                            "Internal governance policy violation: security class "
                            "\"%(class_name)s\" grants %(votes)s votes per share, exceeding "
                            "the bylaws limit of %(limit)s vote(s) per share.",
                            class_name=security_class.display_name,
                            votes=security_class.share_votes,
                            limit=max_votes_per_share,
                        ),
                        _(
                            "مخالفة لسياسة حوكمة الشركة: فئة الأسهم \"%(class_name)s\" "
                            "تمنح %(votes)s أصوات لكل سهم، وهذا يتجاوز "
                            "حد النظام الأساسي البالغ %(limit)s صوت لكل سهم.",
                            class_name=security_class.display_name,
                            votes=security_class.share_votes,
                            limit=max_votes_per_share,
                        ),
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

            Partner = self.env['res.partner']
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
                    transaction._raise_saudi_compliance_error(
                        _(
                            "Internal governance policy violation: shareholder "
                            "\"%(holder)s\" would hold %(pct).2f%% of issued share capital, "
                            "exceeding the maximum permitted concentration of %(limit).2f%%.",
                            holder=holder_name,
                            pct=ownership_pct,
                            limit=max_ownership,
                        ),
                        _(
                            "مخالفة لسياسة حوكمة الشركة: المساهم \"%(holder)s\" "
                            "سيملك %(pct).2f%% من رأس المال المصدر، "
                            "وهذا يتجاوز حد التركز المسموح البالغ %(limit).2f%%.",
                            holder=holder_name,
                            pct=ownership_pct,
                            limit=max_ownership,
                        ),
                    )

                if float_compare(voting_pct, max_voting, precision_digits=2) > 0:
                    transaction._raise_saudi_compliance_error(
                        _(
                            "Internal governance policy violation: shareholder "
                            "\"%(holder)s\" would control %(pct).2f%% of aggregate voting power "
                            "after multi-class weighting, exceeding the bylaws limit of "
                            "%(limit).2f%%.",
                            holder=holder_name,
                            pct=voting_pct,
                            limit=max_voting,
                        ),
                        _(
                            "مخالفة لسياسة حوكمة الشركة: المساهم \"%(holder)s\" "
                            "سيتحكم في %(pct).2f%% من إجمالي قوة التصويت "
                            "بعد أوزان فئات الأسهم، وهذا يتجاوز "
                            "حد النظام الأساسي البالغ %(limit).2f%%.",
                            holder=holder_name,
                            pct=voting_pct,
                            limit=max_voting,
                        ),
                    )

    def check_saudi_statutory_bounds(self):
        """Validate Saudi MOCI CJSC statutory and internal governance boundaries."""
        for transaction in self:
            transaction._check_saudi_rofr_window()
            transaction._check_saudi_minimum_shareholder_count()
            transaction._check_governance_concentration_limits()
        return True

    def _get_sign_template(self):
        """Return the Al Tahtheeb Share Transfer Agreement Sign template."""
        sign_template = self.env['sign.template'].search(
            [('name', '=', SIGN_TEMPLATE_NAME)],
            limit=1,
        )
        if not sign_template:
            raise UserError(_(
                "The Sign template \"%(template)s\" was not found. "
                "Please create it in the Sign application before initiating the legal flow.",
                template=SIGN_TEMPLATE_NAME,
            ))
        return sign_template

    def _get_template_signer_roles(self, sign_template):
        """Return exactly two ordered signer roles from the Sign template."""
        roles = sign_template.sign_item_ids.responsible_id.sorted()
        if len(roles) != 2:
            raise UserError(_(
                "The Sign template \"%(template)s\" must define exactly two signer roles "
                "(shareholder and corporate authority). Found %(count)s role(s).",
                template=sign_template.name,
                count=len(roles),
            ))
        return roles

    def _get_legal_flow_shareholder(self):
        """Return the shareholder partner who must sign first."""
        self.ensure_one()
        shareholder = self.partner_id
        if not shareholder:
            raise UserError(_(
                "Cannot initiate the legal flow: no shareholder is set on this transaction."
            ))
        return shareholder

    def _get_corporate_authority_partner(self):
        """Return Al Tahtheeb's corporate authority representative partner."""
        self.ensure_one()
        corporate_partner = self.company_id.partner_id
        if not corporate_partner:
            raise UserError(_(
                "Cannot initiate the legal flow: the Odoo company has no linked partner."
            ))
        return corporate_partner

    def _validate_signer_partner(self, partner, role_label):
        """Ensure a signer partner is eligible for the Sign request."""
        if not partner.email:
            raise UserError(_(
                "%(role)s \"%(partner)s\" must have an email address before "
                "the signature request can be sent.",
                role=role_label,
                partner=partner.display_name,
            ))

    def action_initiate_legal_flow(self):
        """Create a sequential Sign request and move the transaction to waiting_signature."""
        self.ensure_one()
        self.check_saudi_statutory_bounds()

        if self.state != LEGAL_FLOW_READY_STATE:
            raise UserError(_(
                "The legal signature flow can only be started when the transaction "
                "is confirmed and ready for processing."
            ))
        if self.sign_request_id:
            raise UserError(_(
                "A signature request is already linked to this transaction."
            ))

        sign_template = self._get_sign_template()
        roles = self._get_template_signer_roles(sign_template)
        shareholder = self._get_legal_flow_shareholder()
        corporate_partner = self._get_corporate_authority_partner()

        self._validate_signer_partner(shareholder, _('Shareholder'))
        self._validate_signer_partner(corporate_partner, _('Corporate Authority Representative'))

        reference = _('Share Transfer - %s', self.display_name)
        subject = _('Signature Request - %(document)s', document=reference)

        sign_request_vals = {
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
        }

        sign_request = self.env['sign.request'].create(sign_request_vals)

        self.write({
            'sign_request_id': sign_request.id,
            'state': 'waiting_signature',
        })

        _logger.info(
            "Legal signature flow initiated for equity.transaction %s; "
            "sign.request %s created for shareholders %s and %s.",
            self.id,
            sign_request.id,
            shareholder.display_name,
            corporate_partner.display_name,
        )

        self.message_post(
            body=_(
                "Legal signature request initiated. "
                "The shareholder will sign first, followed by the corporate authority. "
                "Track progress here: %s",
                sign_request._get_html_link(),
            ),
        )

        return {
            'type': 'ir.actions.act_window',
            'name': _('Signature Request'),
            'res_model': 'sign.request',
            'res_id': sign_request.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _finalize_after_legal_signature(self):
        """Lock legally signed transactions into the native equity cap table ledger."""
        waiting_transactions = self.filtered(lambda t: t.state == 'waiting_signature')
        if not waiting_transactions:
            return waiting_transactions

        waiting_transactions.write({'state': 'signed_legal'})
        waiting_transactions.flush_model(['state'])

        # Native equity refreshes the SQL-view cap table whenever transactions change.
        self.env['equity.cap.table'].invalidate_model()

        message = _(LEGAL_SIGNATURE_CONFIRMED_MESSAGE)
        for transaction in waiting_transactions:
            transaction.message_post(body=message)

        _logger.info(
            "Equity transactions %s finalized to signed_legal; cap table cache invalidated.",
            waiting_transactions.ids,
        )
        return waiting_transactions
