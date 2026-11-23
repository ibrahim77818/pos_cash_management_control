/** @odoo-module */

import { OpeningControlPopup } from "@point_of_sale/app/store/opening_control_popup/opening_control_popup";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { patch } from "@web/core/utils/patch";
import { parseFloat } from "@web/views/fields/parsers";
import { _t } from "@web/core/l10n/translation";

patch(OpeningControlPopup.prototype, {
    setup() {
        super.setup(...arguments);
        const settings = this.pos.pcmSettings();
        this.pcmOpeningMode = settings.opening_mode;
        this.pcmFloatAmount = settings.float_amount || 0;
        this.pcmLastClosing = this.pos.session.cash_register_balance_start || 0;
        if (this.pcmOpeningMode === "suggest_float") {
            this.state.openingCash = this.env.utils.formatCurrency(this.pcmFloatAmount, false);
        }
    },
    get pcmIsSuggestFloat() {
        return this.pcmOpeningMode === "suggest_float";
    },
    get pcmIsFirstSessionFallback() {
        return (
            this.pos.pcmSettings().configured_opening_mode === "server_last_closing" &&
            this.pcmOpeningMode === "standard"
        );
    },
    get pcmFloatGap() {
        return !this.env.utils.floatIsZero(this.pcmFloatAmount - this.pcmLastClosing);
    },
    //@override
    async confirm() {
        if (this.pcmOpeningMode !== "standard" && this.cashMethodCount) {
            let amount = NaN;
            try {
                amount = parseFloat(this.state.openingCash);
            } catch {
                amount = NaN;
            }
            if (isNaN(amount) || amount < 0) {
                this.dialog.add(AlertDialog, {
                    title: _t("Invalid Opening Cash"),
                    body: _t("The opening cash amount must be a positive number."),
                });
                return;
            }
        }
        return super.confirm(...arguments);
    },
});
