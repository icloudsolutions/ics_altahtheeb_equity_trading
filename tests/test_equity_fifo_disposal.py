# -*- coding: utf-8 -*-

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestEquityFifoDisposal(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_partner = cls.env['res.partner'].create({
            'name': 'Al Tahtheeb Equity Co.',
            'is_company': True,
        })
        cls.seller = cls.env['res.partner'].create({'name': 'Seller One'})
        cls.buyer = cls.env['res.partner'].create({'name': 'Buyer Two'})
        cls.share_class = cls.env['equity.security.class'].create({'name': 'ORD-A'})

        cls.Transaction = cls.env['equity.transaction']
        cls.Disposal = cls.env['equity.transaction.disposal']
        cls.LotAllocation = cls.env['equity.lot.allocation']

    def _create_buy_lot(self, date, qty, price):
        return self.Transaction.create({
            'partner_id': self.company_partner.id,
            'transaction_type': 'issuance',
            'subscriber_id': self.seller.id,
            'security_class_id': self.share_class.id,
            'securities': qty,
            'security_price': price,
            'date': date,
            'state': 'done',
        })

    def _create_sell(self, date, qty, price):
        return self.Transaction.create({
            'partner_id': self.company_partner.id,
            'transaction_type': 'transfer',
            'seller_id': self.seller.id,
            'subscriber_id': self.buyer.id,
            'security_class_id': self.share_class.id,
            'securities': qty,
            'security_price': price,
            'date': date,
            'state': 'done',
        })

    def test_partial_sell_spans_multiple_fifo_lots(self):
        """Selling 120 shares consumes 100 @ $10 then 20 @ $12 (FIFO)."""
        buy_lot_1 = self._create_buy_lot('2020-01-01', 100, 10.0)
        buy_lot_2 = self._create_buy_lot('2020-06-01', 50, 12.0)
        sell = self._create_sell('2021-01-01', 120, 15.0)

        result = self.Disposal.process_sell_transaction(sell)

        self.assertAlmostEqual(result['cost_basis_total'], 1240.0)
        self.assertAlmostEqual(result['proceeds_total'], 1800.0)
        self.assertAlmostEqual(result['realized_gain_loss'], 560.0)
        self.assertEqual(len(result['allocation_ids']), 2)

        allocations = self.LotAllocation.search([('sell_transaction_id', '=', sell.id)])
        lot_1_alloc = allocations.filtered(lambda a: a.buy_transaction_id == buy_lot_1)
        lot_2_alloc = allocations.filtered(lambda a: a.buy_transaction_id == buy_lot_2)

        self.assertAlmostEqual(lot_1_alloc.qty, 100.0)
        self.assertAlmostEqual(lot_1_alloc.buy_unit_price, 10.0)
        self.assertAlmostEqual(lot_1_alloc.realized_gain_loss, 500.0)

        self.assertAlmostEqual(lot_2_alloc.qty, 20.0)
        self.assertAlmostEqual(lot_2_alloc.buy_unit_price, 12.0)
        self.assertAlmostEqual(lot_2_alloc.realized_gain_loss, 60.0)

        self.assertTrue(sell.disposal_processed)
        self.assertAlmostEqual(sell.realized_gain_loss, 560.0)

    def test_second_partial_sell_uses_remaining_oldest_lot_balance(self):
        """After first sell, a second sell consumes the remaining $12 lot shares."""
        self._create_buy_lot('2020-01-01', 100, 10.0)
        self._create_buy_lot('2020-06-01', 50, 12.0)

        first_sell = self._create_sell('2021-01-01', 120, 15.0)
        self.Disposal.process_sell_transaction(first_sell)

        second_sell = self._create_sell('2021-06-01', 30, 14.0)
        result = self.Disposal.process_sell_transaction(second_sell)

        # Remaining on second buy lot after first sell: 30 shares @ $12 cost, sold @ $14
        self.assertAlmostEqual(result['cost_basis_total'], 360.0)
        self.assertAlmostEqual(result['proceeds_total'], 420.0)
        self.assertAlmostEqual(result['realized_gain_loss'], 60.0)
        self.assertEqual(len(result['allocation_ids']), 1)

    def test_insufficient_buy_lots_raises_validation_error(self):
        self._create_buy_lot('2020-01-01', 40, 10.0)
        sell = self._create_sell('2021-01-01', 50, 15.0)

        with self.assertRaises(ValidationError):
            self.Disposal.process_sell_transaction(sell)

        self.assertFalse(self.LotAllocation.search([('sell_transaction_id', '=', sell.id)]))

    def test_process_is_idempotent_without_force(self):
        self._create_buy_lot('2020-01-01', 100, 10.0)
        sell = self._create_sell('2021-01-01', 50, 15.0)

        first = self.Disposal.process_sell_transaction(sell)
        second = self.Disposal.process_sell_transaction(sell)

        self.assertEqual(first['allocation_ids'], second['allocation_ids'])
        self.assertEqual(
            len(self.LotAllocation.search([('sell_transaction_id', '=', sell.id)])),
            1,
        )

    def test_preview_matches_persisted_fifo_result(self):
        self._create_buy_lot('2020-01-01', 100, 10.0)
        self._create_buy_lot('2020-03-01', 25, 11.0)
        sell = self._create_sell('2021-01-01', 110, 13.0)

        preview = self.Disposal.preview_sell_transaction(sell)
        persisted = self.Disposal.process_sell_transaction(sell)

        self.assertAlmostEqual(preview['realized_gain_loss'], persisted['realized_gain_loss'])
        self.assertAlmostEqual(preview['cost_basis_total'], persisted['cost_basis_total'])
        self.assertEqual(len(preview['lines']), 2)

    def test_write_to_done_triggers_automatic_fifo_processing(self):
        self._create_buy_lot('2020-01-01', 80, 10.0)
        sell = self.Transaction.create({
            'partner_id': self.company_partner.id,
            'transaction_type': 'transfer',
            'seller_id': self.seller.id,
            'subscriber_id': self.buyer.id,
            'security_class_id': self.share_class.id,
            'securities': 30,
            'security_price': 12.0,
            'date': '2021-01-01',
            'state': 'confirmed',
        })

        sell.write({'state': 'done'})

        allocations = self.LotAllocation.search([('sell_transaction_id', '=', sell.id)])
        self.assertEqual(len(allocations), 1)
        self.assertTrue(sell.disposal_processed)
        self.assertAlmostEqual(sell.realized_gain_loss, 60.0)
