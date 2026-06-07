# -*- coding: utf-8 -*-

import logging
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare

from . import tools

_logger = logging.getLogger(__name__)

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
STALE_SIGNATURE_DAYS_PARAM = 'ics_altahtheeb_equity_trading.stale_signature_days'
DEFAULT_STALE_SIGNATURE_DAYS = 5


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

    @api.constrains('state', 'sign_request_id')
    def _check_sign_request_state(self):
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
            votes_per_share = {security_class.id: security_class.share_votes or 0 for security_class in security_classes}

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

        self.write({
            'sign_request_id': sign_request.id,
            'state': 'waiting_signature',
        })

        if self.marketplace_listing_id:
            self.marketplace_listing_id.write({'state': 'sign_process'})

        _logger.info(
            _("Legal signature flow initiated for equity.transaction %s; sign.request %s created."),
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

    def _finalize_after_legal_signature(self):
        """Lock legally signed transactions into the native equity cap table ledger."""
        waiting_transactions = self.filtered(lambda transaction: transaction.state == 'waiting_signature')
        if not waiting_transactions:
            return waiting_transactions

        waiting_transactions.write({'state': 'signed_legal'})
        waiting_transactions.flush_model(['state'])
        self.env['equity.cap.table'].invalidate_model()

        message = _(
            "%(english)s\n\n%(arabic)s",
            english=_(LEGAL_SIGNATURE_CONFIRMED_MESSAGE_EN),
            arabic=_(LEGAL_SIGNATURE_CONFIRMED_MESSAGE_AR),
        )
        for transaction in waiting_transactions:
            transaction.message_post(body=message)

        _logger.info(
            _("Equity transactions %s finalized to signed_legal; cap table cache invalidated."),
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

    # -------------------------------------------------------------------------
    # Stale signature expiration (nightly cron)
    # -------------------------------------------------------------------------

    @api.model
    def _get_stale_signature_days(self):
        """Read the stale signature timeout from System Parameters (default: 5 days)."""
        icp = self.env['ir.config_parameter'].sudo()
        raw_value = icp.get_param(STALE_SIGNATURE_DAYS_PARAM, str(DEFAULT_STALE_SIGNATURE_DAYS))
        try:
            days = int(raw_value)
        except (TypeError, ValueError):
            days = DEFAULT_STALE_SIGNATURE_DAYS
        return max(days, 1)

    @api.model
    def _cron_expire_stale_signature_requests(self):
        """Nightly job: void trades stuck in waiting_signature and reopen marketplace listings."""
        stale_days = self._get_stale_signature_days()
        cutoff = fields.Datetime.now() - timedelta(days=stale_days)

        stale_transactions = self.search([
            ('state', '=', 'waiting_signature'),
            ('sign_request_id', '!=', False),
            ('sign_request_id.state', '=', 'sent'),
            ('sign_request_id.create_date', '<=', cutoff),
        ])
        if not stale_transactions:
            _logger.info(
                _("Stale signature cron: no equity.transaction records exceeded %(days)s day(s)."),
                stale_days,
            )
            return True

        expired_count = 0
        for transaction in stale_transactions.with_context(ics_expiring_stale_signature=True):
            if transaction._expire_stale_signature_request(stale_days):
                expired_count += 1

        _logger.info(
            _("Stale signature cron voided %(count)s equity.transaction record(s) after %(days)s day(s)."),
            expired_count,
            stale_days,
        )
        return True

    def _get_trade_notification_partners(self):
        """Collect stakeholder partners who must be notified about a voided trade."""
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
            partners |= listing.shareholder_id | listing.matched_partner_id
        return partners.filtered('id')

    def _schedule_stale_signature_admin_activity(self, summary):
        """Create a backend todo for equity trading managers."""
        activity_type = self.env.ref('mail.mail_activity_data_warning', raise_if_not_found=False)
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
        """Notify stakeholders, administrators, and linked records about the automatic void."""
        self.ensure_one()
        parties = self._get_trade_notification_partners()
        void_message = _(
            "%(english)s\n\n%(arabic)s",
            english=_(
                "This equity trade was automatically voided because the linked Sign "
                "request remained incomplete for more than %(days)s day(s). "
                "The Sign envelope was cancelled and any linked marketplace listing "
                "was released back to published status so other partners may bid again.",
                days=stale_days,
            ),
            arabic=_(
                "تم إلغاء صفقة الأسهم هذه تلقائياً لأن طلب التوقيع المرتبط "
                "لم يكتمل لأكثر من %(days)s يوماً. تم إلغاء مظروف التوقيع "
                "وإعادة إعلان السوق إلى حالة المنشور ليتمكن الشركاء الآخرون من "
                "تقديم عروض جديدة.",
                days=stale_days,
            ),
        )

        self.message_post(body=void_message, partner_ids=parties.ids)
        self._schedule_stale_signature_admin_activity(_(
            "Stale signature void on %s",
            self.display_name,
        ))

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

        if listing:
            listing._schedule_stale_signature_admin_activity(_(
                "Marketplace listing %s released after stale signature timeout",
                listing.name,
            ))

    def _cancel_linked_sign_request(self):
        """Cancel the native Sign envelope linked to this transaction."""
        self.ensure_one()
        sign_request = self.sign_request_id
        if not sign_request or sign_request.state != 'sent':
            return False
        # Native Sign API entry point is sign.request.cancel().
        sign_request.cancel()
        return True

    def _expire_stale_signature_request(self, stale_days):
        """Void one stale trade, cancel Sign, and reopen the marketplace listing."""
        self.ensure_one()
        if self.state != 'waiting_signature' or not self.sign_request_id:
            return False

        sign_request = self.sign_request_id
        listing = self.marketplace_listing_id

        self._cancel_linked_sign_request()
        self.write({
            'state': 'cancelled',
            'sign_request_id': False,
        })
        self.env['equity.cap.table'].invalidate_model()

        if listing:
            listing._release_after_stale_signature(stale_days=stale_days, transaction=self)

        self._log_stale_signature_void(stale_days, sign_request, listing)
        return True

    def action_cancel_sign_request(self):
        """Manual wrapper around native sign.request.cancel() for UI actions."""
        self.ensure_one()
        if not self.sign_request_id:
            tools.raise_bilingual_user_error(
                "No signature request is linked to this transaction.",
                "لا يوجد طلب توقيع مرتبط بهذه المعاملة.",
            )
        self._cancel_linked_sign_request()
        self.write({
            'state': LEGAL_FLOW_READY_STATE,
            'sign_request_id': False,
        })
        if self.marketplace_listing_id:
            self.marketplace_listing_id.write({'state': 'approved'})
        return True
