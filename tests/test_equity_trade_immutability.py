# -*- coding: utf-8 -*-

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestEquityTradeImmutability(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.equity_company = cls.env['res.partner'].create({
            'name': 'Issuer Co',
            'is_company': True,
        })
        cls.seller = cls.env['res.partner'].create({'name': 'Seller Audit'})
        cls.buyer = cls.env['res.partner'].create({'name': 'Buyer Audit'})
        cls.share_class = cls.env['equity.security.class'].create({
            'name': 'Class A Audit',
            'class_type': 'shares',
        })
        cls.Transaction = cls.env['equity.transaction']
        cls.TradeOrder = cls.env['equity.trade.order']
        cls.AuditLog = cls.env['equity.trade.audit.log']
        cls.manager = cls.env.ref('ics_altahtheeb_equity_trading.group_equity_trading_manager').users[:1]
        if not cls.manager:
            cls.manager = cls.env.user

    def _create_transfer(self, **extra):
        vals = {
            'partner_id': self.equity_company.id,
            'transaction_type': 'transfer',
            'seller_id': self.seller.id,
            'subscriber_id': self.buyer.id,
            'security_class_id': self.share_class.id,
            'securities': 100.0,
            'security_price': 10.0,
            'company_id': self.company.id,
            'state': 'draft',
        }
        vals.update(extra)
        return self.Transaction.create(vals)

    def test_trade_order_created_with_transaction(self):
        transaction = self._create_transfer()
        order = transaction.trade_order_ids
        self.assertEqual(len(order), 1)
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.trade_side, 'sell')
        self.assertEqual(order.quantity, 100.0)

    def test_confirmed_trade_order_blocks_write_and_unlink(self):
        transaction = self._create_transfer(state='confirmed')
        order = transaction.trade_order_ids
        self.assertEqual(order.state, 'confirmed')

        with self.assertRaises(UserError):
            order.write({'quantity': 90.0})

        with self.assertRaises(UserError):
            order.unlink()

        with self.assertRaises(UserError):
            transaction.write({'securities': 90.0})

        with self.assertRaises(UserError):
            transaction.unlink()

    def test_posted_trade_order_blocks_modifications(self):
        transaction = self._create_transfer(state='signed_legal')
        order = transaction.trade_order_ids
        self.assertEqual(order.state, 'posted')

        with self.assertRaises(UserError):
            transaction.write({'security_price': 12.0})

    def test_manager_amendment_on_pending_trade_creates_signed_audit_log(self):
        transaction = self._create_transfer(state='draft')
        order = transaction.trade_order_ids

        transaction.with_user(self.manager).write({'securities': 80.0})

        audit_logs = self.AuditLog.search([('trade_order_id', '=', order.id)])
        self.assertEqual(len(audit_logs), 1)
        self.assertEqual(audit_logs.action, 'amendment')
        self.assertTrue(audit_logs.content_signature)
        self.assertTrue(
            self.AuditLog.verify_signature(
                audit_logs.snapshot_before,
                audit_logs.content_signature,
            )
        )
        self.assertIn('"quantity": 100.0', audit_logs.snapshot_before)

    def test_audit_log_is_immutable(self):
        transaction = self._create_transfer(state='draft')
        transaction.with_user(self.manager).write({'securities': 75.0})
        audit_log = self.AuditLog.search([('trade_order_id', '=', transaction.trade_order_ids.id)], limit=1)

        with self.assertRaises(UserError):
            audit_log.write({'action': 'override'})

        with self.assertRaises(UserError):
            audit_log.unlink()

    def test_system_write_bypasses_immutability_for_internal_sync(self):
        transaction = self._create_transfer(state='confirmed')
        order = transaction.trade_order_ids
        order.with_context(equity_trade_system_write=True).write({'state': 'pending'})
        self.assertEqual(order.state, 'pending')
