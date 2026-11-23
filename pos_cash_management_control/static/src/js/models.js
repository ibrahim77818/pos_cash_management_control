odoo.define('pos_cash_management_control.models', function (require) {
    'use strict';

    const { PosGlobalState } = require('point_of_sale.models');
    const Registries = require('point_of_sale.Registries');

    const DEFAULT_SETTINGS = {
        opening_mode: 'standard',
        closing_mode: 'standard',
        float_amount: 0,
        hide_daily_sale: false,
        configured_opening_mode: 'standard',
        opened_by_server: false,
        user_is_manager: false,
    };

    const PcmPosGlobalState = (PosGlobalState) => class PcmPosGlobalState extends PosGlobalState {
        /**
         * Cash control settings are cached once, from the data already
         * returned by `load_pos_data`: opening/closing screens never need an
         * extra RPC to know how to behave.
         */
        async _processData(loadedData) {
            await super._processData(...arguments);
            this.pcmCashControl = Object.freeze(
                Object.assign({}, DEFAULT_SETTINGS, loadedData['pcm_cash_control'] || {})
            );
        }
        pcmSettings() {
            return this.pcmCashControl || DEFAULT_SETTINGS;
        }
        /**
         * Manager role of whoever operates the POS: the employee with pos_hr,
         * otherwise the logged-in user (role computed by the server from the
         * POS manager group).
         */
        pcmIsManager() {
            const cashier = this.get_cashier && this.get_cashier();
            return Boolean(cashier && cashier.role === 'manager');
        }
        pcmIsBlindClosing() {
            return Boolean(this.config && this.config.cash_control)
                && this.pcmSettings().closing_mode === 'blind'
                && !this.pcmIsManager();
        }
        pcmIsServerClosingCount() {
            return Boolean(this.config && this.config.cash_control)
                && this.pcmSettings().closing_mode === 'server_no_count';
        }
        pcmCanSeeDailySale() {
            return !this.pcmSettings().hide_daily_sale || this.pcmIsManager();
        }
    };

    Registries.Model.extend(PosGlobalState, PcmPosGlobalState);

    return PcmPosGlobalState;
});
