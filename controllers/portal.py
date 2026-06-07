# -*- coding: utf-8 -*-

import logging
from functools import partial

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.tools import format_amount, format_date
from odoo.addons.equity.controllers.portal import PortalEquity

_logger = logging.getLogger(__name__)

COMPLETED_LEDGER_STATES = ('done', 'signed_legal')


class AltahtheebEquityTradingPortal(PortalEquity):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'portfolio_count' in counters:
            holder_id = self._get_user_partner_id()
            Transaction = request.env['equity.transaction']
            values['portfolio_count'] = (
                Transaction.search_count([
                    *self._get_completed_transactions_domain(holder_id),
                ], limit=1)
                if holder_id and Transaction.has_access('read') else
                0
            )
        return values

    def _get_completed_transactions_domain(self, holder_id):
        """Domain for finalized equity transactions visible to the portal holder."""
        return [
            *self._get_transactions_domain(holder_id),
            ('state', 'in', COMPLETED_LEDGER_STATES),
        ]

    def _get_portal_operation_type(self, transaction, holder_id):
        """Map a native equity transaction to a portal-friendly Buy/Sell label."""
        if transaction.transaction_type == 'transfer':
            if transaction.subscriber_id.id == holder_id:
                return 'buy', _('Buy')
            if transaction.seller_id.id == holder_id:
                return 'sell', _('Sell')
        elif transaction.transaction_type == 'issuance':
            return 'buy', _('Buy')
        elif transaction.transaction_type == 'cancellation':
            return 'sell', _('Sell')
        elif transaction.transaction_type == 'exercise':
            return 'buy', _('Buy')
        return 'other', transaction.transaction_type

    def _prepare_portfolio_dashboard_values(self, **extra_values):
        """Build cap-table holdings and completed transaction history for the portal partner."""
        partner = request.env.user.partner_id
        holder_id = partner.id
        Transaction = request.env['equity.transaction']

        if not holder_id or not Transaction.has_access('read'):
            raise request.not_found()

        # Native Odoo 19 cap table (SQL view); sudo mirrors stock equity portal controller.
        cap_entries = request.env['equity.cap.table'].sudo().search([
            ('holder_id', '=', holder_id),
            ('securities', '>', 0),
        ])
        share_entries = cap_entries.filtered(lambda entry: entry.securities_type == 'shares')
        total_shares = sum(share_entries.mapped('securities'))
        portfolio_valuation = sum(share_entries.mapped('valuation'))

        share_classes = [{
            'company_name': entry.partner_id.display_name,
            'class_name': entry.security_class_id.display_name,
            'shares': entry.securities,
            'valuation': entry.valuation,
            'currency': entry.partner_id.equity_currency_id,
        } for entry in share_entries.sorted(
            key=lambda entry: (entry.partner_id.display_name, entry.security_class_id.display_name),
        )]

        portfolio_currencies = share_entries.partner_id.mapped('equity_currency_id')
        display_currency = (
            portfolio_currencies[0]
            if len(portfolio_currencies) == 1
            else partner.equity_currency_id or request.env.company.currency_id
        )

        completed_transactions = Transaction.search(
            self._get_completed_transactions_domain(holder_id),
            order='date desc, id desc',
        )
        transaction_type_labels = dict(
            Transaction._fields['transaction_type']._description_selection(request.env)
        )
        state_labels = dict(
            Transaction._fields['state']._description_selection(request.env)
        )

        transaction_rows = []
        for transaction in completed_transactions:
            operation_code, operation_label = self._get_portal_operation_type(
                transaction,
                holder_id,
            )
            transaction_rows.append({
                'date': transaction.date,
                'reference': transaction.display_name,
                'share_class': transaction.security_class_id.display_name,
                'operation_code': operation_code,
                'operation_label': operation_label,
                'transaction_type_label': transaction_type_labels.get(
                    transaction.transaction_type,
                    transaction.transaction_type,
                ),
                'qty': transaction.securities,
                'unit_price': transaction.security_price,
                'currency': transaction.equity_currency_id,
                'state': transaction.state,
                'state_label': state_labels.get(transaction.state, transaction.state),
            })

        values = {
            **self._prepare_portal_layout_values(),
            'page_name': 'equity_portfolio',
            'current_partner': partner,
            'total_shares': total_shares,
            'portfolio_valuation': portfolio_valuation,
            'display_currency': display_currency,
            'has_mixed_currencies': len(set(portfolio_currencies.ids)) > 1,
            'share_classes': share_classes,
            'transaction_rows': transaction_rows,
            'fmt_amount': partial(format_amount, request.env),
            'fmt_date': partial(format_date, request.env),
            'today': fields.Date.context_today(partner),
        }
        values.update(extra_values)
        return values

    def _get_transaction_sign_url(self, transaction, partner):
        """Return the tokenized Sign iframe URL for the assigned portal signer."""
        sign_item = transaction._portal_get_sign_item_for_partner(partner)
        if not sign_item:
            return False

        # access_token is restricted; read it under sudo after partner ownership checks.
        sign_item = sign_item.sudo()
        if sign_item.state != 'sent' or not sign_item.is_mail_sent:
            return False

        sign_request = transaction.sign_request_id
        return '/sign/document/%s/%s?portal=1' % (sign_request.id, sign_item.access_token)

    @http.route(
        '/my/equity/transaction/<int:tx_id>/sign',
        type='http',
        auth='user',
        website=True,
        sitemap=False,
    )
    def portal_equity_transaction_sign(self, tx_id, **kw):
        partner = request.env.user.partner_id
        Transaction = request.env['equity.transaction']
        transaction = Transaction.browse(tx_id)

        if not transaction.exists():
            return request.redirect('/')

        try:
            transaction.check_access('read')
        except AccessError:
            _logger.warning(
                _("Portal sign access denied for user partner %s on equity.transaction %s."),
                partner.id,
                tx_id,
            )
            return request.redirect('/')

        sign_item = transaction._portal_get_sign_item_for_partner(partner)
        if not sign_item:
            raise AccessError(_(
                "%(english)s\n\n%(arabic)s",
                english=_("You are not an authorized signer for this equity transaction."),
                arabic=_("لست موقعاً مخولاً على هذه المعاملة."),
            ))

        if not transaction.sign_request_id:
            raise AccessError(_(
                "%(english)s\n\n%(arabic)s",
                english=_("No signature request is linked to this equity transaction."),
                arabic=_("لا يوجد طلب توقيع مرتبط بهذه المعاملة."),
            ))

        try:
            transaction.check_saudi_statutory_bounds()
        except ValidationError as error:
            raise AccessError(error.args[0]) from error

        sign_url = self._get_transaction_sign_url(transaction, partner)
        if not sign_url:
            raise AccessError(_(
                "%(english)s\n\n%(arabic)s",
                english=_(
                    "You cannot sign this document yet. "
                    "Please wait until the signature request is available for your role."
                ),
                arabic=_(
                    "لا يمكنك التوقيع على هذا المستند بعد. "
                    "يرجى الانتظار حتى يصبح طلب التوقيع متاحاً لدورك."
                ),
            ))

        values = {
            **self._prepare_portal_layout_values(),
            'page_name': 'equity_sign',
            'transaction': transaction,
            'sign_request': transaction.sign_request_id,
            'sign_url': sign_url,
        }
        return request.render('ics_altahtheeb_equity_trading.portal_sign_iframe_view', values)

    @http.route(
        '/my/equity/portfolio',
        type='http',
        auth='user',
        website=True,
        sitemap=False,
    )
    def portal_equity_portfolio(self, **kw):
        try:
            values = self._prepare_portfolio_dashboard_values()
            return request.render(
                'ics_altahtheeb_equity_trading.portal_portfolio_dashboard_view',
                values,
            )
        except AccessError:
            return request.redirect('/web/login?redirect=/my/equity/portfolio')
        except Exception:
            _logger.exception('Unexpected error rendering equity portfolio dashboard.')
            return request.render('website.page_500', {}, status=500)
