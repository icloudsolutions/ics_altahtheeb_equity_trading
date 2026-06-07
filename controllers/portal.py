# -*- coding: utf-8 -*-

import logging

from odoo import _, http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
from odoo.addons.equity.controllers.portal import PortalEquity

_logger = logging.getLogger(__name__)


class AltahtheebEquityTradingPortal(PortalEquity):

    def _get_transaction_sign_url(self, transaction, partner):
        """Return the tokenized Sign iframe URL for the given portal partner."""
        sign_request = transaction.sign_request_id
        if not sign_request or sign_request.state != 'sent':
            return False

        sign_item = sign_request.request_item_ids.filtered(
            lambda item: item.partner_id == partner
        )[:1].sudo()
        if not sign_item:
            return False

        if sign_item.state != 'sent':
            return False
        if not sign_item.is_mail_sent:
            return False

        return '/sign/document/%s/%s?portal=1' % (
            sign_request.id,
            sign_item.access_token,
        )

    @http.route(
        '/my/equity/transaction/<int:tx_id>/sign',
        type='http',
        auth='user',
        website=True,
        sitemap=False,
    )
    def portal_equity_transaction_sign(self, tx_id, **kw):
        partner = request.env.user.partner_id
        transaction = request.env['equity.transaction'].sudo().browse(tx_id)

        if not transaction.exists():
            return request.redirect('/')

        if transaction.partner_id != partner:
            _logger.warning(
                "Portal sign access denied for user partner %s on equity.transaction %s (owner partner %s).",
                partner.id,
                tx_id,
                transaction.partner_id.id,
            )
            return request.redirect('/')

        if not transaction.sign_request_id:
            raise AccessError(_("No signature request is linked to this equity transaction."))

        try:
            transaction.check_saudi_statutory_bounds()
        except ValidationError as error:
            raise AccessError(error.args[0]) from error

        sign_url = self._get_transaction_sign_url(transaction, partner)
        if not sign_url:
            raise AccessError(_(
                "You cannot sign this document yet. "
                "Please wait until the signature request is available for your role."
            ))

        values = {
            **self._prepare_portal_layout_values(),
            'page_name': 'equity_sign',
            'transaction': transaction,
            'sign_request': transaction.sign_request_id,
            'sign_url': sign_url,
        }
        return request.render('ics_altahtheeb_equity_trading.portal_sign_iframe_view', values)
