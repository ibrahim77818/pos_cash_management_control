odoo.define('pos_cash_management_control.ClosePosPopup', function (require) {
    'use strict';

    const ClosePosPopup = require('point_of_sale.ClosePosPopup');
    const Registries = require('point_of_sale.Registries');

    const PcmClosePosPopup = (ClosePosPopup) => class PcmClosePosPopup extends ClosePosPopup {
        setup() {
            super.setup();
            const pos = this.env.pos;
            const cash = this.defaultCashDetails;
            const hasCash = Boolean(this.cashControl && cash);
            // `pcm_masked` comes from the server (cashier user); the role
            // check covers pos_hr, where the employee may be a cashier while
            // the logged-in user is a manager.
            this.pcmBlind = hasCash && (Boolean(cash.pcm_masked) || pos.pcmIsBlindClosing());
            this.pcmServerCount = hasCash && !this.pcmBlind
                && (Boolean(cash.pcm_server_count) || pos.pcmIsServerClosingCount());
            this.pcmHideDailySale = !pos.pcmCanSeeDailySale();
            if (this.pcmServerCount) {
                this.state.payments[cash.id].counted = cash.amount;
                this.state.payments[cash.id].difference = 0;
            }
        }
        _pcmIsCashEntry(paymentId) {
            return Boolean(this.defaultCashDetails) && parseInt(paymentId) === this.defaultCashDetails.id;
        }
        /**
         * Payments whose difference the cashier may see and be judged on.
         * Blind and server counts exclude the cash line.
         */
        _pcmComparablePayments() {
            const excludeCash = this.pcmBlind || this.pcmServerCount;
            return Object.entries(this.state.payments).filter(
                ([paymentId]) => !(excludeCash && this._pcmIsCashEntry(paymentId))
            );
        }
        //@override
        hasDifference() {
            return this._pcmComparablePayments().find((pm) => pm[1].difference != 0);
        }
        //@override
        hasUserAuthority() {
            const absDifferences = this._pcmComparablePayments().map((pm) => Math.abs(pm[1].difference));
            if (!absDifferences.length) {
                return true;
            }
            return this.isManager || this.amountAuthorizedDiff == null
                || Math.max(...absDifferences) <= this.amountAuthorizedDiff;
        }
        //@override
        async confirm() {
            if (this.pcmBlind) {
                const counted = this.state.payments[this.defaultCashDetails.id].counted;
                if (typeof counted !== 'number' || isNaN(counted) || counted < 0) {
                    await this.showPopup('ErrorPopup', {
                        title: this.env._t('Invalid Cash Count'),
                        body: this.env._t('Please enter the counted cash before closing the session.'),
                    });
                    return;
                }
                const { confirmed } = await this.showPopup('ConfirmPopup', {
                    title: this.env._t('Confirm Blind Count'),
                    body: _.str.sprintf(
                        this.env._t('You are declaring %s as the counted cash.\n' +
                            'The expected amount is hidden; any difference will be reviewed by a manager.'),
                        this.env.pos.format_currency(counted)
                    ),
                });
                if (!confirmed) {
                    return;
                }
            }
            return super.confirm(...arguments);
        }
        //@override
        openDetailsPopup() {
            if (this.pcmServerCount) {
                return;
            }
            return super.openDetailsPopup(...arguments);
        }
        //@override
        async downloadSalesReport() {
            if (this.pcmHideDailySale) {
                await this.showPopup('ErrorPopup', {
                    title: this.env._t('Access Denied'),
                    body: this.env._t('Only Point of Sale managers can view the Daily Sale report.'),
                });
                return;
            }
            return super.downloadSalesReport(...arguments);
        }
    };

    Registries.Component.extend(ClosePosPopup, PcmClosePosPopup);

    return PcmClosePosPopup;
});
