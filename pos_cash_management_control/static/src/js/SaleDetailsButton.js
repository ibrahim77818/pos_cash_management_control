odoo.define('pos_cash_management_control.SaleDetailsButton', function (require) {
    'use strict';

    const SaleDetailsButton = require('point_of_sale.SaleDetailsButton');
    const Registries = require('point_of_sale.Registries');

    const PcmSaleDetailsButton = (SaleDetailsButton) => class PcmSaleDetailsButton extends SaleDetailsButton {
        //@override
        async onClick() {
            if (!this.env.pos.pcmCanSeeDailySale()) {
                await this.showPopup('ErrorPopup', {
                    title: this.env._t('Access Denied'),
                    body: this.env._t('Only Point of Sale managers can view the Daily Sale report.'),
                });
                return;
            }
            return super.onClick(...arguments);
        }
    };

    Registries.Component.extend(SaleDetailsButton, PcmSaleDetailsButton);

    return PcmSaleDetailsButton;
});
