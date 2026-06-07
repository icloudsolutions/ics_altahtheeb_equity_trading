# -*- coding: utf-8 -*-

import logging

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.tools import format_amount

from .portal import AltahtheebEquityTradingPortal

_logger = logging.getLogger(__name__)


class AltahtheebEquityMarketplacePortal(AltahtheebEquityTradingPortal):

    def _prepare_marketplace_values(self, **extra_values):
        partner = request.env.user.partner_id
        Listing = request.env['equity.marketplace.board'].sudo()

        listings = Listing.search([
            ('state', '=', 'published'),
        ], order='rofr_deadline asc, create_date desc')

        listing_type_labels = dict(
            Listing._fields['listing_type']._description_selection(request.env)
        )

        values = {
            **self._prepare_portal_layout_values(),
            'page_name': 'equity_marketplace',
            'listings': listings,
            'current_partner': partner,
            'listing_type_labels': listing_type_labels,
            'format_listing_amount': lambda amount, currency: format_amount(
                request.env,
                amount,
                currency,
            ),
            'today': fields.Date.context_today(Listing),
        }
        values.update(extra_values)
        return values

    def _get_marketplace_flash_messages(self):
        """Map query-string codes to localized portal banner messages."""
        success_code = request.params.get('success')
        error_code = request.params.get('error')

        if success_code:
            return {
                'success_message': _(
                    "Listing matched successfully. A draft equity transfer transaction "
                    "has been created and is awaiting internal approval."
                ),
                'error_message': False,
            }

        error_messages = {
            'own_listing': _(
                "You cannot match your own marketplace listing."
            ),
            'not_found': _(
                "This marketplace listing is no longer available."
            ),
            'rofr_pending': _(
                "The Right of First Refusal (ROFR) window is still open for this listing. "
                "Please wait until the deadline has passed."
            ),
            'access': _(
                "You do not have permission to perform this marketplace action."
            ),
        }
        session_error = request.session.pop('marketplace_error', False)
        error_message = session_error or error_messages.get(error_code)
        if error_code == '1' and session_error:
            error_message = session_error

        return {
            'success_message': False,
            'error_message': error_message,
        }

    @http.route(
        '/my/equity/marketplace',
        type='http',
        auth='user',
        website=True,
        sitemap=False,
    )
    def portal_equity_marketplace(self, **kw):
        flash = self._get_marketplace_flash_messages()
        values = self._prepare_marketplace_values(**flash)
        return request.render(
            'ics_altahtheeb_equity_trading.marketplace_dashboard_view',
            values,
        )

    @http.route(
        '/my/equity/marketplace/match/<int:listing_id>',
        type='http',
        auth='user',
        website=True,
        methods=['POST'],
        sitemap=False,
    )
    def portal_equity_marketplace_match(self, listing_id, **kw):
        partner = request.env.user.partner_id
        listing = request.env['equity.marketplace.board'].sudo().browse(listing_id)

        if not listing.exists() or listing.state != 'published':
            return request.redirect('/my/equity/marketplace?error=not_found')

        if listing.shareholder_id == partner:
            _logger.warning(
                "Portal user %s attempted to match their own listing %s.",
                partner.id,
                listing_id,
            )
            return request.redirect('/my/equity/marketplace?error=own_listing')

        if listing.rofr_deadline and fields.Date.context_today(listing) < listing.rofr_deadline:
            return request.redirect('/my/equity/marketplace?error=rofr_pending')

        try:
            listing.action_portal_match_and_create_transaction(partner.id)
        except AccessError as error:
            request.session['marketplace_error'] = error.args[0]
            return request.redirect('/my/equity/marketplace?error=access')
        except (UserError, ValidationError) as error:
            request.session['marketplace_error'] = error.args[0]
            return request.redirect('/my/equity/marketplace?error=1')

        return request.redirect('/my/equity/marketplace?success=1')
