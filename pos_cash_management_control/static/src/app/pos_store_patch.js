/** @odoo-module */

import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";

const DEFAULT_SETTINGS = Object.freeze({
    opening_mode: "standard",
    configured_opening_mode: "standard",
    closing_mode: "standard",
    float_amount: 0,
    hide_daily_sale: false,
    opened_by_server: false,
    user_is_manager: false,
});

patch(PosStore.prototype, {
    /**
     * Cash control settings are cached once from the session data already
     * returned by `load_data`: opening and closing screens never need an
     * extra RPC.
     */
    async processServerData() {
        await super.processServerData(...arguments);
        this.pcmCashControl = Object.freeze(
            Object.assign({}, DEFAULT_SETTINGS, (this.session && this.session._pcm_cash_control) || {})
        );
    },
    pcmSettings() {
        return this.pcmCashControl || DEFAULT_SETTINGS;
    },
    /**
     * Manager role of whoever operates the POS: the employee with pos_hr,
     * otherwise the logged-in user (role computed by the server).
     */
    pcmIsManager() {
        const cashier = this.getCashier && this.getCashier();
        return Boolean(cashier && (cashier._role || cashier.role) === "manager");
    },
    pcmIsBlindClosing() {
        return (
            Boolean(this.config && this.config.cash_control) &&
            this.pcmSettings().closing_mode === "blind" &&
            !this.pcmIsManager()
        );
    },
    pcmIsServerClosingCount() {
        return (
            Boolean(this.config && this.config.cash_control) &&
            this.pcmSettings().closing_mode === "server_no_count"
        );
    },
    pcmCanSeeDailySale() {
        return !this.pcmSettings().hide_daily_sale || this.pcmIsManager();
    },
});
