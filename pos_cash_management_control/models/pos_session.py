# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount

SERVER_OPENING_MODES = ('server_last_closing', 'server_fixed_float')


class PosSession(models.Model):
    """Opening / closing cash control on top of the Odoo 20 cash statement.

    Since Odoo 20 every session owns an ``account.bank.statement`` on the cash
    journal: the opening count, the cash payments, the cash in/out and the
    closing count are all lines of that statement, and any counting
    difference is booked by ``_handle_cash_statement_entries`` as a
    "Cash correction" line (profit / loss account of the cash journal).
    """
    _inherit = 'pos.session'

    pcm_cash_control = fields.Boolean(related='config_id.cash_control', string='Cash Control')
    pcm_opening_mode = fields.Selection(related='config_id.pcm_opening_mode', string='Opening Cash Control')
    pcm_closing_mode = fields.Selection(related='config_id.pcm_closing_mode', string='Closing Cash Control')
    pcm_opened_by_server = fields.Boolean(
        string='Opened by Server', readonly=True, copy=False,
        help="The opening cash was set by the server, without a count by the cashier.")
    pcm_float_statement_line_id = fields.Many2one(
        'account.bank.statement.line', string='Opening Float Transfer', readonly=True, copy=False,
        help="Cash statement line booking the cash moved between the drawer and the safe to reach the fixed float.")
    pcm_expected_cash = fields.Monetary(
        string='Expected Closing Cash', readonly=True, copy=False, currency_field='currency_id',
        groups='point_of_sale.group_pos_manager',
        help="Cash the drawer should contain at closing (opening, cash payments and cash in/out).")
    pcm_counted_cash = fields.Monetary(
        string='Counted Closing Cash', readonly=True, copy=False, currency_field='currency_id',
        help="Cash recorded at closing: the cashier's count, or the expected cash when the server counts.")
    pcm_cash_difference = fields.Monetary(
        string='Closing Cash Difference', readonly=True, copy=False, currency_field='currency_id',
        groups='point_of_sale.group_pos_manager',
        help="Counted minus expected closing cash, booked as a cash correction.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _pcm_cash_pm(self):
        self.ensure_one()
        return self.config_id._get_cash_payment_method()

    def _pcm_is_cash_managed(self):
        """Our rules apply only to sessions whose POS has a cash payment method."""
        self.ensure_one()
        return bool(self.config_id.cash_control and self._pcm_cash_pm())

    def _pcm_last_session(self):
        """Previous session of the same POS."""
        self.ensure_one()
        return self.search([('config_id', '=', self.config_id.id), ('id', '<', self.id)], limit=1)

    def _pcm_effective_opening_mode(self):
        """Opening mode after edge cases.

        'Open on server with last closing balance' needs a previous session:
        the very first session of a POS falls back to a standard count so the
        initial cash is declared once by a human.
        """
        self.ensure_one()
        mode = self.config_id.pcm_opening_mode or 'standard'
        if mode == 'server_last_closing' and not self._pcm_last_session():
            return 'standard'
        return mode

    def _pcm_is_manager(self):
        self.ensure_one()
        return self.config_id._pcm_user_is_manager()

    def _pcm_last_balance(self):
        """Cash left in the drawer by the previous session (Odoo's opening balance).

        The journal running balance is computed in SQL and cached: flush the
        statement lines and drop the cache so it includes the latest closing.
        """
        self.ensure_one()
        journal = self._pcm_cash_pm().journal_id
        for model in ('account.bank.statement.line', 'account.bank.statement', 'account.move.line'):
            self.env[model].flush_model()
        journal.invalidate_recordset(['current_statement_balance', 'has_statement_lines'])
        return self.currency_id.round(self.config_id._get_opening_balance())

    def _pcm_post_float_adjustment(self, float_amount, previous):
        """Book the gap between the cash in the drawer and the fixed float.

        It is cash moved between the drawer and the safe/bank, not a counting
        loss or profit: it is a line of the session cash statement whose
        counterpart is the float transfer account (or the suspense account,
        like a standard Cash In/Out). Being a statement line, it is part of
        the expected cash of the session, exactly once.
        """
        self.ensure_one()
        currency = self.currency_id
        adjustment = currency.round(float_amount - previous)
        if currency.is_zero(adjustment):
            return
        cash_pm = self._pcm_cash_pm().sudo()
        account = self.config_id.pcm_float_account_id or cash_pm.journal_id.suspense_account_id
        move_lines = cash_pm._create_payment_line(
            self, adjustment, account, _("%s - Opening float adjustment", self.config_id.name))
        self.sudo().pcm_float_statement_line_id = move_lines.move_id.statement_line_id[:1]
        self.message_post(body=_(
            "Opening float adjustment of %(amount)s: drawer reset from %(previous)s to the fixed float %(float)s.",
            amount=format_amount(self.env, adjustment, currency),
            previous=format_amount(self.env, previous, currency),
            float=format_amount(self.env, float_amount, currency)))

    # ------------------------------------------------------------------
    # Opening cash control
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        sessions = super().create(vals_list)
        for session in sessions:
            if (session.state == 'opening_control' and session._pcm_is_cash_managed()
                    and session._pcm_effective_opening_mode() in SERVER_OPENING_MODES):
                # Public entry point: also assigns the session sequence.
                session.set_opening_control(0.0, _("Opening cash set by the server (no count)."))
                session.sudo().pcm_opened_by_server = True
        return sessions

    def _set_opening_control_data(self, cashbox_value, notes):
        """Enforce the opening mode, whatever amount the client sends.

        Server modes ignore the submitted amount entirely, so no client call
        can choose the opening balance of a server-opened POS. Odoo itself
        ignores a second opening (the session is no longer in opening control).
        """
        if len(self) != 1 or self.state != 'opening_control' or not self._pcm_is_cash_managed():
            return super()._set_opening_control_data(cashbox_value, notes)
        mode = self._pcm_effective_opening_mode()
        if mode == 'standard':
            return super()._set_opening_control_data(cashbox_value, notes)

        if mode == 'suggest_float':
            try:
                counted = float(cashbox_value or 0.0)
            except (TypeError, ValueError):
                raise UserError(_("The opening cash amount is not a valid number."))
            if counted < 0:
                raise UserError(_("The opening cash amount cannot be negative."))

        previous = self._pcm_last_balance()
        # Open on the previous balance: Odoo books no correction line.
        res = super()._set_opening_control_data(previous, notes)
        if mode in ('suggest_float', 'server_fixed_float'):
            float_amount = self.config_id.pcm_float_amount or 0.0
            self._pcm_post_float_adjustment(float_amount, previous)
            if mode == 'suggest_float':
                # Counted vs float: a real counting difference, booked by Odoo
                # as a cash correction (statement now ends on the float).
                self.with_context(pcm_opening=True)._handle_cash_statement_entries({
                    self._pcm_cash_pm().id: counted,
                })
        return res

    # ------------------------------------------------------------------
    # Closing cash control
    # ------------------------------------------------------------------
    def get_closing_control_data(self):
        data = super().get_closing_control_data()
        cash = data.get('default_cash_details')
        if not cash or not self._pcm_is_cash_managed():
            return data
        mode = self.config_id.pcm_closing_mode or 'standard'
        cash['pcm_closing_mode'] = mode
        if mode == 'blind' and not self._pcm_is_manager():
            # Blind count: the expected cash and everything it can be rebuilt
            # from never leave the server for a cashier.
            cash.update({'amount': 0.0, 'opening': 0.0, 'payment_amount': 0.0, 'moves': [], 'pcm_masked': True})
            # pos_hr (a dependency, so already applied by super()) adds a
            # per-employee breakdown that would let the cashier rebuild the total.
            for key in ('amount_per_employee', 'moves_per_employee'):
                if key in cash:
                    cash[key] = []
            data['orders_details'] = dict(data.get('orders_details') or {}, amount=0.0)
            # Only existing keys are rewritten: ClosePosPopup declares strict props.
        elif mode == 'server_no_count':
            cash['pcm_server_count'] = True
        return data

    def close_session_from_ui(self, payment_method_closing={}):
        self.ensure_one()
        if (self.state != 'closed' and self._pcm_is_cash_managed()
                and self.config_id.pcm_closing_mode == 'blind'):
            cash_pm = self._pcm_cash_pm()
            closing = payment_method_closing or {}
            counted = closing.get(str(cash_pm.id), closing.get(cash_pm.id))
            if counted is None or counted is False:
                raise UserError(_("A cash count is required to close session %s (blind count).", self.name))
            if counted < 0:
                raise UserError(_("The counted cash cannot be negative."))
        return super().close_session_from_ui(payment_method_closing)

    def _handle_cash_statement_entries(self, payment_method_closing={}):
        """Closing pass (the statement already exists): enforce the no-count
        mode and record expected / counted / difference for managers.

        Called after the session accounting, so the statement already holds
        the cash payments: its ending balance is the expected cash Odoo
        compares the count with.
        """
        if (len(self) != 1 or not self.bank_statement_id or self.env.context.get('pcm_opening')
                or not self._pcm_is_cash_managed()):
            return super()._handle_cash_statement_entries(payment_method_closing)
        cash_pm = self._pcm_cash_pm()
        closing = dict(payment_method_closing or {})
        expected = self.currency_id.round(self.bank_statement_id.balance_end)
        if (self.config_id.pcm_closing_mode or 'standard') == 'server_no_count':
            closing[str(cash_pm.id)] = expected
            closing[cash_pm.id] = expected
            counted = expected
        else:
            counted = closing.get(str(cash_pm.id), 0) or closing.get(cash_pm.id, 0) or 0.0
        res = super()._handle_cash_statement_entries(closing)
        self.sudo().write({
            'pcm_expected_cash': expected,
            'pcm_counted_cash': counted,
            'pcm_cash_difference': self.currency_id.round(counted - expected),
        })
        return res

    # ------------------------------------------------------------------
    # POS UI data (loaded once with the session, no extra RPC)
    # ------------------------------------------------------------------
    @api.model
    def _load_pos_data_read(self, records, config):
        res = super()._load_pos_data_read(records, config)
        for vals in res:
            session = self.browse(vals['id'])
            settings = session.config_id._pcm_get_ui_settings()
            settings.update({
                'configured_opening_mode': session.config_id.pcm_opening_mode or 'standard',
                'opening_mode': session._pcm_effective_opening_mode(),
                'opened_by_server': session.sudo().pcm_opened_by_server,
                'user_is_manager': session._pcm_is_manager(),
            })
            vals['_pcm_cash_control'] = settings
        return res
