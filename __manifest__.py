# -*- coding: utf-8 -*-
{
    'name': 'Al Tahtheeb Equity Trading',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Equity',
    'summary': 'Automated Stakeholder Trading and Legal E-Signatures for Al Tahtheeb',
    'description': """
Al Tahtheeb Equity Trading
==========================
Automate stakeholder equity trading workflows with integrated legal
e-signatures (Sign) and stakeholder self-service on the website portal.
    """,
    'author': 'iCloud Solutions',
    'license': 'OEEL-1',
    'depends': [
        'equity',
        'sign',
        'website',
    ],
    'data': [
        'data/equity_marketplace_sequence.xml',
        # Security
        'security/ics_altahtheeb_equity_trading_security.xml',
        'security/ir.model.access.csv',
        # Backend views
        'views/ics_altahtheeb_equity_trading_views.xml',
        'views/ics_altahtheeb_equity_trading_menus.xml',
        # Portal / website templates
        'views/ics_altahtheeb_equity_trading_portal_templates.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
