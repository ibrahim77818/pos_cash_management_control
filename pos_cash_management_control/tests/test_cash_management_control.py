# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged('post_install', '-at_install')
class TestPosCashManagementControl(TestPoSCommon):
    """Opening / closing cash control modes of pos_cash_management_control.

    TestPoSCommon is a TransactionCase (through AccountTestInvoicingCommon)
    that builds a company with a chart of accounts, a cash journal and a
    basic POS configuration with a cash payment method (cash control on).
    """

    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        Account = cls.env['account.account']
        cash_journal = cls.company_data['default_journal_cash']
        if not cash_journal.profit_account_id:
            cash_journal.profit_account_id = Account.create({
                'name': 'PCM Cash Difference Gain', 'code': 'PCM991',
                'account_type': 'income_other', 'company_id': cls.company.id,
            })
        if not cash_journal.loss_account_id:
            cash_journal.loss_account_id = Account.create({
                'name': 'PCM Cash Difference Loss', 'code': 'PCM992',
                'account_type': 'expense', 'company_id': cls.company.id,
            })
        cls.cash_journal = cash_journal
        cls.float_account = Account.create({
            'name': 'PCM Cash Float in Safe', 'code': 'PCM993',
            'account_type': 'asset_current', 'company_id': cls.company.id,
        })
        common = {'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)]}
        cls.cashier_user = new_test_user(
            cls.env, login='pcm_cashier', groups='base.group_user,point_of_sale.group_pos_user', **common)
        cls.manager_user = new_test_user(
            cls.env, login='pcm_manager', groups='base.group_user,point_of_sale.group_pos_manager', **common)

    def setUp(self):
        super().setUp()
        self.config = self.basic_config
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
        self.env['pos.order'].create_from_ui([order_data])
        return order_data['data']['amount_paid']

    def _close(self, session, counted, user=None):
        session_as = session.with_user(user) if user else session
        res = session_as.post_closing_cash_details(counted)
        self.assertTrue(res['successful'], res)
        session_as.update_closing_control_state_session('')
        res = session.close_session_from_ui()
        self.assertTrue(res['successful'], res)
        self.assertEqual(session.state, 'closed')

    def _run_standard_session(self, closing_cash):
        """Standard session that ends with `closing_cash` in the drawer."""
        modes = {f: self.config[f] for f in ('pcm_opening_mode', 'pcm_closing_mode')}
        self.config.write({'pcm_opening_mode': 'standard', 'pcm_closing_mode': 'standard'})
        session = self._open()
        session.set_cashbox_pos(closing_cash, False)
        self._close(session, closing_cash)
        self.config.write(modes)
        return session

    def _lines(self, session, label):
        return session.sudo().statement_line_ids.filtered(lambda l: label in (l.payment_ref or ''))

    def _float_lines(self, session):
        return self._lines(session, 'Opening float adjustment')

    def _difference_lines(self, session):
        return self._lines(session, 'Cash difference observed during the counting')

    def _assert_register_reconciles(self, session, previous_closing):
        """Every cash movement of the closed session sits in the cash register."""
        total = sum(session.sudo().statement_line_ids.mapped('amount'))
        self.assertAlmostEqual(total, session.cash_register_balance_end_real - previous_closing, places=2)
        if session.move_id:
            self.assertEqual(session.move_id.state, 'posted')
        for move in session.sudo().statement_line_ids.move_id:
            self.assertAlmostEqual(sum(move.line_ids.mapped('balance')), 0.0, places=2)

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
        session.set_cashbox_pos(0, False)
        self.assertEqual(session.state, 'opened')

        paid = self._sell()
        data = session.get_closing_control_data()
        cash = data['default_cash_details']
        self.assertEqual(cash['pcm_closing_mode'], 'standard')
        self.assertNotIn('pcm_masked', cash)
        self.assertNotIn('pcm_server_count', cash)
        self.assertAlmostEqual(cash['amount'], paid)

        self._close(session, paid - 1)
        self.assertAlmostEqual(session.cash_register_difference, -1.0)
        self.assertEqual(len(self._difference_lines(session)), 1)
        self._assert_register_reconciles(session, 0.0)

    def test_02_standard_opening_difference_vs_last_closing(self):
        self._run_standard_session(300)
        session = self._open()
        self.assertEqual(session.state, 'opening_control')
        self.assertAlmostEqual(session.cash_register_balance_start, 300)
        session.set_cashbox_pos(290, False)
        self.assertAlmostEqual(session.cash_register_balance_start, 290)
        diff = self._difference_lines(session)
        self.assertEqual(len(diff), 1)
        self.assertAlmostEqual(diff.amount, -10)
        self.assertFalse(self._float_lines(session))
        self.assertAlmostEqual(session.cash_register_balance_end, 290)

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
        session.set_cashbox_pos(100, 'float counted')

        self.assertEqual(session.state, 'opened')
        self.assertAlmostEqual(session.cash_register_balance_start, 100)
        adjustment = self._float_lines(session)
        self.assertEqual(len(adjustment), 1)
        self.assertAlmostEqual(adjustment.amount, -200)
        float_line = adjustment.move_id.line_ids.filtered(lambda l: l.account_id == self.float_account)
        self.assertAlmostEqual(float_line.debit, 200, msg="Cash moved to the safe is debited on the float account")
        self.assertFalse(self._difference_lines(session), "Resetting to the float is not a cash difference")
        self.assertAlmostEqual(session.cash_register_balance_end, 100)

    def test_11_suggest_float_count_difference(self):
        self._run_standard_session(300)
        self.config.write({'pcm_opening_mode': 'suggest_float', 'pcm_float_amount': 100})
        session = self._open()
        session.set_cashbox_pos(95, False)
        self.assertAlmostEqual(self._float_lines(session).amount, -200)
        diff = self._difference_lines(session)
        self.assertEqual(len(diff), 1)
        self.assertAlmostEqual(diff.amount, -5, msg="The count is compared with the float, not the last closing")
        self.assertAlmostEqual(session.cash_register_balance_start, 95)
        self.assertAlmostEqual(session.cash_register_balance_end, 95)

    def test_12_suggest_float_first_session(self):
        self.config.write({
            'pcm_opening_mode': 'suggest_float', 'pcm_float_amount': 100,
            'pcm_float_account_id': self.float_account.id,
        })
        session = self._open()
        self.assertAlmostEqual(session.cash_register_balance_start, 0)
        session.set_cashbox_pos(100, False)
        adjustment = self._float_lines(session)
        self.assertAlmostEqual(adjustment.amount, 100)
        float_line = adjustment.move_id.line_ids.filtered(lambda l: l.account_id == self.float_account)
        self.assertAlmostEqual(float_line.credit, 100)
        self.assertAlmostEqual(session.cash_register_balance_end, 100)

    def test_13_opening_rejects_negative_and_double_submission(self):
        self.config.write({'pcm_opening_mode': 'suggest_float', 'pcm_float_amount': 50})
        session = self._open()
        with self.assertRaises(UserError):
            session.set_cashbox_pos(-1, False)
        session.set_cashbox_pos(50, False)
        with self.assertRaises(UserError):
            session.set_cashbox_pos(50, False)
        self.assertEqual(len(self._float_lines(session)), 1)

    # ------------------------------------------------------------------
    # opening: server-side
    # ------------------------------------------------------------------
    def test_20_server_last_closing_opens_without_count(self):
        self._run_standard_session(300)
        self.config.write({'pcm_opening_mode': 'server_last_closing'})
        session = self._open()
        self.assertEqual(session.state, 'opened')
        self.assertTrue(session.pcm_opened_by_server)
        self.assertAlmostEqual(session.cash_register_balance_start, 300)
        self.assertFalse(session.sudo().statement_line_ids, "No difference and no adjustment")
        with self.assertRaises(UserError):
            session.set_cashbox_pos(250, False)
        with self.assertRaises(UserError):
            session.with_context(pcm_server_opening=True).set_cashbox_pos(250, False)
        self.assertAlmostEqual(session.cash_register_balance_start, 300)

    def test_21_server_last_closing_first_session_falls_back_to_count(self):
        self.config.write({'pcm_opening_mode': 'server_last_closing'})
        session = self._open()
        self.assertEqual(session.state, 'opening_control', "No previous closing balance: the cashier counts")
        self.assertFalse(session.pcm_opened_by_server)
        self.assertEqual(session._pcm_effective_opening_mode(), 'standard')
        session.set_cashbox_pos(40, False)
        self.assertEqual(session.state, 'opened')
        self.assertAlmostEqual(session.cash_register_balance_start, 40)

    def test_22_server_fixed_float_first_session(self):
        self.config.write({
            'pcm_opening_mode': 'server_fixed_float', 'pcm_float_amount': 150,
            'pcm_float_account_id': self.float_account.id,
        })
        session = self._open()
        self.assertEqual(session.state, 'opened')
        self.assertTrue(session.pcm_opened_by_server)
        self.assertAlmostEqual(session.cash_register_balance_start, 150)
        self.assertAlmostEqual(self._float_lines(session).amount, 150)
        self.assertFalse(self._difference_lines(session))
        self.assertAlmostEqual(session.cash_register_balance_end, 150)

    def test_23_server_fixed_float_full_cycle(self):
        self._run_standard_session(300)
        self.config.write({'pcm_opening_mode': 'server_fixed_float', 'pcm_float_amount': 100})
        session = self._open()
        self.assertAlmostEqual(self._float_lines(session).amount, -200)
        self.assertAlmostEqual(session.cash_register_balance_start, 100)
        with self.assertRaises(UserError):
            session.set_cashbox_pos(100, False)

        paid = self._sell()
        self._close(session, 100 + paid)
        self.assertFalse(self._difference_lines(session))
        self._assert_register_reconciles(session, 300)

        next_session = self._open()
        self.assertAlmostEqual(self._float_lines(next_session).amount, -paid)
        self.assertAlmostEqual(next_session.cash_register_balance_start, 100)

    def test_24_server_fixed_float_zero(self):
        self._run_standard_session(300)
        self.config.write({'pcm_opening_mode': 'server_fixed_float', 'pcm_float_amount': 0})
        session = self._open()
        self.assertEqual(session.state, 'opened')
        self.assertAlmostEqual(self._float_lines(session).amount, -300)
        self.assertAlmostEqual(session.cash_register_balance_start, 0)

    # ------------------------------------------------------------------
    # closing: blind count
    # ------------------------------------------------------------------
    def test_30_blind_count_hides_expected_cash_from_cashier_only(self):
        self.config.write({'pcm_closing_mode': 'blind'})
        session = self._open()
        session.set_cashbox_pos(0, False)
        paid = self._sell()

        cashier_data = session.with_user(self.cashier_user).get_closing_control_data()
        cash = cashier_data['default_cash_details']
        self.assertTrue(cash.get('pcm_masked'))
        self.assertEqual(cash['amount'], 0.0)
        self.assertEqual(cash['payment_amount'], 0.0)
        self.assertEqual(cash['moves'], [])
        self.assertEqual(cashier_data['orders_details']['amount'], 0.0)

        manager_data = session.with_user(self.manager_user).get_closing_control_data()
        cash = manager_data['default_cash_details']
        self.assertFalse(cash.get('pcm_masked'))
        self.assertAlmostEqual(cash['amount'], paid)

    def test_31_blind_count_posts_real_difference(self):
        self.config.write({'pcm_closing_mode': 'blind'})
        session = self._open()
        session.set_cashbox_pos(0, False)
        paid = self._sell()
        messages_before = session.message_ids

        self._close(session, paid - 2, user=self.cashier_user)
        self.assertAlmostEqual(session.cash_register_balance_end_real, paid - 2)
        self.assertAlmostEqual(session.cash_register_difference, -2)
        diff = self._difference_lines(session)
        self.assertEqual(len(diff), 1)
        self.assertAlmostEqual(diff.amount, -2)
        loss = diff.move_id.line_ids.filtered(lambda l: l.account_id == self.cash_journal.loss_account_id)
        self.assertAlmostEqual(loss.debit, 2)
        new_bodies = ' '.join((session.message_ids - messages_before).mapped('body'))
        self.assertNotIn('Closing difference', new_bodies, "The chatter must not reveal the blind difference")
        self._assert_register_reconciles(session, 0.0)

    def test_32_blind_count_requires_a_valid_count(self):
        self.config.write({'pcm_closing_mode': 'blind'})
        session = self._open()
        session.set_cashbox_pos(0, False)
        with self.assertRaises(UserError):
            session.post_closing_cash_details(None)
        with self.assertRaises(UserError):
            session.post_closing_cash_details(-5)

    # ------------------------------------------------------------------
    # closing: server no-count
    # ------------------------------------------------------------------
    def test_40_server_count_ignores_submitted_amount(self):
        self.config.write({'pcm_closing_mode': 'server_no_count'})
        session = self._open()
        session.set_cashbox_pos(0, False)
        paid = self._sell(2)
        self.assertTrue(session.get_closing_control_data()['default_cash_details'].get('pcm_server_count'))

        self._close(session, 999)
        self.assertAlmostEqual(session.cash_register_balance_end_real, paid)
        self.assertAlmostEqual(session.cash_register_difference, 0)
        self.assertFalse(self._difference_lines(session))
        self.assertAlmostEqual(self.config.last_session_closing_cash, paid)
        self._assert_register_reconciles(session, 0.0)

    def test_41_server_count_backend_close_and_late_cash_move(self):
        self.config.write({'pcm_closing_mode': 'server_no_count'})
        session = self._open()
        session.set_cashbox_pos(0, False)
        paid = self._sell()
        session.post_closing_cash_details(0)
        # Cash taken out after the count was posted is still included.
        session.try_cash_in_out('out', 3, 'bank deposit', {'translatedType': 'out', 'formattedAmount': '3'})
        session.action_pos_session_closing_control()
        self.assertEqual(session.state, 'closed')
        self.assertAlmostEqual(session.cash_register_balance_end_real, paid - 3)
        self.assertFalse(self._difference_lines(session))

    # ------------------------------------------------------------------
    # Daily Sale, settings, UI payload
    # ------------------------------------------------------------------
    def test_50_daily_sale_restricted_to_managers(self):
        session = self._open()
        session.set_cashbox_pos(0, False)
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
        session.set_cashbox_pos(0, False)
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
        session.set_cashbox_pos(0, False)
        settings = session.load_pos_data()['pcm_cash_control']
        self.assertEqual(settings['configured_opening_mode'], 'server_last_closing')
        self.assertEqual(settings['opening_mode'], 'standard')
        self.assertEqual(settings['closing_mode'], 'blind')
        self.assertTrue(settings['hide_daily_sale'])
        self.assertIn('user_is_manager', settings)
        self.assertFalse(settings['opened_by_server'])
        self.assertTrue(session.with_user(self.manager_user)._pcm_is_manager())
        self.assertFalse(session.with_user(self.cashier_user)._pcm_is_manager())

    def test_54_rescue_session_untouched(self):
        self.config.write({'pcm_opening_mode': 'server_fixed_float', 'pcm_float_amount': 100})
        rescue = self.env['pos.session'].create({
            'config_id': self.config.id, 'user_id': self.env.uid, 'rescue': True, 'name': 'PCM RESCUE',
        })
        self.assertFalse(rescue._pcm_is_cash_managed())
        self.assertFalse(rescue.pcm_opened_by_server)
        self.assertFalse(self._float_lines(rescue))
