# -*- coding: utf-8 -*-

from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestEquityPortalMatchSecurity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.seller = cls.env['res.partner'].create({
            'name': 'Seller Portal',
            'email': 'seller.portal@example.com',
        })
        cls.buyer = cls.env['res.partner'].create({
            'name': 'Buyer Portal',
            'email': 'buyer.portal@example.com',
        })
        cls.intruder = cls.env['res.partner'].create({
            'name': 'Intruder Portal',
            'email': 'intruder.portal@example.com',
        })
        cls.share_class = cls.env['equity.security.class'].create({
            'name': 'Class A Portal',
            'class_type': 'shares',
        })
        cls.portal_user = cls.env['res.users'].create({
            'name': 'Buyer Portal User',
            'login': 'buyer.portal.user@example.com',
            'email': 'buyer.portal@example.com',
            'groups_id': [(6, 0, [cls.env.ref('base.group_portal').id])],
            'partner_id': cls.buyer.id,
        })
        cls.Listing = cls.env['equity.marketplace.board']

    def _create_published_sell_listing(self):
        listing = self.Listing.create({
            'listing_type': 'sell',
            'shareholder_id': self.seller.id,
            'share_class_id': self.share_class.id,
            'qty': 10,
            'price_unit': 100.0,
            'company_id': self.company.id,
        })
        listing.action_publish()
        listing.write({'rofr_deadline': '2000-01-01'})
        return listing

    def test_portal_match_ignores_spoofed_counterparty_id(self):
        listing = self._create_published_sell_listing()
        listing_as_portal = listing.with_user(self.portal_user)

        transaction = listing_as_portal.action_portal_match_and_create_transaction(
            self.intruder.id,
        )

        self.assertEqual(listing.matched_partner_id, self.buyer)
        self.assertEqual(transaction.subscriber_id, self.buyer)
        self.assertNotEqual(transaction.subscriber_id, self.intruder)

    def test_portal_user_cannot_match_own_listing(self):
        seller_user = self.env['res.users'].create({
            'name': 'Seller Portal User',
            'login': 'seller.portal.user@example.com',
            'email': 'seller.portal@example.com',
            'groups_id': [(6, 0, [self.env.ref('base.group_portal').id])],
            'partner_id': self.seller.id,
        })
        listing = self._create_published_sell_listing()
        listing.write({'rofr_deadline': '2000-01-01'})

        with self.assertRaises(AccessError):
            listing.with_user(seller_user).action_portal_match_and_create_transaction(
                seller_user.partner_id.id,
            )
