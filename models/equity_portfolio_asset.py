# -*- coding: utf-8 -*-
"""
equity.portfolio.asset
======================
Tracks live market prices per share class for portfolio valuation and
marketplace reference pricing.

Market feed sync architecture
-----------------------------
External price feeds (or manual staging) set ``feed_target_price`` and move
the row to ``sync_state='pending'``.  The scheduled action
``_cron_sync_market_feed_prices`` processes the queue one row at a time using:

1. ``SELECT … FOR UPDATE NOWAIT`` — fail fast when a portal/cron worker already
   holds the row lock instead of blocking the whole batch.
2. **Isolated cursor commits** — each successful (or retry-queued) row is
   committed in its own database transaction so a later failure cannot roll
   back earlier successes.
3. **Retry queue** — ``psycopg2.errors.LockNotAvailable`` (SQLSTATE ``55P03``)
   increments ``sync_attempts``, schedules ``sync_retry_at`` with exponential
   backoff, and leaves the target price intact for the next cron pass.
"""

import logging
from datetime import timedelta

from psycopg2 import OperationalError
from psycopg2 import errors as pg_errors

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL
from odoo.tools.float_utils import float_compare, float_is_zero

from . import tools

_logger = logging.getLogger(__name__)

MARKET_FEED_BATCH_SIZE_PARAM = 'ics_altahtheeb_equity_trading.market_feed_batch_size'
MARKET_FEED_MAX_RETRIES_PARAM = 'ics_altahtheeb_equity_trading.market_feed_max_retries'
MARKET_FEED_RETRY_BASE_MINUTES_PARAM = (
    'ics_altahtheeb_equity_trading.market_feed_retry_base_minutes'
)
DEFAULT_MARKET_FEED_BATCH_SIZE = 50
DEFAULT_MARKET_FEED_MAX_RETRIES = 8
DEFAULT_MARKET_FEED_RETRY_BASE_MINUTES = 5
REVALUATION_AMOUNT_PRECISION = 2
REVALUATION_QTY_PRECISION = 6

SYNC_STATE_SELECTION = [
    ('synced', 'Synced'),
    ('pending', 'Pending'),
    ('retry', 'Queued for Retry'),
    ('failed', 'Failed'),
]


class EquityPortfolioAsset(models.Model):
    _name = 'equity.portfolio.asset'
    _description = 'Equity Portfolio Asset'
    _inherit = ['mail.thread']
    _order = 'company_id, share_class_id'
    _rec_name = 'display_name'

    company_id = fields.Many2one(
        comodel_name='res.company',
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    share_class_id = fields.Many2one(
        comodel_name='equity.security.class',
        string="Share Class",
        required=True,
        domain=[('class_type', '=', 'shares')],
        index=True,
        tracking=True,
    )
    display_name = fields.Char(
        compute='_compute_display_name',
        store=True,
    )
    market_price = fields.Float(
        string="Market Price",
        digits='Product Price',
        tracking=True,
        help="Last successfully applied market price from the feed.",
    )
    feed_target_price = fields.Float(
        string="Pending Feed Price",
        digits='Product Price',
        copy=False,
        help="Price staged by the market feed awaiting cron application.",
    )
    sync_state = fields.Selection(
        selection=SYNC_STATE_SELECTION,
        string="Sync State",
        default='synced',
        required=True,
        index=True,
        tracking=True,
    )
    sync_retry_at = fields.Datetime(
        string="Next Retry At",
        copy=False,
        index=True,
    )
    sync_attempts = fields.Integer(
        string="Sync Attempts",
        default=0,
        copy=False,
    )
    sync_last_error = fields.Text(
        string="Last Sync Error",
        copy=False,
    )
    last_market_sync = fields.Datetime(
        string="Last Market Sync",
        copy=False,
        readonly=True,
    )
    active = fields.Boolean(default=True)
    investment_fund_id = fields.Many2one(
        comodel_name='equity.investment.fund',
        string="Investment Fund",
        index=True,
        tracking=True,
        check_company=True,
        help="Fund whose GL accounts and analytic axis are used for period-end revaluation.",
    )
    book_value_account_id = fields.Many2one(
        comodel_name='account.account',
        string="Book Value Account",
        check_company=True,
        tracking=True,
        help="Optional override of the fund book value account for this position.",
    )
    current_quantity = fields.Float(
        string="Current Quantity",
        compute='_compute_revaluation_metrics',
        digits=(16, REVALUATION_QTY_PRECISION),
        help="Live share quantity from the equity cap table.",
    )
    current_market_value = fields.Monetary(
        string="Current Market Value",
        compute='_compute_revaluation_metrics',
        currency_field='currency_id',
    )
    current_book_value = fields.Monetary(
        string="Current Book Value",
        compute='_compute_revaluation_metrics',
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id',
        store=True,
        readonly=True,
    )
    zakat_classification = fields.Selection(
        related='share_class_id.zakat_classification',
        store=True,
        readonly=True,
        index=True,
    )
    zakat_base_contribution_value = fields.Monetary(
        string="Zakat Base Contribution Value",
        compute='_compute_zakat_metrics',
        store=True,
        currency_field='currency_id',
        help=(
            "Amount this holding contributes to the Zakat base (ZB) under "
            "GAZT/ZATCA rules for listed shares:\n"
            "• Trading — year-end fair market value (quantity × market price).\n"
            "• Strategic / Long-term — zero (deduction handled separately on "
            "the zakat declaration)."
        ),
    )
    zakat_deductible_value = fields.Monetary(
        string="Zakat Deductible Value",
        compute='_compute_zakat_metrics',
        store=True,
        currency_field='currency_id',
        help=(
            "Book value eligible for deduction from the Zakat base when the "
            "holding is classified as strategic/long-term and investee zakat "
            "conditions are satisfied."
        ),
    )

    _company_share_class_unique = models.Constraint(
        'UNIQUE(company_id, share_class_id)',
        _('Each share class may only have one portfolio asset per company.'),
    )

    @api.depends('company_id', 'share_class_id')
    def _compute_display_name(self):
        for asset in self:
            company = asset.company_id.display_name or ''
            share_class = asset.share_class_id.display_name or ''
            asset.display_name = f'{company} — {share_class}' if share_class else company

    @api.depends(
        'share_class_id',
        'market_price',
        'investment_fund_id',
        'investment_fund_id.holder_ids',
        'book_value_account_id',
    )
    def _compute_revaluation_metrics(self):
        today = fields.Date.context_today(self)
        for asset in self:
            quantity = asset._get_holdings_quantity()
            asset.current_quantity = quantity
            asset.current_market_value = quantity * asset.market_price
            asset.current_book_value = asset._get_allocated_book_value(today)

    @api.depends(
        'zakat_classification',
        'share_class_id',
        'market_price',
        'investment_fund_id',
        'investment_fund_id.holder_ids',
        'book_value_account_id',
    )
    def _compute_zakat_metrics(self):
        """
        Compute GAZT/ZATCA Zakat base figures for Tadawul-listed holdings.

        Trading investments are included in the Zakat base at fair market
        value.  Strategic/long-term investments are not added to the base;
        their book value may be reported as a deductible amount instead.
        """
        today = fields.Date.context_today(self)
        for asset in self:
            quantity = asset._get_holdings_quantity()
            market_value = quantity * asset.market_price
            book_value = asset._get_allocated_book_value(today)
            if asset.zakat_classification == 'short_term_trading':
                asset.zakat_base_contribution_value = market_value
                asset.zakat_deductible_value = 0.0
            else:
                asset.zakat_base_contribution_value = 0.0
                asset.zakat_deductible_value = book_value

    # -------------------------------------------------------------------------
    # Period-end revaluation helpers
    # -------------------------------------------------------------------------

    def _get_book_value_account(self):
        self.ensure_one()
        if self.book_value_account_id:
            return self.book_value_account_id
        fund = self.investment_fund_id
        return fund.book_value_account_id if fund else self.env['account.account']

    @api.model
    def _get_account_balance(self, account, company, analytic_account, date_to):
        """Return posted GL balance for an account, optionally filtered by analytic axis."""
        if not account:
            return 0.0
        domain = [
            ('account_id', '=', account.id),
            ('company_id', '=', company.id),
            ('date', '<=', date_to),
            ('parent_state', '=', 'posted'),
        ]
        lines = self.env['account.move.line'].search(domain)
        if analytic_account:
            analytic_key = str(analytic_account.id)
            lines = lines.filtered(
                lambda line, key=analytic_key: key in (line.analytic_distribution or {})
            )
        return sum(lines.mapped('balance'))

    def _get_holdings_quantity(self):
        """Sum cap-table shares for this asset, optionally scoped to fund holders."""
        self.ensure_one()
        CapTable = self.env['equity.cap.table'].sudo()
        domain = [
            ('security_class_id', '=', self.share_class_id.id),
            ('securities_type', '=', 'shares'),
            ('securities', '>', 0),
        ]
        fund = self.investment_fund_id
        if fund and fund.holder_ids:
            domain.append(('holder_id', 'in', fund.holder_ids.ids))
        return sum(CapTable.search(domain).mapped('securities'))

    def _get_peer_assets_for_book_allocation(self):
        """Other portfolio assets sharing the same fund book value account."""
        self.ensure_one()
        account = self._get_book_value_account()
        if not account or not self.investment_fund_id:
            return self
        return self.search([
            ('company_id', '=', self.company_id.id),
            ('investment_fund_id', '=', self.investment_fund_id.id),
            ('active', '=', True),
            ('market_price', '>', 0),
            '|',
            ('book_value_account_id', '=', account.id),
            '&',
            ('book_value_account_id', '=', False),
            ('investment_fund_id.book_value_account_id', '=', account.id),
        ])

    def _get_allocated_book_value(self, as_of_date):
        """
        Book value allocated to this position.

        When several positions share the same book value account, the posted
        balance is split proportionally to each position's fair market value.
        """
        self.ensure_one()
        fund = self.investment_fund_id
        account = self._get_book_value_account()
        if not fund or not account:
            return 0.0

        total_balance = self._get_account_balance(
            account,
            self.company_id,
            fund.analytic_account_id,
            as_of_date,
        )
        peers = self._get_peer_assets_for_book_allocation()
        peer_market_values = {
            peer.id: peer._get_holdings_quantity() * peer.market_price
            for peer in peers
        }
        total_market_value = sum(peer_market_values.values())
        if float_is_zero(total_market_value, precision_digits=REVALUATION_AMOUNT_PRECISION):
            return total_balance if len(peers) == 1 else 0.0
        if len(peers) == 1:
            return total_balance
        return total_balance * (peer_market_values[self.id] / total_market_value)

    def _prepare_revaluation_payload(self, revaluation_date):
        """
        Build values for ``equity.portfolio.revaluation.line`` creation.

        Returns ``None`` when the position is inactive or not configured.
        """
        self.ensure_one()
        fund = self.investment_fund_id
        if not fund or not self.active:
            return None

        quantity = self._get_holdings_quantity()
        if float_compare(quantity, 0.0, precision_digits=REVALUATION_QTY_PRECISION) <= 0:
            return None

        market_price = self.market_price
        if float_compare(market_price, 0.0, precision_digits=REVALUATION_AMOUNT_PRECISION) <= 0:
            return None

        market_value = quantity * market_price
        book_value = self._get_allocated_book_value(revaluation_date)
        adjustment_amount = market_value - book_value

        return {
            'portfolio_asset_id': self.id,
            'investment_fund_id': fund.id,
            'share_class_id': self.share_class_id.id,
            'quantity': quantity,
            'market_price': market_price,
            'market_value': market_value,
            'book_value': book_value,
            'adjustment_amount': adjustment_amount,
        }

    # -------------------------------------------------------------------------
    # Configuration helpers
    # -------------------------------------------------------------------------

    @api.model
    def _get_market_feed_batch_size(self):
        icp = self.env['ir.config_parameter'].sudo()
        raw = icp.get_param(MARKET_FEED_BATCH_SIZE_PARAM, str(DEFAULT_MARKET_FEED_BATCH_SIZE))
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return DEFAULT_MARKET_FEED_BATCH_SIZE

    @api.model
    def _get_market_feed_max_retries(self):
        icp = self.env['ir.config_parameter'].sudo()
        raw = icp.get_param(MARKET_FEED_MAX_RETRIES_PARAM, str(DEFAULT_MARKET_FEED_MAX_RETRIES))
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return DEFAULT_MARKET_FEED_MAX_RETRIES

    @api.model
    def _get_market_feed_retry_base_minutes(self):
        icp = self.env['ir.config_parameter'].sudo()
        raw = icp.get_param(
            MARKET_FEED_RETRY_BASE_MINUTES_PARAM,
            str(DEFAULT_MARKET_FEED_RETRY_BASE_MINUTES),
        )
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return DEFAULT_MARKET_FEED_RETRY_BASE_MINUTES

    # -------------------------------------------------------------------------
    # Locking helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _is_lock_not_available(exc):
        """Return True when Postgres could not acquire a row lock immediately."""
        if isinstance(exc, pg_errors.LockNotAvailable):
            return True
        if isinstance(exc, OperationalError) and getattr(exc, 'pgcode', None) == '55P03':
            return True
        cause = getattr(exc, '__cause__', None)
        if cause and cause is not exc:
            return EquityPortfolioAsset._is_lock_not_available(cause)
        return False

    def _lock_row_nowait(self):
        """Acquire a row-level lock or raise LockNotAvailable immediately."""
        self.ensure_one()
        self.env.cr.execute(SQL(
            "SELECT id FROM %s WHERE id = %s FOR UPDATE NOWAIT",
            SQL.identifier(self._table),
            self.id,
        ))
        return bool(self.env.cr.fetchone())

    def _compute_retry_datetime(self, attempts):
        """Exponential backoff capped at six hours."""
        base_minutes = self._get_market_feed_retry_base_minutes()
        delay_minutes = min(base_minutes * (2 ** max(attempts - 1, 0)), 360)
        return fields.Datetime.now() + timedelta(minutes=delay_minutes)

    # -------------------------------------------------------------------------
    # Feed staging API (called by integrations / server actions)
    # -------------------------------------------------------------------------

    @api.model
    def stage_market_feed_prices(self, feed_rows):
        """
        Stage one or more market prices for asynchronous cron application.

        ``feed_rows`` is a list of dicts::

            {
                'company_id': int,          # optional, defaults to env.company
                'share_class_id': int,      # required
                'price': float,             # required, must be >= 0
            }
        """
        if not feed_rows:
            return self.browse()

        staged_assets = self.browse()
        for row in feed_rows:
            share_class_id = row.get('share_class_id')
            price = row.get('price')
            if not share_class_id:
                tools.raise_bilingual_user_error(
                    "Each market feed row must include a share_class_id.",
                    "يجب أن يتضمن كل صف من تغذية السوق معرف فئة الأسهم share_class_id.",
                )
            if price is None or price < 0:
                tools.raise_bilingual_user_error(
                    "Market feed price must be zero or positive.",
                    "يجب أن يكون سعر تغذية السوق صفراً أو أكبر.",
                )

            company_id = row.get('company_id') or self.env.company.id
            asset = self.search([
                ('company_id', '=', company_id),
                ('share_class_id', '=', share_class_id),
            ], limit=1)
            if not asset:
                asset = self.create({
                    'company_id': company_id,
                    'share_class_id': share_class_id,
                    'market_price': price,
                    'feed_target_price': price,
                    'sync_state': 'pending',
                })
            else:
                asset.write({
                    'feed_target_price': price,
                    'sync_state': 'pending',
                    'sync_last_error': False,
                })
            staged_assets |= asset
        return staged_assets

    # -------------------------------------------------------------------------
    # Per-row sync (isolated transaction)
    # -------------------------------------------------------------------------

    def _resolve_target_price(self):
        self.ensure_one()
        if self.feed_target_price:
            return self.feed_target_price
        return self.market_price

    def _apply_locked_market_price(self, target_price):
        """Apply ``target_price`` after acquiring a NOWAIT row lock."""
        self.ensure_one()
        if not self._lock_row_nowait():
            return False
        self.write({
            'market_price': target_price,
            'feed_target_price': 0.0,
            'sync_state': 'synced',
            'sync_retry_at': False,
            'sync_attempts': 0,
            'sync_last_error': False,
            'last_market_sync': fields.Datetime.now(),
        })
        self.env['equity.cap.table'].invalidate_model()
        return True

    def _queue_feed_retry(self, target_price, error_message):
        """Mark the asset for a later cron attempt without losing the target price."""
        self.ensure_one()
        attempts = self.sync_attempts + 1
        max_retries = self._get_market_feed_max_retries()
        vals = {
            'feed_target_price': target_price,
            'sync_last_error': (error_message or '')[:2000],
            'sync_attempts': attempts,
        }
        if attempts >= max_retries:
            vals.update({
                'sync_state': 'failed',
                'sync_retry_at': False,
            })
            _logger.error(
                "Market feed sync permanently failed for portfolio asset %s "
                "after %s attempts: %s",
                self.id,
                attempts,
                error_message,
            )
        else:
            vals.update({
                'sync_state': 'retry',
                'sync_retry_at': self._compute_retry_datetime(attempts),
            })
            _logger.warning(
                "Market feed sync lock unavailable for portfolio asset %s; "
                "queued retry #%s at %s.",
                self.id,
                attempts,
                vals['sync_retry_at'],
            )
        self.write(vals)

    @api.model
    def _process_market_feed_update_isolated(self, asset_id):
        """
        Process one portfolio asset in an isolated DB transaction.

        Returns one of: ``synced``, ``queued``, ``skipped``, ``failed``.
        """
        dbname = self.env.cr.dbname
        uid = self.env.uid
        context = dict(self.env.context or {})

        try:
            with self.pool.cursor() as new_cr:
                env = api.Environment(new_cr, uid, context)
                Asset = env['equity.portfolio.asset'].sudo()
                asset = Asset.browse(asset_id)
                if not asset.exists() or not asset.active:
                    return 'skipped'

                if asset.sync_state == 'failed':
                    return 'failed'

                target_price = asset._resolve_target_price()
                if target_price <= 0 and asset.sync_state == 'synced':
                    return 'skipped'

                try:
                    if not asset._apply_locked_market_price(target_price):
                        new_cr.commit()
                        return 'skipped'
                except (pg_errors.LockNotAvailable, OperationalError) as exc:
                    if not Asset._is_lock_not_available(exc):
                        raise
                    asset._queue_feed_retry(target_price, str(exc))
                    new_cr.commit()
                    return 'queued'

                new_cr.commit()
                return 'synced'

        except Exception as exc:
            _logger.exception(
                "Unexpected market feed sync failure for portfolio asset %s.",
                asset_id,
            )
            try:
                with self.pool.cursor() as err_cr:
                    err_env = api.Environment(err_cr, uid, context)
                    asset = err_env['equity.portfolio.asset'].sudo().browse(asset_id)
                    if asset.exists():
                        asset._queue_feed_retry(
                            asset._resolve_target_price(),
                            str(exc),
                        )
                    err_cr.commit()
            except Exception:
                _logger.exception(
                    "Could not persist retry queue state for portfolio asset %s.",
                    asset_id,
                )
            return 'queued'

    @api.model
    def _get_market_feed_sync_candidates(self, limit=None):
        """Return portfolio assets due for market price application."""
        now = fields.Datetime.now()
        limit = limit or self._get_market_feed_batch_size()
        return self.search([
            ('active', '=', True),
            ('sync_state', 'in', ('pending', 'retry')),
            '|',
            ('sync_state', '=', 'pending'),
            ('sync_retry_at', '<=', now),
        ], limit=limit, order='sync_retry_at asc, write_date asc, id asc')

    @api.model
    def _process_market_feed_updates(self, assets=None):
        """
        Main batch processor for staged market feed prices.

        Each asset is handled in its own committed transaction so row-lock
        contention on one record cannot roll back the rest of the batch.
        """
        assets = assets or self._get_market_feed_sync_candidates()
        stats = {'synced': 0, 'queued': 0, 'skipped': 0, 'failed': 0}

        for asset in assets:
            outcome = self._process_market_feed_update_isolated(asset.id)
            stats[outcome] = stats.get(outcome, 0) + 1

        _logger.info(
            "Market feed sync batch finished: %s synced, %s queued, "
            "%s skipped, %s failed (of %s candidate(s)).",
            stats['synced'],
            stats['queued'],
            stats['skipped'],
            stats['failed'],
            len(assets),
        )
        return stats

    @api.model
    def _cron_sync_market_feed_prices(self):
        """Scheduled action entry point for market feed price application."""
        return self._process_market_feed_updates()

    def action_retry_market_feed_sync(self):
        """Manual operator action to re-queue failed assets."""
        retriable = self.filtered(lambda asset: asset.sync_state == 'failed')
        if not retriable:
            raise UserError(_("Only failed portfolio assets can be manually re-queued."))
        retriable.write({
            'sync_state': 'retry',
            'sync_attempts': 0,
            'sync_retry_at': fields.Datetime.now(),
            'sync_last_error': False,
        })
        return True
