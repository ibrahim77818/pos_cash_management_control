/** @odoo-module */

import { SaleDetailsButton } from "@point_of_sale/app/navbar/sale_details_button/sale_details_button";
import { Navbar } from "@point_of_sale/app/navbar/navbar";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

function pcmDenyDailySale(component) {
    if (component.pos.pcmCanSeeDailySale()) {
        return false;
    }
    component.dialog.add(AlertDialog, {
        title: _t("Access Denied"),
        body: _t("Only Point of Sale managers can view the Daily Sale report."),
    });
    return true;
}

patch(SaleDetailsButton.prototype, {
    //@override
    async onClick() {
        if (pcmDenyDailySale(this)) {
            return;
        }
        return super.onClick(...arguments);
    },
});

patch(Navbar.prototype, {
    //@override
    async showSaleDetails() {
        if (pcmDenyDailySale(this)) {
            return;
        }
        return super.showSaleDetails(...arguments);
    },
});
