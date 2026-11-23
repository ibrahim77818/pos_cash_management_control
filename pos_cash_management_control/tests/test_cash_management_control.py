# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged('post_install', '-at_install')
class TestPosCashManagementControl(TestPoSCommon):
    """Opening / closing cash control modes of pos_cash_management_control (Odoo 20).

    Odoo 20 accounting rules this module follows:
    - every session owns a cash statement: opening count, cash payments,
      cash in/out and closing count are lines of that statement;
    - any counting difference (opening or closing) is booked by Odoo as a
      "Cash correction" line on the profit / loss account of the cash journal;
    - the float transfer is a line of the session statement whose
      counterpart is the float transfer account (or the suspense account).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data['company']
        Account = cls.env['account.account']
        cash_journal = cls.company_data['default_journal_cash']
        if not cash_journal.profit_account_id:
            cash_journal.profit_account_id = Account.create({
                'name': 'PCM Cash Difference Gain', 'code': 'PCM991',
                'account_type': 'income_other', 'company_ids': [(6, 0, cls.company.ids)],
            })
        if not cash_journal.loss_account_id:
            cash_journal.loss_account_id = Account.create({
                'name': 'PCM Cash Difference Loss', 'code': 'PCM992',
                'account_type': 'expense', 'company_ids': [(6, 0, cls.company.ids)],
            })
        cls.cash_journal = cash_journal
        cls.float_account = Account.create({
            'name': 'PCM Cash Float in Safe', 'code': 'PCM993',
            'account_type': 'asset_current', 'company_ids': [(6, 0, cls.company.ids)],
        })
        common = {'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)]}
        cls.cashier_user = new_test_user(
            cls.env, login='pcm_cashier', groups='base.group_user,point_of_sale.group_pos_user', **common)
        cls.manager_user = new_test_user(
            cls.env, login='pcm_manager', groups='base.group_user,point_of_sale.group_pos_manager', **common)

    def setUp(self):
        super().setUp()
        self.config = self.basic_config
        self.cash_pm = self.config._get_cash_payment_method()
        self.product1 = self.create_product('PCM Product', self.categ_basic, 10.0, 5.0)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _open(self, config=None):
        config = config or self.config
        config.open_ui()
        session = config.current_session_id
        self.config = config
        self.pos_session = session
        self.currency = session.currency_id
        self.pricelist = config.pricelist_id
        return session

    def _sell(self, qty=1):
        order_data = self.create_ui_order_data([(self.product1, qty)])
        self.env['pos.order'].sync_from_ui([order_data])
        return order_data['amount_paid']

    def _close(self, session, counted):
        closing = {} if counted is None else {str(self.cash_pm.id): counted}
        res = session.close_session_from_ui(closing)
        self.assertTrue(res['status'], res)
        self.assertEqual(session.state, 'closed')

    def _run_standard_session(self, closing_cash):
        """Standard session that ends with `closing_cash` in the drawer."""
        modes = {f: self.config[f] for f in ('pcm_opening_mode', 'pcm_closing_mode')}
        self.config.write({'pcm_opening_mode': 'standard', 'pcm_closing_mode': 'standard'})
        session = self._open()
        session.set_opening_control(closing_cash, False)
        self._close(session, closing_cash)
        self.config.write(modes)
        return session

    def _lines(self, session):
        return session.sudo().bank_statement_id.line_ids

    def _float_line(self, session):
        return session.sudo().pcm_float_statement_line_id

    def _correction_lines(self, session):
        return self._lines(session).filtered(lambda l: (l.payment_ref or '').startswith('Cash correction'))

    def _balance_end(self, session):
        return session.sudo().bank_statement_id.balance_end

    def _assert_register_reconciles(self, session):
        """Every cash movement of the closed session sits in its cash statement."""
        statement = session.sudo().bank_statement_id
        self.assertAlmostEqual(
            statement.balance_start + sum(statement.line_ids.mapped('amount')), statement.balance_end_real, places=2)
        for move in statement.line_ids.move_id:
            self.assertEqual(move.state, 'posted')
            self.assertAlmostEqual(sum(move.line_ids.mapped('balance')), 0.0, places=2)

    def _assert_float_line(self, session, amount, float_side, account=None):
        line = self._float_line(session)
        self.assertTrue(line, "A float transfer line is expected")
        self.assertAlmostEqual(line.amount, amount)
        self.assertEqual(line.statement_id, session.sudo().bank_statement_id,
                         "Part of the session statement, hence of the expected cash, exactly once")
        self.assertEqual(line.move_id.state, 'posted')
        account = account or self.float_account
        float_ml = line.move_id.line_ids.filtered(lambda l: l.account_id == account)
        self.assertAlmostEqual(float_ml[float_side], abs(amount))

    # ------------------------------------------------------------------
    # defaults
    # ------------------------------------------------------------------
    def test_01_defaults_keep_standard_behaviour(self):
        config = self.config
        self.assertEqual(config.pcm_opening_mode, 'standard')
        self.assertEqual(config.pcm_closing_mode, 'standard')
        self.assertFalse(config.pcm_hide_daily_sale)
        self.assertFalse(config.pcm_float_amount)

        session = self._open()
        self.assertEqual(session.state, 'opening_control', "Standard mode still asks for an opening count")
        self.assertFalse(session.pcm_opened_by_server)
        session.set_opening_control(0, False)
        self.assertEqual(session.state, 'opened')

        paid = self._sell()
        cash = session.get_closing_control_data()['default_cash_details']
        self.assertEqual(cash['pcm_closing_mode'], 'standard')
        self.assertNotIn('pcm_masked', cash)
        self.assertNotIn('pcm_server_count', cash)
        self.assertAlmostEqual(cash['amount'], paid)

        self._close(session, paid - 1)
        self.assertAlmostEqual(session.pcm_expected_cash, paid)
        self.assertAlmostEqual(session.pcm_counted_cash, paid - 1)
        self.assertAlmostEqual(session.pcm_cash_difference, -1.0)
        correction = self._correction_lines(session)
        self.assertEqual(len(correction), 1)
        self.assertAlmostEqual(correction.amount, -1.0)
        self._assert_register_reconciles(session)

    def test_02_standard_opening_follows_odoo_20(self):
        self._run_standard_session(300)
        session = self._open()
        self.assertEqual(session.state, 'opening_control')
        session.set_opening_control(290, False)
        statement = session.sudo().bank_statement_id
        self.assertAlmostEqual(statement.balance_start, 300)
        correction = self._correction_lines(session)
        self.assertEqual(len(correction), 1, "Odoo 20 books the opening difference")
        self.assertAlmostEqual(correction.amount, -10)
        self.assertFalse(self._float_line(session))
        self.assertAlmostEqual(self._balance_end(session), 290)

    # ------------------------------------------------------------------
    # opening: suggested fixed float
    # ------------------------------------------------------------------
    def test_10_suggest_float_books_float_transfer(self):
        self._run_standard_session(300)
        self.config.write({
            'pcm_opening_mode': 'suggest_float',
            'pcm_float_amount': 100,
            'pcm_float_account_id': self.float_account.id,
        })
        session = self._open()
        self.assertEqual(session.state, 'opening_control', "Suggested float is still counted by the cashier")
        session.set_opening_control(100, 'float counted')

        self.assertEqual(session.state, 'opened')
        self.assertAlmostEqual(session.sudo().bank_statement_id.balance_start, 300)
        self._assert_float_line(session, -200, 'debit')
        self.assertFalse(self._correction_lines(session), "The float transfer is not a cash difference")
        self.assertAlmostEqual(self._balance_end(session), 100)

    def test_11_suggest_float_count_difference(self):
        self._run_standard_session(300)
        self.config.write({'pcm_opening_mode': 'suggest_float', 'pcm_float_amount': 100})
        session = self._open()
        session.set_opening_control(95, False)
        self._assert_float_line(session, -200, 'debit', account=self.cash_journal.suspense_account_id)
        correction = self._correction_lines(session)
        self.assertEqual(len(correction), 1, "The count is compared with the float, not the last closing")
        self.assertAlmostEqual(correction.amount, -5)
        self.assertAlmostEqual(self._balance_end(session), 95)

    def test_12_suggest_float_first_session(self):
        self.config.write({
            'pcm_opening_mode': 'suggest_float', 'pcm_float_amount': 100,
            'pcm_float_account_id': self.float_account.id,
        })
        session = self._open()
        session.set_opening_control(100, False)
        self._assert_float_line(session, 100, 'credit')
        self.assertFalse(self._correction_lines(session))
        self.assertAlmostEqual(self._balance_end(session), 100)

    def test_13_opening_rejects_negative_and_ignores_second_submission(self):
        self.config.write({'pcm_opening_mode': 'suggest_float', 'pcm_float_amount': 50})
        session = self._open()
        with self.assertRaises(UserError):
            session.set_opening_control(-1, False)
        session.set_opening_control(50, False)
        line = self._float_line(session)
        lines = self._lines(session)
        session.set_opening_control(80, False)  # Odoo: no-op once opened
        self.assertAlmostEqual(self._balance_end(session), 50)
        self.assertEqual(self._float_line(session), line)
        self.assertEqual(self._lines(session), lines)

    # ------------------------------------------------------------------
    # opening: server-side
    # ------------------------------------------------------------------
    def test_20_server_last_closing_opens_without_count(self):
        self._run_standard_session(300)
        self.config.write({'pcm_opening_mode': 'server_last_closing'})
        session = self._open()
        self.assertEqual(session.state, 'opened')
        self.assertTrue(session.pcm_opened_by_server)
        self.assertNotEqual(session.name, '/', "The session sequence is assigned")
        self.assertAlmostEqual(session.sudo().bank_statement_id.balance_start, 300)
        self.assertFalse(self._lines(session))
        self.assertFalse(self._float_line(session))
        session.set_opening_control(250, False)
        self.assertAlmostEqual(self._balance_end(session), 300, msg="A client count cannot change it")

    def test_21_server_last_closing_first_session_falls_back_to_count(self):
        self.config.write({'pcm_opening_mode': 'server_last_closing'})
        session = self._open()
        self.assertEqual(session.state, 'opening_control', "No previous closing balance: the cashier counts")
        self.assertFalse(session.pcm_opened_by_server)
        self.assertEqual(session._pcm_effective_opening_mode(), 'standard')
        session.set_opening_control(40, False)
        self.assertEqual(session.state, 'opened')
        self.assertAlmostEqual(self._balance_end(session), 40)

    def test_22_server_fixed_float_first_session(self):
        self.config.write({
            'pcm_opening_mode': 'server_fixed_float', 'pcm_float_amount': 150,
            'pcm_float_account_id': self.float_account.id,
        })
        session = self._open()
        self.assertEqual(session.state, 'opened')
        self.assertTrue(session.pcm_opened_by_server)
        self._assert_float_line(session, 150, 'credit')
        self.assertFalse(self._correction_lines(session))
        self.assertAlmostEqual(self._balance_end(session), 150)

    def test_23_server_fixed_float_full_cycle(self):
        self._run_standard_session(300)
        self.config.write({'pcm_opening_mode': 'server_fixed_float', 'pcm_float_amount': 100})
        session = self._open()
        self._assert_float_line(session, -200, 'debit', account=self.cash_journal.suspense_account_id)
        self.assertAlmostEqual(self._balance_end(session), 100)

        paid = self._sell()
        self._close(session, 100 + paid)
        self.assertFalse(self._correction_lines(session))
        self.assertAlmostEqual(session.pcm_cash_difference, 0)
        self._assert_register_reconciles(session)

        next_session = self._open()
        self.assertAlmostEqual(self._float_line(next_session).amount, -paid)
        self.assertAlmostEqual(self._balance_end(next_session), 100)

    def test_24_server_fixed_float_zero(self):
        self._run_standard_session(300)
        self.config.write({'pcm_opening_mode': 'server_fixed_float', 'pcm_float_amount': 0})
        session = self._open()
        self.assertEqual(session.state, 'opened')
        self.assertAlmostEqual(self._float_line(session).amount, -300)
        self.assertAlmostEqual(self._balance_end(session), 0)

    def test_25_float_equal_to_last_closing_books_nothing(self):
        self._run_standard_session(100)
        self.config.write({'pcm_opening_mode': 'server_fixed_float', 'pcm_float_amount': 100})
        session = self._open()
        self.assertEqual(session.state, 'opened')
        self.assertFalse(self._float_line(session))
        self.assertFalse(self._lines(session))

    # ------------------------------------------------------------------
    # closing: blind count
    # ------------------------------------------------------------------
    def test_30_blind_count_hides_expected_cash_from_cashier_only(self):
        self.config.write({'pcm_closing_mode': 'blind'})
        session = self._open()
        session.set_opening_control(0, False)
        paid = self._sell()

        cashier_data = session.with_user(self.cashier_user).get_closing_control_data()
        cash = cashier_data['default_cash_details']
        self.assertTrue(cash.get('pcm_masked'))
        self.assertEqual(cash['amount'], 0.0)
        self.assertEqual(cash['opening'], 0.0)
        self.assertEqual(cash['payment_amount'], 0.0)
        self.assertEqual(cash['moves'], [])
        self.assertEqual(cashier_data['orders_details']['amount'], 0.0)

        manager_data = session.with_user(self.manager_user).get_closing_control_data()
        self.assertEqual(set(cashier_data), set(manager_data), "No extra keys: ClosePosPopup has strict props")
        cash = manager_data['default_cash_details']
        self.assertFalse(cash.get('pcm_masked'))
        self.assertAlmostEqual(cash['amount'], paid)

    def test_31_blind_count_books_real_difference_for_managers_only(self):
        self.config.write({'pcm_closing_mode': 'blind'})
        session = self._open()
        session.set_opening_control(0, False)
        paid = self._sell()

        self._close(session, paid - 2)
        self.assertAlmostEqual(session.pcm_expected_cash, paid)
        self.assertAlmostEqual(session.pcm_counted_cash, paid - 2)
        self.assertAlmostEqual(session.pcm_cash_difference, -2)
        correction = self._correction_lines(session)
        self.assertEqual(len(correction), 1)
        self.assertAlmostEqual(correction.amount, -2)
        loss = correction.move_id.line_ids.filtered(lambda l: l.account_id == self.cash_journal.loss_account_id)
        self.assertAlmostEqual(loss.debit, 2)
        self._assert_register_reconciles(session)

        # Expected cash and difference are manager-only fields.
        with self.assertRaises(AccessError):
            session.with_user(self.cashier_user).read(['pcm_cash_difference'])
        with self.assertRaises(AccessError):
            session.with_user(self.cashier_user).read(['pcm_expected_cash'])
        data = session.with_user(self.manager_user).read(['pcm_expected_cash', 'pcm_cash_difference'])[0]
        self.assertAlmostEqual(data['pcm_cash_difference'], -2)

    def test_32_blind_count_requires_a_valid_count(self):
        self.config.write({'pcm_closing_mode': 'blind'})
        session = self._open()
        session.set_opening_control(0, False)
        with self.assertRaises(UserError):
            session.close_session_from_ui({})
        with self.assertRaises(UserError):
            session.close_session_from_ui({str(self.cash_pm.id): -5})
        self.assertEqual(session.state, 'opened')
        self._close(session, 0)

    def test_33_blind_count_masks_pos_hr_breakdown(self):
        self.config.write({'pcm_closing_mode': 'blind'})
        session = self._open()
        session.set_opening_control(0, False)
        self._sell()
        cash = session.with_user(self.cashier_user).get_closing_control_data()['default_cash_details']
        self.assertTrue(cash.get('pcm_masked'))
        self.assertEqual(cash['amount'], 0.0)
        self.assertEqual(cash.get('amount_per_employee'), [])
        self.assertEqual(cash.get('moves_per_employee'), [])

        cash = session.with_user(self.manager_user).get_closing_control_data()['default_cash_details']
        self.assertFalse(cash.get('pcm_masked'))
        self.assertTrue(cash.get('amount_per_employee'), "Managers keep the per-employee breakdown")

    def test_34_standard_mode_keeps_pos_hr_breakdown(self):
        session = self._open()
        session.set_opening_control(0, False)
        self._sell()
        cash = session.with_user(self.cashier_user).get_closing_control_data()['default_cash_details']
        self.assertTrue(cash.get('amount_per_employee'))

    # ------------------------------------------------------------------
    # closing: server no-count
    # ------------------------------------------------------------------
    def test_40_server_count_ignores_submitted_amount(self):
        self.config.write({'pcm_closing_mode': 'server_no_count'})
        session = self._open()
        session.set_opening_control(0, False)
        paid = self._sell(2)
        self.assertTrue(session.get_closing_control_data()['default_cash_details'].get('pcm_server_count'))

        self._close(session, 999)
        self.assertAlmostEqual(session.pcm_counted_cash, paid)
        self.assertAlmostEqual(session.pcm_cash_difference, 0)
        self.assertFalse(self._correction_lines(session))
        self.assertAlmostEqual(session.sudo().bank_statement_id.balance_end_real, paid)
        self._assert_register_reconciles(session)

        next_session = self.env['pos.session'].create({'config_id': self.config.id})
        self.assertAlmostEqual(next_session._pcm_last_balance(), paid)

    def test_41_server_count_without_count_and_cash_move(self):
        self.config.write({'pcm_closing_mode': 'server_no_count'})
        session = self._open()
        session.set_opening_control(0, False)
        paid = self._sell()
        session.try_cash_in_out('out', 3, 'bank deposit', False)
        # No cash amount at all (e.g. a closing without the cash popup).
        self._close(session, None)
        self.assertAlmostEqual(session.pcm_counted_cash, paid - 3)
        self.assertFalse(self._correction_lines(session))
        self._assert_register_reconciles(session)

    # ------------------------------------------------------------------
    # Daily Sale, settings, UI payload
    # ------------------------------------------------------------------
    def test_50_daily_sale_restricted_to_managers(self):
        session = self._open()
        session.set_opening_control(0, False)
        paid = self._sell()
        self._close(session, paid)
        report = self.env['report.point_of_sale.report_saledetails']

        # Not restricted: cashiers keep the standard access.
        report.with_user(self.cashier_user).get_sale_details(session_ids=[session.id])

        self.config.pcm_hide_daily_sale = True
        with self.assertRaises(AccessError):
            report.with_user(self.cashier_user).get_sale_details(session_ids=[session.id])
        with self.assertRaises(AccessError):
            report.with_user(self.cashier_user).get_sale_details(config_ids=[self.config.id])
        details = report.with_user(self.manager_user).get_sale_details(session_ids=[session.id])
        self.assertTrue(details['products'])

    def test_51_settings_locked_while_session_running(self):
        session = self._open()
        session.set_opening_control(0, False)
        # Rewriting identical values (as res.config.settings does) is harmless.
        self.config.write({'pcm_closing_mode': 'standard', 'pcm_float_amount': 0})
        with self.assertRaises(UserError):
            self.config.write({'pcm_closing_mode': 'blind'})
        with self.assertRaises(ValidationError):
            self.config.copy().write({'pcm_float_amount': -1})

    def test_52_res_config_settings_round_trip(self):
        settings = self.env['res.config.settings'].create({
            'pos_config_id': self.config.id,
            'pos_pcm_opening_mode': 'server_fixed_float',
            'pos_pcm_float_amount': 75,
            'pos_pcm_float_account_id': self.float_account.id,
            'pos_pcm_closing_mode': 'blind',
            'pos_pcm_hide_daily_sale': True,
        })
        settings.execute()
        self.assertEqual(self.config.pcm_opening_mode, 'server_fixed_float')
        self.assertAlmostEqual(self.config.pcm_float_amount, 75)
        self.assertEqual(self.config.pcm_float_account_id, self.float_account)
        self.assertEqual(self.config.pcm_closing_mode, 'blind')
        self.assertTrue(self.config.pcm_hide_daily_sale)

    def test_53_ui_payload_loaded_once_with_session(self):
        self.config.write({
            'pcm_opening_mode': 'server_last_closing',  # first session: falls back
            'pcm_closing_mode': 'blind',
            'pcm_hide_daily_sale': True,
        })
        session = self._open()
        session.set_opening_control(0, False)
        data = session.load_data({'models': ['pos.session'], 'only_records': True})
        settings = data['pos.session'][0]['_pcm_cash_control']
        self.assertEqual(settings['configured_opening_mode'], 'server_last_closing')
        self.assertEqual(settings['opening_mode'], 'standard')
        self.assertEqual(settings['closing_mode'], 'blind')
        self.assertTrue(settings['hide_daily_sale'])
        self.assertIn('user_is_manager', settings)
        self.assertFalse(settings['opened_by_server'])
        self.assertTrue(session.with_user(self.manager_user)._pcm_is_manager())
        self.assertFalse(session.with_user(self.cashier_user)._pcm_is_manager())

    def test_54_pos_without_cash_method_untouched(self):
        config = self.config.copy({'name': 'PCM No Cash'})
        config.payment_method_ids = self.bank_pm1
        config.write({'pcm_opening_mode': 'server_fixed_float', 'pcm_float_amount': 100})
        self.assertFalse(config.cash_control)
        session = self.env['pos.session'].create({'config_id': config.id})
        self.assertFalse(session._pcm_is_cash_managed())
        self.assertEqual(session.state, 'opening_control')
        self.assertFalse(session.pcm_opened_by_server)
        self.assertFalse(self._float_line(session))
