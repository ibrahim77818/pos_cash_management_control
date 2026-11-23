odoo.define('pos_cash_management_control.CashOpeningPopup', function (require) {
    'use strict';

    const CashOpeningPopup = require('point_of_sale.CashOpeningPopup');
    const Registries = require('point_of_sale.Registries');

    const { onMounted } = owl;

    const PcmCashOpeningPopup = (CashOpeningPopup) => class PcmCashOpeningPopup extends CashOpeningPopup {
        setup() {
            super.setup();
            const settings = this.env.pos.pcmSettings();
            this.pcmOpeningMode = settings.opening_mode;
            this.pcmFloatAmount = settings.float_amount || 0;
            this.pcmLastClosing = this.env.pos.pos_session.cash_register_balance_start || 0;
            if (this.pcmOpeningMode === 'suggest_float') {
                this.state.openingCash = this.pcmFloatAmount;
                // Registered after the core hook, so it overrides the value
                // pre-filled with the last closing balance.
                onMounted(() => {
                    const input = this.openingCashInputRef.el;
                    if (input) {
                        input.value = this.env.pos.format_currency_no_symbol(this.pcmFloatAmount);
                    }
                });
            }
        }
        get pcmIsSuggestFloat() {
            return this.pcmOpeningMode === 'suggest_float';
        }
        get pcmFloatGap() {
            return this.env.pos.round_decimals_currency(this.pcmFloatAmount - this.pcmLastClosing);
        }
        //@override
        async confirm() {
            if (this.pcmOpeningMode !== 'standard') {
                const amount = this.state.openingCash;
                if (typeof amount !== 'number' || isNaN(amount) || amount < 0) {
                    await this.showPopup('ErrorPopup', {
                        title: this.env._t('Invalid Opening Cash'),
                        body: this.env._t('The opening cash amount must be a positive number.'),
                    });
                    return;
                }
            }
            return super.confirm(...arguments);
        }
    };

    Registries.Component.extend(CashOpeningPopup, PcmCashOpeningPopup);

    return PcmCashOpeningPopup;
});
