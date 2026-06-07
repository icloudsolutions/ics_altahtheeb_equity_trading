# -*- coding: utf-8 -*-

import logging

from odoo import _, models

_logger = logging.getLogger(__name__)

LEGAL_SIGNATURE_CONFIRMED_MESSAGE = (
    "Cryptographic identity verification verified. "
    "Equity ledger balances updated securely."
)


class SignRequest(models.Model):
    _inherit = 'sign.request'

    def write(self, vals):
        becoming_signed = self.browse()
        if vals.get('state') == 'signed':
            becoming_signed = self.filtered(lambda sr: sr.state != 'signed')

        res = super().write(vals)

        if becoming_signed:
            becoming_signed._ics_finalize_linked_equity_transactions()
        return res

    def _ics_finalize_linked_equity_transactions(self):
        """Finalize linked equity transactions when Sign requests reach the signed state."""
        sign_requests = self.filtered(lambda sr: sr.state == 'signed')
        if not sign_requests:
            return

        transactions = self.env['equity.transaction'].search([
            ('sign_request_id', 'in', sign_requests.ids),
            ('state', '=', 'waiting_signature'),
        ])
        if not transactions:
            return

        finalized = transactions._finalize_after_legal_signature()
        if not finalized:
            return

        message = _(LEGAL_SIGNATURE_CONFIRMED_MESSAGE)
        finalized_sign_request_ids = set(finalized.sign_request_id.ids)
        for sign_request in sign_requests.filtered(lambda sr: sr.id in finalized_sign_request_ids):
            sign_request.message_post(body=message)

        _logger.info(
            "sign.request write hook finalized equity.transaction %s "
            "after sign.request %s transitioned to signed.",
            finalized.ids,
            list(finalized_sign_request_ids),
        )
