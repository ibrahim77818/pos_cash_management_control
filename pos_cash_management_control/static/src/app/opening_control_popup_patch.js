import { OpeningControlPopup } from "@point_of_sale/app/components/popups/opening_control_popup/opening_control_popup";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

patch(OpeningControlPopup.prototype, {
    setup() {
        super.setup(...arguments);
        const settings = this.pos.pcmSettings();
        this.pcmOpeningMode = settings.opening_mode;
        this.pcmFloatAmount = settings.float_amount || 0;
        // Odoo 20 pre-fills the opening with the cash journal balance left by
        // the previous session.
        this.pcmLastClosing = this.pos.config._last_opening_balance || 0;
        if (this.pcmOpeningMode === "suggest_float") {
            this.state.openingCash = this.pos.formatCurrency(
                this.pcmFloatAmount,
                this.pos.config.currency_id.id,
                { noSymbol: true }
            );
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
        return !this.pos.currency.isZero(this.pcmFloatAmount - this.pcmLastClosing);
    },
    //@override
    async confirm() {
        if (this.pcmOpeningMode !== "standard" && this.cashMethodCount) {
            const value = this.state.openingCash;
            const amount = this.pos.isValidFloat(value) ? this.pos.parseCurrency(value) : NaN;
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
