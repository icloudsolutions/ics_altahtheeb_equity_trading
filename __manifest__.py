# -*- coding: utf-8 -*-
{
    'name': 'Al Tahtheeb Equity Trading',
    'version': '19.0.1.0.3',
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
        'account',
        'equity',
        'mail',
        'portal',
        'sign',
        'website',
    ],
    'data': [
        'data/equity_marketplace_sequence.xml',
        'data/equity_revaluation_sequence.xml',
        'data/equity_trade_sequence.xml',
        'data/ir_config_parameter_data.xml',
        'data/ir_cron_data.xml',
        # Security
        'security/ics_altahtheeb_equity_trading_security.xml',
        'security/equity_marketplace_board_rules.xml',
        'security/ir.model.access.csv',
        # Backend views
        'views/equity_trading_dashboard_views.xml',
        'views/ics_altahtheeb_equity_trading_views.xml',
        'views/equity_investment_fund_views.xml',
        'views/equity_security_class_views.xml',
        'views/equity_portfolio_asset_zakat_views.xml',
        'views/equity_portfolio_revaluation_views.xml',
        'views/equity_trade_order_views.xml',
        'views/equity_transaction_disposal_views.xml',
        'views/equity_transaction_views.xml',
        'views/res_config_settings_views.xml',
        'views/ics_altahtheeb_equity_trading_menus.xml',
        # Portal / website templates
        'views/ics_altahtheeb_equity_trading_portal_templates.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ics_altahtheeb_equity_trading/static/src/components/**/*.js',
            'ics_altahtheeb_equity_trading/static/src/components/**/*.xml',
            'ics_altahtheeb_equity_trading/static/src/scss/*.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
