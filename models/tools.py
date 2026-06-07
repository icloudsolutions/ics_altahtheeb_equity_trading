# -*- coding: utf-8 -*-

from odoo import _
from odoo.exceptions import AccessError, UserError, ValidationError


def raise_bilingual_error(exception_class, english, arabic, **params):
    """Raise an Odoo exception with English and Arabic user-facing text."""
    if params:
        english_text = _(english, **params)
        arabic_text = _(arabic, **params)
    else:
        english_text = _(english)
        arabic_text = _(arabic)
    raise exception_class(_(
        "%(english)s\n\n%(arabic)s",
        english=english_text,
        arabic=arabic_text,
    ))


def raise_bilingual_validation(english, arabic, **params):
    raise_bilingual_error(ValidationError, english, arabic, **params)


def raise_bilingual_user_error(english, arabic, **params):
    raise_bilingual_error(UserError, english, arabic, **params)


def raise_bilingual_access_error(english, arabic, **params):
    raise_bilingual_error(AccessError, english, arabic, **params)
