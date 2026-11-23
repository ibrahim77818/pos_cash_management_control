# -*- coding: utf-8 -*-
{
    'name': 'POS Cash Management Control',
    'version': '20.0.1.0.0',
    'category': 'Sales/Point of Sale',
    'summary': 'Per-POS opening and closing cash control: fixed float, '
               'server-side opening, blind count, no-count closing, '
               'Daily Sale restriction for cashiers.',
    'description': """
POS Cash Management Control
===========================
Opening Cash Control per Point of Sale:
 * Standard count (Odoo default)
 * Count with a suggested fixed float
 * Open on server with the last closing balance
 * Open on server with a fixed float

Closing Cash Control per Point of Sale:
 * Standard count showing the difference (Odoo default)
 * Blind count: expected cash and difference hidden from cashiers
 * No count: the server takes the expected cash as counted
 * Hide the Daily Sale report from cashiers

All rules are enforced server-side in pos.session; the POS UI reads the
settings once at session load and adds no extra RPCs.
Works with Odoo 20 Community and Enterprise.
""",
    'author': 'Ibrahim Yahia',
    'website': 'https://www.linkedin.com/in/ibrahim-yahia',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'pos_hr'],
    'data': [
        'views/res_config_settings_views.xml',
        'views/pos_session_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_cash_management_control/static/src/css/pos_cash_management_control.css',
            'pos_cash_management_control/static/src/app/**/*',
        ],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
