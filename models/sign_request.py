# -*- coding: utf-8 -*-

import logging

from odoo import _, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

LEGAL_SIGNATURE_CONFIRMED_MESSAGE_EN = (
    "Cryptographic identity verification verified. "
    "Equity ledger balances updated securely."
)
LEGAL_SIGNATURE_CONFIRMED_MESSAGE_AR = (
    "تم التحقق من الهوية الرقمية بنجاح. "
    "تم تحديث أرصدة سجل الأسهم بشكل آمن."
)


class SignRequest(models.Model):
    _inherit = 'sign.request'

    def write(self, vals):
        becoming_signed = self.browse()
        if vals.get('state') == 'signed' and not self.env.context.get('ics_skip_equity_finalize'):
            becoming_signed = self.filtered(lambda sign_request: sign_request.state != 'signed')

        result = super().write(vals)

        if becoming_signed:
            becoming_signed._ics_finalize_linked_equity_transactions()
        return result

    def _ics_finalize_linked_equity_transactions(self):
        """Finalize linked equity transactions when sign requests reach the signed state."""
        sign_requests = self.filtered(lambda sign_request: sign_request.state == 'signed')
        if not sign_requests:
            return

        try:
            transactions = self.env['equity.transaction'].search([
                ('sign_request_id', 'in', sign_requests.ids),
                ('state', '=', 'waiting_signature'),
            ])
        except AccessError:
            _logger.warning(
                'Access denied while resolving equity transactions for sign.request %s.',
                sign_requests.ids,
            )
            return

        if not transactions:
            return

        finalized = transactions._finalize_after_legal_signature()
        if not finalized:
            return

        message = _(
            "%(english)s\n\n%(arabic)s",
            english=_(LEGAL_SIGNATURE_CONFIRMED_MESSAGE_EN),
            arabic=_(LEGAL_SIGNATURE_CONFIRMED_MESSAGE_AR),
        )
        finalized_sign_request_ids = set(finalized.sign_request_id.ids)
        for sign_request in sign_requests.filtered(
            lambda sr: sr.id in finalized_sign_request_ids
        ):
            sign_request.message_post(body=message)

        _logger.info(
            _("sign.request write hook finalized equity.transaction %s after sign.request %s."),
            finalized.ids,
            list(finalized_sign_request_ids),
        )
