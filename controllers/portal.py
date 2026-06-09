# -*- coding: utf-8 -*-
"""
portal.py — Al Tahtheeb Equity Trading Portal Controller
=========================================================
Extends the upstream ``equity`` module's ``PortalEquity`` controller with:

* **My Portfolio Dashboard** (``/my/equity/portfolio``) — shows live cap-table
  share allocations and a full historical transaction log for the authenticated
  partner.
* **Sign Iframe** (``/my/equity/transaction/<id>/sign``) — embeds the Odoo Sign
  envelope for a pending equity transfer that awaits the portal user's signature.

Security model
--------------
* All ORM reads use the portal user's own access rights first; ``sudo()`` is
  applied narrowly and only after ownership/access checks are passed.
* The cap-table (``equity.cap.table``) is a read-only SQL view that requires
  ``sudo()`` for stable access from portal sessions — consistent with the
  upstream ``equity`` portal controller pattern.
* Sign access tokens are read under ``sudo()`` only after confirming the
  partner is an authorised signer on the request item.
"""

import logging
from functools import partial

from odoo import _, fields, http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
from odoo.tools import format_amount, format_date
from odoo.addons.equity.controllers.portal import PortalEquity

_logger = logging.getLogger(__name__)

# States considered "completed" for the Historical Transactions Log.
# ``signed_legal`` is our module-level state that precedes native ``done``.
COMPLETED_LEDGER_STATES = ('done', 'signed_legal')


class AltahtheebEquityTradingPortal(PortalEquity):
    """
    Portal controller for Al Tahtheeb equity trading features.

    Inherits ``PortalEquity`` so the standard ``/my/equity`` dashboard,
    breadcrumb helpers, and ``_prepare_home_portal_values`` counter mechanism
    are all available without duplication.
    """

    # -------------------------------------------------------------------------
    # Home portal counter
    # -------------------------------------------------------------------------

    def _prepare_home_portal_values(self, counters):
        """
        Inject the ``portfolio_count`` counter shown on the My Account homepage.

        A non-zero count causes the "My Portfolio" entry to display a badge,
        signalling that the partner has completed transaction history to review.
        """
        values = super()._prepare_home_portal_values(counters)
        partner_id = self._resolve_portal_partner_id()
        Transaction = request.env['equity.transaction']

        if 'portfolio_count' in counters:
            values['portfolio_count'] = (
                Transaction.search_count(
                    self._build_completed_transactions_domain(partner_id),
                    limit=1,
                )
                if partner_id and Transaction.has_access('read')
                else 0
            )
        if 'marketplace_count' in counters:
            Listing = request.env['equity.marketplace.board']
            values['marketplace_count'] = (
                Listing.search_count([('state', '=', 'published')], limit=1)
                if Listing.has_access('read')
                else 0
            )
        if 'pending_signature_count' in counters:
            values['pending_signature_count'] = (
                len(self._build_pending_signature_rows(
                    request.env.user.partner_id,
                ))
                if partner_id and Transaction.has_access('read')
                else 0
            )
        return values

    @staticmethod
    def _split_bilingual_message(message):
        """Split bilingual EN/AR portal messages into display paragraphs."""
        if not message:
            return []
        return [part.strip() for part in message.split('\n\n') if part.strip()]

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _resolve_portal_partner_id(self):
        """
        Return the authenticated portal user's ``res.partner`` ID.

        Uses ``request.env.user.partner_id`` directly rather than the parent's
        ``_get_user_partner_id()`` helper (which may not be present in all
        upstream ``equity`` versions) to ensure forward compatibility.
        """
        partner = request.env.user.partner_id
        return partner.id if partner else False

    def _build_completed_transactions_domain(self, partner_id):
        """
        Build the ORM domain that selects finalized equity transactions
        where ``partner_id`` was either the seller or the subscriber.

        Both roles are included so a complete bilateral trade history is shown
        regardless of whether the partner was the originator or the counterparty.

        :param int partner_id: ``res.partner`` ID of the authenticated user.
        :returns: list — Odoo domain expression.
        """
        if not partner_id:
            return [('id', '=', False)]
        return [
            ('state', 'in', COMPLETED_LEDGER_STATES),
            '|',
            ('seller_id', '=', partner_id),
            ('subscriber_id', '=', partner_id),
        ]

    def _resolve_operation_type(self, transaction, partner_id):
        """
        Map a native ``equity.transaction`` to a portal-friendly operation label.

        Returns a ``(code, label)`` tuple where ``code`` is one of
        ``'buy'``, ``'sell'``, or ``'other'`` — used by the QWeb template to
        apply the correct Bootstrap badge colour class.

        :param transaction:  ``equity.transaction`` browse record.
        :param int partner_id: Authenticated partner ID.
        :returns: tuple[str, str]
        """
        tx_type = transaction.transaction_type
        if tx_type == 'transfer':
            if transaction.subscriber_id.id == partner_id:
                return 'buy', _('Buy')
            if transaction.seller_id.id == partner_id:
                return 'sell', _('Sell')
        elif tx_type == 'issuance':
            return 'buy', _('Issuance')
        elif tx_type == 'cancellation':
            return 'sell', _('Cancellation')
        elif tx_type == 'exercise':
            return 'buy', _('Exercise')
        return 'other', tx_type

    def _resolve_display_currency(self, share_entries, partner):
        """
        Choose a single display currency for the portfolio valuation summary.

        If all cap-table entries share the same currency, that currency is used.
        If the portfolio spans multiple currencies, fall back to the partner's
        own ``equity_currency_id``, then to the current company currency.

        :param share_entries:  Filtered ``equity.cap.table`` recordset (shares only).
        :param partner:        ``res.partner`` browse record of the portal user.
        :returns: ``res.currency`` browse record.
        """
        portfolio_currencies = share_entries.partner_id.mapped('equity_currency_id').filtered('id')
        if len(portfolio_currencies) == 1:
            return portfolio_currencies
        return (
            partner.equity_currency_id
            or request.env.company.currency_id
        )

    def _build_share_class_rows(self, share_entries):
        """
        Convert cap-table share entries into serialisable dicts for the template.

        Sorted alphabetically by company name then share class name so the
        Current Share Allocations table is stable across page reloads.

        :param share_entries: Filtered ``equity.cap.table`` recordset.
        :returns: list[dict]
        """
        rows = []
        for entry in share_entries.sorted(
            key=lambda e: (
                e.partner_id.display_name or '',
                e.security_class_id.display_name or '',
            )
        ):
            rows.append({
                'company_name': entry.partner_id.display_name,
                'class_name': entry.security_class_id.display_name,
                'shares': entry.securities,
                'valuation': entry.valuation,
                'currency': entry.partner_id.equity_currency_id or request.env.company.currency_id,
            })
        return rows

    def _build_transaction_rows(self, completed_transactions, partner_id):
        """
        Convert completed ``equity.transaction`` records into serialisable dicts.

        Pre-resolves human-readable selection labels once outside the loop for
        efficiency, and maps each transaction to a portal-friendly operation
        code/label pair for badge rendering.

        :param completed_transactions: ``equity.transaction`` recordset.
        :param int partner_id: Authenticated partner ID (for buy/sell mapping).
        :returns: list[dict]
        """
        Transaction = request.env['equity.transaction']
        state_labels = dict(
            Transaction._fields['state']._description_selection(request.env)
        )
        rows = []
        for tx in completed_transactions:
            operation_code, operation_label = self._resolve_operation_type(tx, partner_id)
            rows.append({
                'date': tx.date,
                'reference': tx.display_name,
                'share_class': tx.security_class_id.display_name or '—',
                'operation_code': operation_code,
                'operation_label': operation_label,
                'qty': tx.securities,
                'unit_price': tx.security_price,
                'currency': (
                    tx.equity_currency_id
                    or request.env.company.currency_id
                ),
                'state': tx.state,
                'state_label': state_labels.get(tx.state, tx.state),
            })
        return rows

    def _build_pending_signature_rows(self, partner):
        """
        Return equity transactions awaiting the portal partner's signature.

        :param partner: ``res.partner`` browse record of the authenticated user.
        :returns: list[dict]
        """
        if not partner:
            return []

        Transaction = request.env['equity.transaction']
        if not Transaction.has_access('read'):
            return []

        pending_transactions = Transaction.search([
            ('state', '=', 'waiting_signature'),
            '|',
            ('seller_id', '=', partner.id),
            ('subscriber_id', '=', partner.id),
        ], order='date desc, id desc')

        rows = []
        for tx in pending_transactions:
            sign_url = self._get_transaction_sign_url(tx, partner)
            if not sign_url:
                continue
            rows.append({
                'id': tx.id,
                'reference': tx.display_name,
                'share_class': tx.security_class_id.display_name or '—',
                'date': tx.date,
                'sign_url': sign_url,
            })
        return rows

    # -------------------------------------------------------------------------
    # Portfolio dashboard value builder
    # -------------------------------------------------------------------------

    def _prepare_portfolio_dashboard_values(self, **extra_values):
        """
        Assemble the full template context for the My Portfolio Dashboard.

        Data sources:
        * ``equity.cap.table`` (sudo) — live share allocations.
        * ``equity.transaction`` (portal ACL) — completed transaction history.

        :raises AccessError: if the user has no read access to equity.transaction.
        :returns: dict — QWeb template context.
        """
        partner = request.env.user.partner_id
        partner_id = partner.id

        # Guard: must be a real partner with equity.transaction read access.
        Transaction = request.env['equity.transaction']
        if not partner_id or not Transaction.has_access('read'):
            raise AccessError(_("Equity portfolio access is not available for your account."))

        # ------------------------------------------------------------------
        # Cap-table: live holdings (sudo — read-only SQL view)
        # ------------------------------------------------------------------
        cap_entries = request.env['equity.cap.table'].sudo().search([
            ('holder_id', '=', partner_id),
            ('securities', '>', 0),
        ])
        share_entries = cap_entries.filtered(
            lambda e: e.securities_type == 'shares'
        )

        total_shares = sum(share_entries.mapped('securities'))
        portfolio_valuation = sum(share_entries.mapped('valuation'))
        share_class_rows = self._build_share_class_rows(share_entries)
        display_currency = self._resolve_display_currency(share_entries, partner)

        portfolio_currencies = share_entries.partner_id.mapped('equity_currency_id').filtered('id')
        has_mixed_currencies = len(set(portfolio_currencies.ids)) > 1

        # ------------------------------------------------------------------
        # Completed transaction history (portal ACL enforced by ORM)
        # ------------------------------------------------------------------
        completed_transactions = Transaction.search(
            self._build_completed_transactions_domain(partner_id),
            order='date desc, id desc',
        )
        transaction_rows = self._build_transaction_rows(completed_transactions, partner_id)
        pending_signature_rows = self._build_pending_signature_rows(partner)

        # ------------------------------------------------------------------
        # Assemble context
        # ------------------------------------------------------------------
        fmt_amount_fn = partial(format_amount, request.env)
        fmt_date_fn = partial(format_date, request.env)

        values = {
            **self._prepare_portal_layout_values(),
            'page_name': 'equity_portfolio',
            'current_partner': partner,
            # Summary cards
            'total_shares': int(total_shares) if total_shares == int(total_shares) else total_shares,
            'portfolio_valuation': portfolio_valuation,
            'display_currency': display_currency,
            'has_mixed_currencies': has_mixed_currencies,
            # Share allocations table
            'share_class_rows': share_class_rows,
            'has_holdings': bool(share_class_rows),
            # Pending signatures
            'pending_signature_rows': pending_signature_rows,
            'has_pending_signatures': bool(pending_signature_rows),
            # Transaction history table
            'transaction_rows': transaction_rows,
            'has_transactions': bool(transaction_rows),
            # Formatting helpers (called from QWeb as Python callables)
            'fmt_amount': fmt_amount_fn,
            'fmt_date': fmt_date_fn,
            'today': fields.Date.context_today(partner),
        }
        values.update(extra_values)
        return values

    # -------------------------------------------------------------------------
    # Sign iframe helper
    # -------------------------------------------------------------------------

    def _get_transaction_sign_url(self, transaction, partner):
        """
        Return the tokenized Odoo Sign iframe URL for the assigned portal signer.

        Reads ``access_token`` under ``sudo()`` only after confirming the
        partner is an authorised signer and the request item is in ``sent`` state.

        :returns: str URL or ``False`` if signing is not yet available.
        """
        sign_item = transaction._portal_get_sign_item_for_partner(partner)
        if not sign_item:
            return False

        sign_item = sign_item.sudo()
        if sign_item.state != 'sent' or not sign_item.is_mail_sent:
            return False

        sign_request = transaction.sign_request_id
        return '/sign/document/%s/%s?portal=1' % (sign_request.id, sign_item.access_token)

    # -------------------------------------------------------------------------
    # Routes
    # -------------------------------------------------------------------------

    @http.route(
        '/my/equity/transaction/<int:tx_id>/sign',
        type='http',
        auth='user',
        website=True,
        sitemap=False,
    )
    def portal_equity_transaction_sign(self, tx_id, **kw):
        """Render the Odoo Sign iframe for a pending equity transfer."""
        partner = request.env.user.partner_id
        transaction = request.env['equity.transaction'].browse(tx_id)

        if not transaction.exists():
            return request.redirect('/')

        try:
            transaction.check_access('read')
        except AccessError:
            _logger.warning(
                "Portal sign access denied for partner %s on equity.transaction %s.",
                partner.id,
                tx_id,
            )
            return request.redirect('/')

        sign_item = transaction._portal_get_sign_item_for_partner(partner)
        if not sign_item:
            raise AccessError(_(
                "%(english)s\n\n%(arabic)s",
                english=_("You are not an authorised signer for this equity transaction."),
                arabic=_("لست موقعاً مخولاً على هذه المعاملة."),
            ))

        if not transaction.sign_request_id:
            raise AccessError(_(
                "%(english)s\n\n%(arabic)s",
                english=_("No signature request is linked to this equity transaction."),
                arabic=_("لا يوجد طلب توقيع مرتبط بهذه المعاملة."),
            ))

        if transaction.state != 'waiting_signature':
            try:
                transaction.check_saudi_statutory_bounds()
            except ValidationError as exc:
                raise AccessError(exc.args[0]) from exc

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
            'fmt_date': partial(format_date, request.env),
        }
        return request.render(
            'ics_altahtheeb_equity_trading.portal_sign_iframe_view',
            values,
        )

    @http.route(
        '/my/equity/portfolio',
        type='http',
        auth='user',
        website=True,
        sitemap=False,
    )
    def portal_equity_portfolio(self, **kw):
        """Render the My Portfolio Dashboard for the authenticated partner."""
        try:
            values = self._prepare_portfolio_dashboard_values()
            return request.render(
                'ics_altahtheeb_equity_trading.portal_portfolio_dashboard_view',
                values,
            )
        except AccessError:
            return request.redirect('/web/login?redirect=/my/equity/portfolio')
        except Exception:
            _logger.exception("Unexpected error rendering equity portfolio dashboard.")
            return request.render('website.page_500', {}, status=500)
