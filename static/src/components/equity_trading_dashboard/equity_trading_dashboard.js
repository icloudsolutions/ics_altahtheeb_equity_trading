/** @odoo-module **/

import { Component, useState, onWillStart, useRef, useEffect, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

// ── State presentation maps ───────────────────────────────────────────────────

const STATE_COLORS = {
    draft:        "#adb5bd",
    published:    "#0d6efd",
    matched:      "#fd7e14",
    approved:     "#6f42c1",
    sign_process: "#ffc107",
    done:         "#198754",
    cancelled:    "#dc3545",
};

function stateLabel(state) {
    const MAP = {
        draft:        _t("Draft"),
        published:    _t("Published"),
        matched:      _t("Matched"),
        approved:     _t("Approved"),
        sign_process: _t("Signing"),
        done:         _t("Done"),
        cancelled:    _t("Cancelled"),
    };
    return MAP[state] || state;
}

function stateBadgeClass(state) {
    const MAP = {
        draft:        "bg-secondary",
        published:    "bg-primary",
        matched:      "bg-warning text-dark",
        approved:     "bg-info text-dark",
        sign_process: "bg-warning text-dark",
        done:         "bg-success",
        cancelled:    "bg-danger",
    };
    return "badge rounded-pill " + (MAP[state] || "bg-secondary");
}

function listingTypeLabel(type) {
    return type === "sell" ? _t("Sell") : _t("Buy");
}

function listingTypeBadge(type) {
    return type === "sell"
        ? "badge rounded-pill bg-danger-subtle text-danger border border-danger-subtle"
        : "badge rounded-pill bg-success-subtle text-success border border-success-subtle";
}

function formatValue(amount, symbol) {
    if (!amount) return `0 ${symbol || ""}`;
    if (amount >= 1_000_000) return `${(amount / 1_000_000).toFixed(1)}M ${symbol || ""}`;
    if (amount >= 1_000)     return `${(amount / 1_000).toFixed(1)}K ${symbol || ""}`;
    return `${Number(amount).toFixed(2)} ${symbol || ""}`;
}

// ── Dashboard component ───────────────────────────────────────────────────────

export class EquityTradingDashboard extends Component {
    static template = "ics_altahtheeb_equity_trading.EquityTradingDashboard";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm           = useService("orm");
        this.actionService = useService("action");
        this.notification  = useService("notification");

        this.stateChartRef    = useRef("stateChart");
        this.activityChartRef = useRef("activityChart");

        // ── UI state ─────────────────────────────────────────────────────────
        this.ui = useState({ loading: true, refreshing: false, filtersOpen: false });

        // ── Active filters ────────────────────────────────────────────────────
        this.filters = useState({
            company_id:   false,   // false = current company
            listing_type: "all",
            state_filter: "all",
            period:       "all",
        });

        // ── Filter option lists (populated from server) ───────────────────────
        this.filterOptions = useState({
            companies:     [],
            listing_types: [],
            states:        [],
            periods:       [],
        });

        // ── Dashboard data ────────────────────────────────────────────────────
        this.data = useState({
            kpi:             { active_listings: 0, pending_signatures: 0, portfolio_value: 0, portfolio_currency: "SAR", completed_trades_month: 0 },
            listing_states:  [],
            weekly_activity: [],
            recent_listings: [],
            pending_actions: [],
            company_name:    "",
        });

        onWillStart(async () => {
            document.body.classList.add("o_equity_trading_dashboard_active");
            await loadJS("/web/static/lib/Chart/Chart.js");
            await this._loadFilterOptions();
            await this._fetchData();
        });

        useEffect(
            () => { if (!this.ui.loading) this._renderCharts(); },
            () => [this.ui.loading, this.ui.refreshing]
        );

        onWillUnmount(() => {
            document.body.classList.remove("o_equity_trading_dashboard_active");
            this._destroyChart("_stateChart");
            this._destroyChart("_activityChart");
        });
    }

    // ── Filter options ────────────────────────────────────────────────────────

    async _loadFilterOptions() {
        const opts = await this.orm.call(
            "equity.marketplace.board", "get_filter_options", []
        );
        Object.assign(this.filterOptions, opts);
    }

    // ── Data loading ──────────────────────────────────────────────────────────

    async _fetchData() {
        const raw = await this.orm.call(
            "equity.marketplace.board",
            "get_dashboard_data",
            [{ ...this.filters }]
        );
        Object.assign(this.data, raw);
        this.ui.loading = false;
    }

    async refresh() {
        this.ui.refreshing = true;
        this._destroyChart("_stateChart");
        this._destroyChart("_activityChart");
        try {
            await this._fetchData();
        } finally {
            this.ui.refreshing = false;
        }
        this.notification.add(_t("Dashboard refreshed"), { type: "success", sticky: false });
    }

    // ── Filter handlers ───────────────────────────────────────────────────────

    async applyFilter(key, value) {
        this.filters[key] = value;
        this.ui.refreshing = true;
        this._destroyChart("_stateChart");
        this._destroyChart("_activityChart");
        try {
            await this._fetchData();
        } finally {
            this.ui.refreshing = false;
        }
    }

    async clearFilters() {
        Object.assign(this.filters, {
            company_id:   false,
            listing_type: "all",
            state_filter: "all",
            period:       "all",
        });
        await this.refresh();
    }

    get hasActiveFilters() {
        return (
            this.filters.company_id !== false ||
            this.filters.listing_type !== "all" ||
            this.filters.state_filter !== "all" ||
            this.filters.period !== "all"
        );
    }

    get activeFilterCount() {
        let count = 0;
        if (this.filters.company_id !== false)   count++;
        if (this.filters.listing_type !== "all") count++;
        if (this.filters.state_filter !== "all") count++;
        if (this.filters.period !== "all")       count++;
        return count;
    }

    get showFilterBadge() {
        return this.activeFilterCount > 0;
    }

    get companyDisplayName() {
        return this.data.company_name || "Al Tahtheeb";
    }

    get pendingSignaturesWarningClass() {
        return this.data.kpi.pending_signatures > 0 ? "text-warning" : "";
    }

    get hasListingChartData() {
        return this.data.listing_states.some((row) => row.count > 0);
    }

    get selectedCompanyName() {
        if (!this.filters.company_id) return _t("All Companies");
        const c = this.filterOptions.companies.find(c => c.id === this.filters.company_id);
        return c ? c.name : _t("All Companies");
    }

    get selectedPeriodLabel() {
        const p = this.filterOptions.periods.find(p => p.value === this.filters.period);
        return p ? p.label : _t("All Time");
    }

    toggleFiltersPanel() {
        this.ui.filtersOpen = !this.ui.filtersOpen;
    }

    onCompanyFilterChange(ev) {
        const value = ev.target.value;
        this.applyFilter("company_id", value ? parseInt(value, 10) : false);
    }

    onStateFilterChange(ev) {
        this.applyFilter("state_filter", ev.target.value);
    }

    onPeriodFilterChange(ev) {
        this.applyFilter("period", ev.target.value);
    }

    onListingTypeFilterClick(ev) {
        const value = ev.currentTarget.dataset.value;
        if (value) {
            this.applyFilter("listing_type", value);
        }
    }

    clearCompanyFilter() {
        this.applyFilter("company_id", false);
    }

    clearListingTypeFilter() {
        this.applyFilter("listing_type", "all");
    }

    clearStateFilter() {
        this.applyFilter("state_filter", "all");
    }

    clearPeriodFilter() {
        this.applyFilter("period", "all");
    }

    onListingRowClick(ev) {
        const listingId = parseInt(ev.currentTarget.dataset.id, 10);
        if (listingId) {
            this.openListing(listingId);
        }
    }

    listingTypeButtonClass(value) {
        const active = this.filters.listing_type === value;
        return "btn btn-sm " + (active ? "btn-primary" : "btn-outline-secondary");
    }

    // ── Chart rendering ───────────────────────────────────────────────────────

    _destroyChart(key) {
        if (this[key]) {
            this[key].destroy();
            this[key] = null;
        }
    }

    _renderCharts() {
        this._renderStateChart();
        this._renderActivityChart();
    }

    _renderStateChart() {
        const canvas = this.stateChartRef.el;
        if (!canvas) return;
        this._destroyChart("_stateChart");

        const rows = this.data.listing_states.filter(r => r.count > 0);
        if (!rows.length) return;

        this._stateChart = new window.Chart(canvas.getContext("2d"), {
            type: "doughnut",
            data: {
                labels:   rows.map(r => stateLabel(r.state)),
                datasets: [{
                    data:            rows.map(r => r.count),
                    backgroundColor: rows.map(r => STATE_COLORS[r.state] || "#6c757d"),
                    borderWidth:     2,
                    borderColor:     "#fff",
                    hoverOffset:     6,
                }],
            },
            options: {
                responsive:          true,
                maintainAspectRatio: false,
                cutout:              "68%",
                plugins: {
                    legend: {
                        position: "right",
                        labels: { boxWidth: 10, padding: 14, font: { size: 12 } },
                    },
                    tooltip: {
                        callbacks: {
                            label: ctx => `  ${ctx.label}: ${ctx.raw}`,
                        },
                    },
                },
            },
        });
    }

    _renderActivityChart() {
        const canvas = this.activityChartRef.el;
        if (!canvas) return;
        this._destroyChart("_activityChart");

        const rows = this.data.weekly_activity;
        this._activityChart = new window.Chart(canvas.getContext("2d"), {
            type: "bar",
            data: {
                labels:   rows.map(r => r.label),
                datasets: [{
                    label:           _t("New Listings"),
                    data:            rows.map(r => r.count),
                    backgroundColor: "rgba(13,110,253,0.65)",
                    borderColor:     "#0d6efd",
                    borderWidth:     1,
                    borderRadius:    4,
                    borderSkipped:   false,
                }],
            },
            options: {
                responsive:          true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks:  { stepSize: 1, precision: 0, font: { size: 11 } },
                        grid:   { color: "rgba(0,0,0,0.06)" },
                        border: { display: false },
                    },
                    x: {
                        grid:   { display: false },
                        ticks:  { font: { size: 11 } },
                        border: { display: false },
                    },
                },
            },
        });
    }

    // ── Template helpers ──────────────────────────────────────────────────────

    stateBadgeClass(state)    { return stateBadgeClass(state); }
    stateLabel(state)         { return stateLabel(state); }
    listingTypeBadge(type)    { return listingTypeBadge(type); }
    listingTypeLabel(type)    { return listingTypeLabel(type); }
    formatValue(amount, sym)  { return formatValue(amount, sym); }

    get portfolioValueFormatted() {
        const { portfolio_value, portfolio_currency } = this.data.kpi;
        return formatValue(portfolio_value, portfolio_currency);
    }

    get totalListings() {
        return this.data.listing_states.reduce((s, r) => s + r.count, 0);
    }

    // ── Navigation ────────────────────────────────────────────────────────────

    openMarketplace()  { this.actionService.doAction("ics_altahtheeb_equity_trading.equity_marketplace_board_action"); }
    openPortfolio()    { this.actionService.doAction("ics_altahtheeb_equity_trading.equity_portfolio_asset_action"); }
    openTradeOrders()  { this.actionService.doAction("ics_altahtheeb_equity_trading.equity_trade_order_action"); }
    openRevaluations() { this.actionService.doAction("ics_altahtheeb_equity_trading.equity_portfolio_revaluation_action"); }

    openListing(id) {
        this.actionService.doAction({
            type:      "ir.actions.act_window",
            res_model: "equity.marketplace.board",
            res_id:    id,
            views:     [[false, "form"]],
            target:    "current",
        });
    }
}

registry.category("actions").add(
    "ics_altahtheeb_equity_trading.EquityTradingDashboard",
    EquityTradingDashboard
);
