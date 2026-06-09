# -*- coding: utf-8 -*-
"""
equity_marketplace_board.py
===========================
Models the listing (buy or sell offer) that shareholders post on the Al
Tahtheeb internal equity marketplace.

Lifecycle
---------
``draft`` → ``published`` → ``matched`` → ``approved`` → ``sign_process``
→ ``done``  (happy path)

``sign_process`` → ``published``  (stale-signature expiration path, driven
by the nightly cron in ``equity.transaction``)

Any state → ``cancelled``  (administrative void)

Key integration points
----------------------
* When a buy/sell pair is matched the listing spawns an ``equity.transaction``
  via ``_create_equity_transaction_from_match``.
* When the transaction's legal flow starts (``action_initiate_legal_flow``)
  the listing moves to ``sign_process``.
* If the sign envelope remains unsigned beyond the configured timeout the
  cron calls ``_release_after_stale_signature``, reverting the listing to
  ``published`` and clearing the counterparty so new bids are accepted.
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from . import tools

_logger = logging.getLogger(__name__)

# Number of days a newly published listing remains in the ROFR window.
ROFR_PUBLISH_DAYS = 15


class EquityMarketplaceBoard(models.Model):
    _name = 'equity.marketplace.board'
    _description = 'Equity Marketplace Listing'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'name'

    # -------------------------------------------------------------------------
    # Fields
    # -------------------------------------------------------------------------

    @api.model
    def _default_company(self):
        return self.env.company

    name = fields.Char(
        string="Listing ID",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
        index='btree',
        tracking=True,
    )
    listing_type = fields.Selection(
        selection=[
            ('sell', 'Sell Offer'),
            ('buy', 'Buying Request'),
        ],
        string="Listing Type",
        required=True,
        tracking=True,
    )
    shareholder_id = fields.Many2one(
        comodel_name='res.partner',
        string="Shareholder",
        required=True,
        tracking=True,
        index='btree',
        help="Partner creating this marketplace listing.",
    )
    share_class_id = fields.Many2one(
        comodel_name='equity.security.class',
        string="Share Class",
        required=True,
        domain=[('class_type', '=', 'shares')],
        tracking=True,
        help="Native Odoo 19 equity security class offered or requested.",
    )
    qty = fields.Integer(
        string="Quantity",
        required=True,
        tracking=True,
    )
    price_unit = fields.Float(
        string="Price per Share",
        required=True,
        tracking=True,
    )
    price_total = fields.Float(
        string="Total Price",
        compute='_compute_price_total',
        store=True,
        tracking=True,
    )
    matched_partner_id = fields.Many2one(
        comodel_name='res.partner',
        string="Matched Counterparty",
        copy=False,
        tracking=True,
        help="Partner who accepted this listing (buyer or seller).",
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('published', 'Published'),
            ('matched', 'Matched'),
            ('approved', 'Approved'),
            ('sign_process', 'Signature In Progress'),
            ('done', 'Done'),
            ('cancelled', 'Cancelled'),
        ],
        string="Status",
        default='draft',
        required=True,
        tracking=True,
        copy=False,
        index=True,
        help=(
            "Tracks the listing through its lifecycle.  "
            "The 'sign_process' state means the linked equity.transaction has "
            "a live Sign envelope; the listing is locked until signing completes "
            "or the nightly stale-signature cron releases it back to 'published'."
        ),
    )
    rofr_deadline = fields.Date(
        string="ROFR Deadline",
        copy=False,
        tracking=True,
        help="End date of the Right of First Refusal window for existing shareholders.",
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        string="Company",
        default=_default_company,
        required=True,
        index=True,
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        string="Currency",
        related='shareholder_id.equity_currency_id',
        store=True,
        readonly=True,
    )
    equity_transaction_id = fields.Many2one(
        comodel_name='equity.transaction',
        string="Equity Transaction",
        copy=False,
        readonly=True,
        tracking=True,
        help="The equity.transaction spawned when this listing was matched.",
    )

    _name_unique = models.Constraint(
        'UNIQUE(name)',
        _('The listing ID must be unique.'),
    )

    # -------------------------------------------------------------------------
    # Computed fields
    # -------------------------------------------------------------------------

    @api.depends('qty', 'price_unit')
    def _compute_price_total(self):
        for listing in self:
            listing.price_total = listing.qty * listing.price_unit

    # -------------------------------------------------------------------------
    # Constraints
    # -------------------------------------------------------------------------

    @api.constrains('qty', 'price_unit')
    def _check_positive_values(self):
        for listing in self:
            if listing.qty <= 0:
                tools.raise_bilingual_validation(
                    "Share quantity must be strictly positive.",
                    "يجب أن تكون كمية الأسهم أكبر من صفر.",
                )
            if listing.price_unit < 0:
                tools.raise_bilingual_validation(
                    "Price per share cannot be negative.",
                    "لا يمكن أن يكون سعر السهم سالباً.",
                )

    @api.constrains('shareholder_id', 'matched_partner_id')
    def _check_distinct_counterparties(self):
        for listing in self.filtered('matched_partner_id'):
            if listing.shareholder_id == listing.matched_partner_id:
                tools.raise_bilingual_validation(
                    "The matched counterparty must be different from the listing shareholder.",
                    "يجب أن يكون الطرف المقابل مختلفاً عن المساهم صاحب الإعلان.",
                )

    @api.constrains('state', 'matched_partner_id', 'equity_transaction_id')
    def _check_state_consistency(self):
        """
        Enforce field coherence across state transitions.

        * States beyond ``published`` require a matched counterparty.
        * An ``equity_transaction_id`` link is only valid once the listing
          has been matched.

        Note: ``sign_process`` is intentionally included in the matched-partner
        requirement because the listing is still locked during signature.
        The stale-signature cron clears ``matched_partner_id`` as part of the
        revert, so this constraint is never violated during expiration.
        """
        for listing in self:
            if (
                listing.state in ('matched', 'approved', 'sign_process', 'done')
                and not listing.matched_partner_id
            ):
                raise ValidationError(_(
                    "A matched counterparty is required once the listing leaves "
                    "the published state."
                ))
            if (
                listing.equity_transaction_id
                and listing.state not in ('matched', 'approved', 'sign_process', 'done')
            ):
                raise ValidationError(_(
                    "An equity transaction can only be linked after the listing "
                    "has been matched."
                ))

    # -------------------------------------------------------------------------
    # ORM overrides
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == _('New'):
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('equity.marketplace.board')
                    or _('New')
                )
        listings = super().create(vals_list)
        _logger.info(
            "Created equity marketplace listing(s): %s",
            ', '.join(listings.mapped('name')),
        )
        return listings

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _selection_label(self, field_name):
        """Return the human-readable label for a Selection field value."""
        self.ensure_one()
        return dict(
            self._fields[field_name]._description_selection(self.env)
        ).get(getattr(self, field_name), getattr(self, field_name))

    # -------------------------------------------------------------------------
    # State-transition actions
    # -------------------------------------------------------------------------

    def action_publish(self):
        """Transition a draft listing to published and open the ROFR window."""
        for listing in self:
            if listing.state != 'draft':
                tools.raise_bilingual_user_error(
                    "Only draft listings can be published. "
                    "Listing %(listing)s is currently %(state)s.",
                    "يمكن نشر الإعلانات في حالة المسودة فقط. "
                    "الإعلان %(listing)s في حالة %(state)s.",
                    listing=listing.name,
                    state=listing._selection_label('state'),
                )
            rofr_deadline = fields.Date.context_today(listing) + timedelta(days=ROFR_PUBLISH_DAYS)
            listing.write({
                'state': 'published',
                'rofr_deadline': rofr_deadline,
            })
            listing.message_post(body=_(
                "%(english)s\n\n%(arabic)s",
                english=_(
                    "Listing published on the Al Tahtheeb equity marketplace. "
                    "ROFR window ends on %(deadline)s.",
                    deadline=rofr_deadline,
                ),
                arabic=_(
                    "تم نشر الإعلان في سوق التداول الداخلي لشركة التحذيب. "
                    "تنتهي فترة حق الأولوية في الشراء في %(deadline)s.",
                    deadline=rofr_deadline,
                ),
            ))
        return True

    @api.model
    def get_published_listings(self, limit=None, order='rofr_deadline asc, create_date desc'):
        """Return published listings using a clean domain (portal-safe API)."""
        domain = [('state', '=', 'published')]
        return self.search(domain, limit=limit, order=order)

    def action_match_listing(self, buyer_or_seller_id):
        """Link a counterparty and move the listing to the matched state."""
        counterparty = self.env['res.partner'].browse(buyer_or_seller_id).exists()
        if not counterparty:
            tools.raise_bilingual_user_error(
                "The selected counterparty does not exist.",
                "الطرف المقابل المحدد غير موجود.",
            )

        invalid_state = self.filtered(lambda l: l.state != 'published')
        if invalid_state:
            listing = invalid_state[0]
            tools.raise_bilingual_user_error(
                "Only published listings can be matched. "
                "Listing %(listing)s is currently %(state)s.",
                "يمكن مطابقة الإعلانات المنشورة فقط. "
                "الإعلان %(listing)s في حالة %(state)s.",
                listing=listing.name,
                state=listing._selection_label('state'),
            )
        if self.filtered(lambda l: l.shareholder_id == counterparty):
            tools.raise_bilingual_user_error(
                "The counterparty must be different from the listing shareholder.",
                "يجب أن يكون الطرف المقابل مختلفاً عن المساهم صاحب الإعلان.",
            )

        self.write({
            'matched_partner_id': counterparty.id,
            'state': 'matched',
        })
        for listing in self:
            listing.message_post(body=_(
                "%(english)s\n\n%(arabic)s",
                english=_(
                    "Listing matched with counterparty %(partner)s.",
                    partner=counterparty.display_name,
                ),
                arabic=_(
                    "تمت مطابقة الإعلان مع الطرف المقابل %(partner)s.",
                    partner=counterparty.display_name,
                ),
            ))
        return True

    def _get_equity_company_partner(self):
        """Resolve the cap-table company partner linked to this listing."""
        self.ensure_one()
        # equity.cap.table is a readonly SQL view; sudo is required for stable lookup.
        CapTable = self.env['equity.cap.table'].sudo()
        domain = [
            ('security_class_id', '=', self.share_class_id.id),
            ('securities_type', '=', 'shares'),
        ]
        if self.listing_type == 'sell':
            domain.append(('holder_id', '=', self.shareholder_id.id))
        entry = CapTable.search(domain, limit=1)
        if entry:
            return entry.partner_id

        company_partner = self.company_id.partner_id
        if company_partner.is_company:
            return company_partner

        tools.raise_bilingual_user_error(
            "Unable to determine the equity company partner for listing %(listing)s.",
            "تعذر تحديد شريك شركة الأسهم للإعلان %(listing)s.",
            listing=self.name,
        )

    def _create_equity_transaction_from_match(self):
        """Create a draft equity.transaction from a matched marketplace listing."""
        self.ensure_one()
        if self.state != 'matched' or not self.matched_partner_id:
            tools.raise_bilingual_user_error(
                "An equity transaction can only be created from a matched listing.",
                "يمكن إنشاء معاملة الأسهم فقط من إعلان تمت مطابقته.",
            )
        if self.equity_transaction_id:
            return self.equity_transaction_id

        if self.listing_type == 'sell':
            seller = self.shareholder_id
            buyer = self.matched_partner_id
        else:
            seller = self.matched_partner_id
            buyer = self.shareholder_id

        equity_company = self._get_equity_company_partner()
        transaction_env = self.env['equity.transaction']
        if self.env.user.has_group('base.group_portal'):
            # Portal users have no direct create rights on equity.transaction.
            transaction_env = transaction_env.sudo()

        if self.rofr_deadline:
            notice_date = self.rofr_deadline - timedelta(days=ROFR_PUBLISH_DAYS)
        elif self.create_date:
            notice_date = fields.Date.to_date(self.create_date)
        else:
            notice_date = fields.Date.context_today(self)
        transaction = transaction_env.create({
            'partner_id': equity_company.id,
            'transaction_type': 'transfer',
            'seller_id': seller.id,
            'subscriber_id': buyer.id,
            'security_class_id': self.share_class_id.id,
            'securities': float(self.qty),
            'security_price': self.price_unit,
            'company_id': self.company_id.id,
            'state': 'draft',
            'marketplace_listing_id': self.id,
            'rofr_notice_date': notice_date,
        })
        self.write({'equity_transaction_id': transaction.id})
        self.message_post(body=_(
            "%(english)s\n\n%(arabic)s",
            english=_(
                "Equity transfer transaction %(transaction)s created from marketplace listing.",
                transaction=transaction.display_name,
            ),
            arabic=_(
                "تم إنشاء معاملة نقل أسهم %(transaction)s من إعلان السوق.",
                transaction=transaction.display_name,
            ),
        ))
        transaction.message_post(body=_(
            "%(english)s\n\n%(arabic)s",
            english=_(
                "Created from marketplace listing %(listing)s.",
                listing=self.name,
            ),
            arabic=_(
                "تم الإنشاء من إعلان السوق %(listing)s.",
                listing=self.name,
            ),
        ))
        _logger.info(
            "Marketplace listing %s matched; equity.transaction %s created.",
            self.name,
            transaction.id,
        )
        return transaction

    # -------------------------------------------------------------------------
    # Portal match flow
    # -------------------------------------------------------------------------

    def _check_portal_match_allowed(self, counterparty):
        """Validate portal-side business rules before elevated writes."""
        self.ensure_one()
        self.check_access('read')

        if self.state != 'published':
            tools.raise_bilingual_access_error(
                "This marketplace listing is no longer available.",
                "لم يعد إعلان السوق هذا متاحاً.",
            )
        if self.shareholder_id == counterparty:
            tools.raise_bilingual_access_error(
                "You cannot match your own marketplace listing.",
                "لا يمكنك مطابقة إعلانك الخاص في السوق.",
            )
        if self.rofr_deadline and fields.Date.context_today(self) < self.rofr_deadline:
            tools.raise_bilingual_user_error(
                "The Right of First Refusal (ROFR) window is still open for this listing.",
                "لا تزال فترة حق الأولوية في الشراء (ROFR) سارية لهذا الإعلان.",
            )
        if self.listing_type != 'sell':
            tools.raise_bilingual_user_error(
                "Only sell offers can receive buy proposals from the portal.",
                "يمكن تقديم عروض الشراء من البوابة على إعلانات البيع فقط.",
            )

    def action_approve(self):
        """Governance approval after a listing has been matched."""
        for listing in self:
            if listing.state != 'matched':
                tools.raise_bilingual_user_error(
                    "Only matched listings can be approved. "
                    "Listing %(listing)s is currently %(state)s.",
                    "يمكن اعتماد الإعلانات المطابقة فقط. "
                    "الإعلان %(listing)s في حالة %(state)s.",
                    listing=listing.name,
                    state=listing._selection_label('state'),
                )
            listing.write({'state': 'approved'})
            listing.message_post(body=_(
                "%(english)s\n\n%(arabic)s",
                english=_("Marketplace listing approved by governance."),
                arabic=_("تم اعتماد إعلان السوق من قبل الحوكمة."),
            ))
        return True

    def action_match_selected_partner(self):
        """Match the listing with the counterparty selected on the form."""
        for listing in self:
            if not listing.matched_partner_id:
                tools.raise_bilingual_user_error(
                    "Select a counterparty before matching listing %(listing)s.",
                    "يرجى تحديد الطرف المقابل قبل مطابقة الإعلان %(listing)s.",
                    listing=listing.name,
                )
            listing.action_match_listing(listing.matched_partner_id.id)
        return True

    def action_create_equity_transaction(self):
        """Create the equity.transaction for an approved or matched listing."""
        transactions = self.env['equity.transaction']
        for listing in self:
            if listing.state not in ('matched', 'approved'):
                tools.raise_bilingual_user_error(
                    "An equity transaction can only be created from a matched or "
                    "approved listing. Listing %(listing)s is currently %(state)s.",
                    "يمكن إنشاء معاملة الأسهم فقط من إعلان مطابق أو معتمد. "
                    "الإعلان %(listing)s في حالة %(state)s.",
                    listing=listing.name,
                    state=listing._selection_label('state'),
                )
            transactions |= listing._create_equity_transaction_from_match()
        if len(transactions) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Equity Transaction'),
                'res_model': 'equity.transaction',
                'res_id': transactions.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return True

    def action_open_equity_transaction(self):
        self.ensure_one()
        if not self.equity_transaction_id:
            tools.raise_bilingual_user_error(
                "No equity transaction is linked to listing %(listing)s.",
                "لا توجد معاملة أسهم مرتبطة بالإعلان %(listing)s.",
                listing=self.name,
            )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Equity Transaction'),
            'res_model': 'equity.transaction',
            'res_id': self.equity_transaction_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_portal_match_and_create_transaction(self, counterparty_id):
        """Match a listing with a portal counterparty and spawn the equity transaction."""
        self.ensure_one()
        if self.env.user.has_group('base.group_portal'):
            counterparty_id = self.env.user.partner_id.id

        counterparty = self.env['res.partner'].browse(counterparty_id).exists()
        if not counterparty:
            tools.raise_bilingual_user_error(
                "The selected counterparty does not exist.",
                "الطرف المقابل المحدد غير موجود.",
            )

        self._check_portal_match_allowed(counterparty)
        # Portal users have read-only ACL on listings; elevate narrowly after validation.
        listing = self.sudo() if self.env.user.has_group('base.group_portal') else self
        with self.env.cr.savepoint():
            listing.action_match_listing(counterparty.id)
            return listing._create_equity_transaction_from_match()

    # =========================================================================
    # Stale signature release (called by equity.transaction cron)
    # =========================================================================

    def _release_after_stale_signature(self, stale_days, transaction=None):
        """
        Return a locked listing to the marketplace after a stale Sign request
        expires automatically via the nightly cron.

        This method is called by ``equity.transaction._expire_stale_signature_request``
        and should **not** be called manually.

        Steps performed:
        1. Revert ``state`` from ``sign_process`` (or ``matched`` / ``approved``)
           back to ``published`` so other partners can see and bid on the listing.
        2. Clear ``matched_partner_id`` — the previous counterparty's reservation
           is released.
        3. Clear ``equity_transaction_id`` — the cancelled transaction link is
           removed to avoid stale FK references.
        4. Post a bilingual chatter note explaining the automatic release.

        :param int stale_days:    Timeout that triggered the expiry.
        :param transaction:       The ``equity.transaction`` being expired
                                  (used for display in the chatter message).
        :returns: ``True`` if the listing was released, ``False`` otherwise.
        """
        self.ensure_one()
        # Guard: only release listings that are actually locked.
        if self.state not in ('matched', 'approved', 'sign_process'):
            _logger.warning(
                "equity.marketplace.board %s is in state '%s'; "
                "expected one of matched/approved/sign_process. Skipping release.",
                self.name,
                self.state,
            )
            return False

        # Write all three fields atomically so _check_state_consistency sees
        # the final consistent state (published + no partner + no transaction).
        self.write({
            'state': 'published',
            'matched_partner_id': False,
            'equity_transaction_id': False,
        })

        self.message_post(body=_(
            "%(english)s\n\n%(arabic)s",
            english=_(
                "⚠️ Marketplace listing %(listing)s has been automatically released back "
                "to published status because the linked signature request on transaction "
                "%(transaction)s expired after %(days)s day(s) without completion. "
                "The listing is now open for new bids.",
                listing=self.name,
                transaction=transaction.display_name if transaction else _('N/A'),
                days=stale_days,
            ),
            arabic=_(
                "⚠️ أُعيد إعلان السوق %(listing)s تلقائياً إلى حالة المنشور لانتهاء "
                "طلب التوقيع المرتبط بالمعاملة %(transaction)s بعد %(days)s يوماً دون إتمام. "
                "الإعلان متاح الآن لتلقي عروض جديدة.",
                listing=self.name,
                transaction=transaction.display_name if transaction else _('N/A'),
                days=stale_days,
            ),
        ))
        _logger.info(
            "equity.marketplace.board %s released back to 'published' "
            "after stale signature expiry on equity.transaction %s.",
            self.name,
            transaction.id if transaction else 'N/A',
        )
        return True

    def _schedule_stale_signature_admin_activity(self, summary):
        """
        Create a warning activity for every equity trading manager on this
        listing so the team can review the automatic stale-signature release.

        Falls back to ``base.user_root`` (OdooBot) if the security group is
        not found, ensuring the activity is never silently dropped.
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

    # =========================================================================
    # Backend dashboard data
    # =========================================================================

    @api.model
    def get_filter_options(self):
        """Return selectable filter options for the dashboard UI."""
        # Companies the current user can access
        allowed = self.env.user.company_ids or self.env.companies
        companies = [{'id': c.id, 'name': c.name} for c in allowed.sorted('name')]
        return {
            'companies': companies,
            'listing_types': [
                {'value': 'all',  'label': 'All Types',  'label_ar': 'الكل'},
                {'value': 'sell', 'label': 'Sell',       'label_ar': 'بيع'},
                {'value': 'buy',  'label': 'Buy',        'label_ar': 'شراء'},
            ],
            'states': [
                {'value': 'all',        'label': 'All States',  'label_ar': 'كل الحالات'},
                {'value': 'active',     'label': 'Active',      'label_ar': 'نشط'},
                {'value': 'in_progress','label': 'In Progress', 'label_ar': 'قيد التنفيذ'},
                {'value': 'done',       'label': 'Done',        'label_ar': 'منجز'},
                {'value': 'cancelled',  'label': 'Cancelled',   'label_ar': 'ملغي'},
            ],
            'periods': [
                {'value': 'all',     'label': 'All Time',      'label_ar': 'كل الفترات'},
                {'value': 'today',   'label': 'Today',         'label_ar': 'اليوم'},
                {'value': 'week',    'label': 'This Week',     'label_ar': 'هذا الأسبوع'},
                {'value': 'month',   'label': 'This Month',    'label_ar': 'هذا الشهر'},
                {'value': 'quarter', 'label': 'This Quarter',  'label_ar': 'هذا الربع'},
                {'value': 'year',    'label': 'This Year',     'label_ar': 'هذا العام'},
            ],
        }

    @api.model
    def get_dashboard_data(self, filters=None):
        """
        Return a structured dict consumed by the OWL EquityTradingDashboard
        client action.  Accepts an optional ``filters`` dict:

        ``company_id``    – int or False (defaults to env.company)
        ``listing_type``  – 'all' | 'sell' | 'buy'
        ``state_filter``  – 'all' | 'active' | 'in_progress' | 'done' | 'cancelled'
        ``period``        – 'all' | 'today' | 'week' | 'month' | 'quarter' | 'year'
        """
        from datetime import date, timedelta

        filters = filters or {}

        # ── Resolve company ──────────────────────────────────────────────────
        company_id = filters.get('company_id') or self.env.company.id
        company = self.env['res.company'].browse(company_id)
        base_domain = [('company_id', '=', company.id)]

        # ── Listing type filter ──────────────────────────────────────────────
        listing_type = filters.get('listing_type', 'all')
        if listing_type in ('sell', 'buy'):
            base_domain += [('listing_type', '=', listing_type)]

        # ── State group filter ───────────────────────────────────────────────
        STATE_GROUPS = {
            'active':      ['published'],
            'in_progress': ['matched', 'approved', 'sign_process'],
            'done':        ['done'],
            'cancelled':   ['cancelled'],
        }
        state_filter = filters.get('state_filter', 'all')
        if state_filter != 'all' and state_filter in STATE_GROUPS:
            base_domain += [('state', 'in', STATE_GROUPS[state_filter])]

        # ── Period filter ────────────────────────────────────────────────────
        today = date.today()
        period = filters.get('period', 'all')
        period_domain = []
        if period == 'today':
            period_domain = [('create_date', '>=', str(today) + ' 00:00:00')]
        elif period == 'week':
            week_start = today - timedelta(days=today.weekday())
            period_domain = [('create_date', '>=', str(week_start) + ' 00:00:00')]
        elif period == 'month':
            month_start = today.replace(day=1)
            period_domain = [('create_date', '>=', str(month_start) + ' 00:00:00')]
        elif period == 'quarter':
            q_month = ((today.month - 1) // 3) * 3 + 1
            quarter_start = today.replace(month=q_month, day=1)
            period_domain = [('create_date', '>=', str(quarter_start) + ' 00:00:00')]
        elif period == 'year':
            year_start = today.replace(month=1, day=1)
            period_domain = [('create_date', '>=', str(year_start) + ' 00:00:00')]

        listing_domain = base_domain + period_domain
        month_start = today.replace(day=1)

        # ── KPI cards ────────────────────────────────────────────────────────
        active_listings = self.search_count(
            listing_domain + [('state', '=', 'published')]
            if state_filter == 'all' else listing_domain
        )

        pending_signatures = self.env['equity.transaction'].search_count([
            ('state', '=', 'waiting_signature'),
            ('company_id', '=', company.id),
        ])

        portfolio_assets = self.env['equity.portfolio.asset'].search([
            ('company_id', '=', company.id),
            ('active', '=', True),
        ])
        portfolio_value = sum(portfolio_assets.mapped('current_market_value'))

        completed_month = self.env['equity.transaction'].search_count([
            ('state', '=', 'done'),
            ('company_id', '=', company.id),
            ('date', '>=', fields.Date.to_string(month_start)),
        ])

        # ── Listing state breakdown ──────────────────────────────────────────
        all_states = [
            'draft', 'published', 'matched', 'approved',
            'sign_process', 'done', 'cancelled',
        ]
        listing_states = []
        for state in all_states:
            count = self.search_count(
                listing_domain + [('state', '=', state)]
                if state_filter == 'all' else
                listing_domain
            )
            listing_states.append({'state': state, 'count': count})

        # ── Weekly new-listing activity (last 8 weeks) ───────────────────────
        weekly_activity = []
        for weeks_ago in range(7, -1, -1):
            week_start = today - timedelta(weeks=weeks_ago, days=today.weekday())
            week_end = week_start + timedelta(days=6)
            count = self.search_count(base_domain + [
                ('create_date', '>=', str(week_start) + ' 00:00:00'),
                ('create_date', '<=', str(week_end) + ' 23:59:59'),
            ])
            iso_week = week_start.isocalendar()[1]
            weekly_activity.append({'label': f"W{iso_week}", 'count': count})

        # ── Recent listings ──────────────────────────────────────────────────
        recent_recs = self.search(listing_domain, limit=10, order='create_date desc')
        recent_listings = []
        for rec in recent_recs:
            recent_listings.append({
                'id': rec.id,
                'name': rec.name,
                'listing_type': rec.listing_type,
                'share_class': rec.share_class_id.name or '',
                'qty': rec.qty,
                'price_total': rec.price_total,
                'currency_symbol': rec.currency_id.symbol or company.currency_id.symbol or '',
                'state': rec.state,
                'shareholder': rec.shareholder_id.name or '',
            })

        # ── Pending action items ──────────────────────────────────────────────
        pending_domain = [
            ('company_id', '=', company.id),
            ('state', 'in', ['matched', 'approved']),
        ]
        if listing_type in ('sell', 'buy'):
            pending_domain += [('listing_type', '=', listing_type)]
        pending_actions = self.search(pending_domain, limit=5, order='create_date asc')
        pending_action_data = []
        for rec in pending_actions:
            pending_action_data.append({
                'id': rec.id,
                'name': rec.name,
                'state': rec.state,
                'listing_type': rec.listing_type,
                'action_label': 'Approve' if rec.state == 'matched' else 'Create Transaction',
                'shareholder': rec.shareholder_id.name or '',
            })

        return {
            'kpi': {
                'active_listings': active_listings,
                'pending_signatures': pending_signatures,
                'portfolio_value': portfolio_value,
                'portfolio_currency': company.currency_id.symbol or 'SAR',
                'completed_trades_month': completed_month,
            },
            'listing_states': listing_states,
            'weekly_activity': weekly_activity,
            'recent_listings': recent_listings,
            'pending_actions': pending_action_data,
            'company_name': company.name,
        }
