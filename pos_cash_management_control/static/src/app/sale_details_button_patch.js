/** @odoo-module */

import { SaleDetailsButton } from "@point_of_sale/app/navbar/sale_details_button/sale_details_button";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

patch(SaleDetailsButton.prototype, {
    //@override
    async onClick() {
        if (!this.pos.pcmCanSeeDailySale()) {
            await this.popup.add(ErrorPopup, {
                title: _t("Access Denied"),
                body: _t("Only Point of Sale managers can view the Daily Sale report."),
            });
            return;
        }
        return super.onClick(...arguments);
    },
});
