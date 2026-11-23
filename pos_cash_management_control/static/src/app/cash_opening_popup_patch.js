/** @odoo-module */

import { CashOpeningPopup } from "@point_of_sale/app/store/cash_opening_popup/cash_opening_popup";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { patch } from "@web/core/utils/patch";
import { parseFloat } from "@web/views/fields/parsers";
import { _t } from "@web/core/l10n/translation";

patch(CashOpeningPopup.prototype, {
    setup() {
        super.setup(...arguments);
        const settings = this.pos.pcmSettings();
        this.pcmOpeningMode = settings.opening_mode;
        this.pcmFloatAmount = settings.float_amount || 0;
        this.pcmLastClosing = this.pos.pos_session.cash_register_balance_start || 0;
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
        if (this.pcmOpeningMode !== "standard") {
            let amount = NaN;
            try {
                amount = parseFloat(this.state.openingCash);
            } catch {
                amount = NaN;
            }
            if (isNaN(amount) || amount < 0) {
                await this.popup.add(ErrorPopup, {
                    title: _t("Invalid Opening Cash"),
                    body: _t("The opening cash amount must be a positive number."),
                });
                return;
            }
        }
        return super.confirm(...arguments);
    },
});
