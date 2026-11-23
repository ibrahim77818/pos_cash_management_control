/** @odoo-module */

import { PosStore } from "@point_of_sale/app/store/pos_store";
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
     * Cash control settings are cached once from the data already returned by
     * `load_pos_data`: opening and closing screens never need an extra RPC.
     */
    async _processData(loadedData) {
        await super._processData(...arguments);
        this.pcmCashControl = Object.freeze(
            Object.assign({}, DEFAULT_SETTINGS, loadedData["pcm_cash_control"] || {})
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
        const cashier = this.get_cashier && this.get_cashier();
        return Boolean(cashier && cashier.role === "manager");
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
