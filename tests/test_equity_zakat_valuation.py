# -*- coding: utf-8 -*-

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestEquityZakatValuation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.share_class_trading = cls.env['equity.security.class'].create({
            'name': 'Tadawul Trading',
            'class_type': 'shares',
            'zakat_classification': 'short_term_trading',
        })
        cls.share_class_strategic = cls.env['equity.security.class'].create({
            'name': 'Tadawul Strategic',
            'class_type': 'shares',
            'zakat_classification': 'long_term_strategic',
        })
        cls.PortfolioAsset = cls.env['equity.portfolio.asset']

    def _create_asset(self, share_class, market_price):
        return self.PortfolioAsset.create({
            'company_id': self.company.id,
            'share_class_id': share_class.id,
            'market_price': market_price,
        })

    def test_trading_holding_contributes_market_value_to_zakat_base(self):
        asset = self._create_asset(self.share_class_trading, 25.0)
        asset.invalidate_recordset()
        asset._compute_zakat_metrics()

        self.assertEqual(asset.zakat_classification, 'short_term_trading')
        expected_market_value = asset._get_holdings_quantity() * 25.0
        self.assertAlmostEqual(asset.zakat_base_contribution_value, expected_market_value)
        self.assertAlmostEqual(asset.zakat_deductible_value, 0.0)

    def test_strategic_holding_has_zero_zakat_base_contribution(self):
        asset = self._create_asset(self.share_class_strategic, 40.0)
        asset.invalidate_recordset()
        asset._compute_zakat_metrics()

        self.assertEqual(asset.zakat_classification, 'long_term_strategic')
        self.assertAlmostEqual(asset.zakat_base_contribution_value, 0.0)
        book_value = asset._get_allocated_book_value(fields.Date.context_today(asset))
        self.assertAlmostEqual(asset.zakat_deductible_value, book_value)
