# -*- coding: utf-8 -*-

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

ROFR_PUBLISH_DAYS = 15


class EquityMarketplaceBoard(models.Model):
    _name = 'equity.marketplace.board'
    _description = 'Equity Marketplace Listing'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'name'

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
        help="Native Odoo 19 equity security class (share class) offered or requested.",
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
        default=lambda self: self.env.company,
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
    )

    _name_unique = models.Constraint(
        'UNIQUE(name)',
        'The listing ID must be unique.',
    )

    @api.depends('qty', 'price_unit')
    def _compute_price_total(self):
        for listing in self:
            listing.price_total = listing.qty * listing.price_unit

    @api.constrains('qty', 'price_unit')
    def _check_positive_values(self):
        for listing in self:
            if listing.qty <= 0:
                raise ValidationError(_("Share quantity must be strictly positive."))
            if listing.price_unit < 0:
                raise ValidationError(_("Price per share cannot be negative."))

    @api.constrains('shareholder_id', 'matched_partner_id')
    def _check_distinct_counterparties(self):
        for listing in self.filtered('matched_partner_id'):
            if listing.shareholder_id == listing.matched_partner_id:
                raise ValidationError(_(
                    "The matched counterparty must be different from the listing shareholder."
                ))

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
            listings.mapped('name'),
        )
        return listings

    def action_publish(self):
        for listing in self:
            if listing.state != 'draft':
                raise UserError(_(
                    "Only draft listings can be published. Listing %(listing)s is currently %(state)s.",
                    listing=listing.name,
                    state=dict(listing._fields['state']._description_selection(listing.env)).get(listing.state),
                ))
            rofr_deadline = fields.Date.context_today(listing) + timedelta(days=ROFR_PUBLISH_DAYS)
            listing.write({
                'state': 'published',
                'rofr_deadline': rofr_deadline,
            })
            listing.message_post(body=_(
                "Listing published on the Al Tahtheeb equity marketplace. "
                "ROFR window ends on %(deadline)s.",
                deadline=rofr_deadline,
            ))
        return True

    def action_match_listing(self, buyer_or_seller_id):
        """Link a counterparty and move the listing to the matched state."""
        counterparty = self.env['res.partner'].browse(buyer_or_seller_id)
        if not counterparty.exists():
            raise UserError(_("The selected counterparty does not exist."))

        for listing in self:
            if listing.state != 'published':
                raise UserError(_(
                    "Only published listings can be matched. Listing %(listing)s is currently %(state)s.",
                    listing=listing.name,
                    state=dict(listing._fields['state']._description_selection(listing.env)).get(listing.state),
                ))
            if listing.shareholder_id == counterparty:
                raise UserError(_(
                    "The counterparty must be different from the listing shareholder."
                ))
            listing.write({
                'matched_partner_id': counterparty.id,
                'state': 'matched',
            })
            listing.message_post(body=_(
                "Listing matched with counterparty %(partner)s.",
                partner=counterparty.display_name,
            ))
        return True

    def _get_equity_company_partner(self):
        """Resolve the cap-table company partner linked to this listing."""
        self.ensure_one()
        CapTable = self.env['equity.cap.table'].sudo()
        base_domain = [
            ('security_class_id', '=', self.share_class_id.id),
            ('securities_type', '=', 'shares'),
        ]
        if self.listing_type == 'sell':
            entry = CapTable.search(
                base_domain + [('holder_id', '=', self.shareholder_id.id)],
                limit=1,
            )
        else:
            entry = CapTable.search(base_domain, limit=1)

        if entry:
            return entry.partner_id

        company_partner = self.company_id.partner_id
        if company_partner.is_company:
            return company_partner

        raise UserError(_(
            "Unable to determine the equity company partner for listing %(listing)s.",
            listing=self.name,
        ))

    def _create_equity_transaction_from_match(self):
        """Create a draft equity.transaction from a matched marketplace listing."""
        self.ensure_one()
        if self.state != 'matched' or not self.matched_partner_id:
            raise UserError(_(
                "An equity transaction can only be created from a matched listing."
            ))
        if self.equity_transaction_id:
            return self.equity_transaction_id

        if self.listing_type == 'sell':
            seller = self.shareholder_id
            buyer = self.matched_partner_id
        else:
            seller = self.matched_partner_id
            buyer = self.shareholder_id

        equity_company = self._get_equity_company_partner()
        transaction = self.env['equity.transaction'].sudo().create({
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
            'rofr_notice_date': self.create_date.date() if self.create_date else fields.Date.context_today(self),
        })
        self.equity_transaction_id = transaction.id
        self.message_post(body=_(
            "Equity transfer transaction %(transaction)s created from marketplace listing.",
            transaction=transaction.display_name,
        ))
        transaction.message_post(body=_(
            "Created from marketplace listing %(listing)s.",
            listing=self.name,
        ))
        _logger.info(
            "Marketplace listing %s matched; equity.transaction %s created.",
            self.name,
            transaction.id,
        )
        return transaction

    def action_portal_match_and_create_transaction(self, counterparty_id):
        """Match a listing with a portal counterparty and spawn the equity transaction."""
        self.ensure_one()
        self.action_match_listing(counterparty_id)
        return self._create_equity_transaction_from_match()
