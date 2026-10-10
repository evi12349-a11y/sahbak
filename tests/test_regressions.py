import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_TEST_DB_DIR = tempfile.TemporaryDirectory(prefix='sahbak-tests-')
os.environ['DB_PATH'] = os.path.join(_TEST_DB_DIR.name, 'test.db')
os.environ['SHABBAT_NOTIFICATIONS'] = 'false'
os.environ['ALLOWED_USERS'] = ''
os.environ['ADMIN_USERS'] = '972501234567'

import main


class RegressionTests(unittest.TestCase):
    def setUp(self):
        with main._connect() as conn:
            for table in ('budget', 'budget_limits', 'allowed_users', 'known_users',
                          'contexts', 'notification_runs', 'feedback',
                          'user_consent', 'notification_deliveries'):
                conn.execute(f'DELETE FROM {table}')
            conn.commit()
        main.ALLOWED_USERS = set()
        main.ADMIN_USERS = {'972501234567'}

    def test_user_feedback_is_saved_and_admin_can_list_and_resolve(self):
        reply = main._try_fast_shortcut('באג: התזכורת לא הגיעה', '972521234567')
        self.assertIn('רשמתי', reply)

        listing = main._try_admin_command('משובים', '972501234567')
        self.assertIn('התזכורת לא הגיעה', listing)
        self.assertIsNone(main._try_admin_command('משובים', '972521234567'))

        with main._connect() as conn:
            fid = conn.execute('SELECT id FROM feedback').fetchone()[0]
        self.assertIn('טופל', main._try_admin_command(f'טופל {fid}', '972501234567'))
        self.assertIn('אין משובים', main._try_admin_command('משובים', '972501234567'))

    def test_feedback_daily_limit(self):
        for i in range(main._FEEDBACK_DAILY_LIMIT):
            main._save_feedback(f'הצעה: רעיון {i}', '972521234567')
        self.assertIn('מחר', main._save_feedback('הצעה: עוד אחת', '972521234567'))

    def test_dashboard_chat_requires_admin_scoped_token_and_returns_reply(self):
        user_id = '972501234567'
        client = main.app.test_client()
        with patch.object(main, 'DASHBOARD_API_KEY', 'master-key'), \
             patch.object(main, 'process_message', return_value='הנה התשובה') as process:
            token = main._dash_token(user_id)
            denied = client.post('/api/chat', json={
                'user_id': user_id, 'message': 'שלום',
            })
            self.assertEqual(denied.status_code, 401)

            response = client.post('/api/chat', json={
                'user_id': user_id, 'message': 'שלום',
            }, headers={'X-Dash-Token': token})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'reply': 'הנה התשובה'})
        process.assert_called_once_with('שלום', user_id, admin_phone='web-dashboard')

    def test_dashboard_chat_rejects_non_admin_and_invalid_message(self):
        user_id = '972521234567'
        client = main.app.test_client()
        with patch.object(main, 'DASHBOARD_API_KEY', 'master-key'):
            token = main._dash_token(user_id)
            admin_token = main._dash_token('972501234567')
            forbidden = client.post('/api/chat', json={
                'user_id': user_id, 'message': 'שלום',
            }, headers={'X-Dash-Token': token})
            missing = client.post('/api/chat', json={
                'user_id': '972501234567', 'message': '   ',
            }, headers={'X-Dash-Token': admin_token})

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(missing.status_code, 400)

    def test_approval_records_consent_and_opt_out_roundtrip(self):
        main._try_admin_command('אשר משתמש 052-123-4567 דני', '972501234567')
        listing = main._try_admin_command('משתמשים', '972501234567')
        self.assertIn('972521234567', listing)
        self.assertIn('אושר', listing)
        self.assertFalse(main.is_opted_out('972521234567'))

        reply = main.process_message('הפסק', '972521234567')
        self.assertIn('הפסקתי', reply)
        self.assertTrue(main.is_opted_out('+972 52-123-4567'))
        self.assertIn('הפסיק', main._try_admin_command('משתמשים', '972501234567'))

        main.process_message('חזור', '972521234567')
        self.assertFalse(main.is_opted_out('972521234567'))

    def test_shabbat_partial_failure_does_not_resend_to_successes(self):
        main.ALLOWED_USERS = {'972521111111', '972522222222'}
        main.record_consent('972521111111')
        main.record_consent('972522222222')
        sent = []

        def fake_send(user, *_args):
            sent.append(user)
            return user == '972521111111'

        with patch.object(main, '_shabbat_notification_for_date', return_value='שבת שלום'), \
             patch.object(main, 'send_whatsapp_template_message', side_effect=fake_send), \
             patch.object(main, 'SHABBAT_TEMPLATE_NAME', 'approved_shabbat'):
            main._send_shabbat_notification()
            main._send_shabbat_notification()

        self.assertEqual(sent.count('972521111111'), 1)

    def test_shabbat_skips_opted_out_users(self):
        main.ALLOWED_USERS = {'972521111111', '972522222222'}
        main.record_consent('972521111111')
        main.record_consent('972522222222')
        main.set_opted_out('972522222222', True)
        sent = []
        with patch.object(main, '_shabbat_notification_for_date', return_value='שבת שלום'), \
             patch.object(main, 'send_whatsapp_template_message',
                          side_effect=lambda u, *args: sent.append(u) or True), \
             patch.object(main, 'SHABBAT_TEMPLATE_NAME', 'approved_shabbat'):
            main._send_shabbat_notification()
        self.assertEqual(sent, ['972521111111'])

    def test_shabbat_requires_an_approved_template(self):
        main.ALLOWED_USERS = {'972521111111'}
        main.record_consent('972521111111')
        with patch.object(main, '_claim_notification_run') as claim, \
             patch.object(main, 'send_whatsapp_message') as text_send, \
             patch.object(main, 'send_whatsapp_template_message') as template_send, \
             patch.object(main, 'SHABBAT_TEMPLATE_NAME', ''):
            main._send_shabbat_notification()

        claim.assert_not_called()
        text_send.assert_not_called()
        template_send.assert_not_called()

    def test_shabbat_requires_recorded_consent_and_uses_template(self):
        main.ALLOWED_USERS = {
            '972521111111', '972522222222', '972523333333'
        }
        main.record_consent('972521111111')
        main.record_consent('972522222222')
        main.set_opted_out('972522222222', True)
        sent = []
        with patch.object(main, '_shabbat_notification_for_date', return_value='שבת שלום'), \
             patch.object(main, 'send_whatsapp_template_message',
                          side_effect=lambda user, *args: sent.append(user) or True), \
             patch.object(main, 'send_whatsapp_message') as text_send, \
             patch.object(main, 'SHABBAT_TEMPLATE_NAME', 'approved_shabbat'):
            main._send_shabbat_notification()

        self.assertEqual(sent, ['972521111111'])
        text_send.assert_not_called()

    def test_admin_can_approve_and_revoke_normalized_phone(self):
        approved = main._try_admin_command(
            'אשר משתמש 052-123-4567 דני', '972501234567')
        self.assertIn('972521234567', approved)
        self.assertTrue(main.is_user_allowed('+972 52 123 4567'))

        main.ALLOWED_USERS = {'972501234567'}
        main._try_admin_command('הסר משתמש 0521234567', '972501234567')
        self.assertFalse(main.is_user_allowed('972521234567'))

    def test_db_approved_user_passes_webhook_allowlist_gate(self):
        main.ALLOWED_USERS = {'972501234567'}
        main.add_allowed_user('972585231231')
        queued = []
        payload = {
            'entry': [{'changes': [{'value': {'messages': [
                {'from': '972585231231', 'id': 'approved-message-1', 'type': 'text'}
            ]}}]}]
        }
        with patch.object(main, '_enqueue_account_message',
                          side_effect=lambda message, phone: queued.append(phone)):
            response = main.app.test_client().post('/webhook', json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(queued, ['972585231231'])

    def test_account_update_webhook_logs_meta_violation_details(self):
        payload = {
            'entry': [{
                'id': '123456789',
                'changes': [{
                    'field': 'account_update',
                    'value': {
                        'event': 'ACCOUNT_VIOLATION',
                        'violation_info': {'violation_type': 'POLICY'},
                        'ban_info': {
                            'waba_ban_state': 'DISABLED',
                            'waba_ban_date': '2026-10-06',
                        },
                    },
                }],
            }],
        }

        with self.assertLogs(main.logger, level='WARNING') as logs:
            response = main.app.test_client().post('/webhook', json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'status': 'ok', 'account_updates': 1})
        self.assertIn('violation_type=POLICY', logs.output[0])
        self.assertIn('ban_state=DISABLED', logs.output[0])

    def test_category_migration_preserves_expenses_and_combines_limits(self):
        with main._connect() as conn:
            conn.executemany(
                'INSERT INTO budget (category, amount, date, description, user_id) '
                'VALUES (?, ?, ?, ?, ?)',
                [('קניות', -20, '2026-09-01', 'shirt', 'u1'),
                 ('נופש', -30, '2026-09-02', 'trip', 'u1')]
            )
            conn.executemany(
                'INSERT INTO budget_limits (user_id, category, amount) VALUES (?, ?, ?)',
                [('u1', 'קניות', 100), ('u1', 'נופש', 250), ('u1', 'שונות', 50)]
            )
            conn.commit()
            before = conn.execute('SELECT SUM(amount) FROM budget').fetchone()[0]
            main._migrate_budget_categories(conn)
            main._migrate_budget_categories(conn)
            after = conn.execute('SELECT SUM(amount) FROM budget').fetchone()[0]
            categories = conn.execute('SELECT DISTINCT category FROM budget').fetchall()
            limit = conn.execute(
                'SELECT amount FROM budget_limits WHERE user_id = ? AND category = ?',
                ('u1', 'שונות')
            ).fetchone()[0]

        self.assertEqual(before, after)
        self.assertEqual(categories, [('שונות',)])
        self.assertEqual(limit, 400)

    def test_dashboard_selectors_use_the_new_category_catalog(self):
        html = (Path(__file__).resolve().parents[1] / 'dashboard.html').read_text(
            encoding='utf-8')
        categories = set(main.VALID_CATEGORIES)
        self.assertTrue(all(f'value="{category}"' in html for category in categories))
        self.assertNotIn('value="מזון"', html)
        self.assertNotIn('value="קניות"', html)

    def test_shabbat_notification_includes_allowed_people_not_in_known_users(self):
        main.ALLOWED_USERS = {'972501234567'}
        main.add_allowed_user('972585231231')
        main.record_consent('972501234567')
        main.record_consent('972585231231')
        with main._connect() as conn:
            conn.execute(
                'INSERT INTO known_users (user_id, first_seen) VALUES (?, ?)',
                ('972501234567', '2026-09-25T12:00:00+03:00')
            )
            conn.commit()

        with patch.object(main, '_claim_notification_run', return_value=True), \
             patch.object(main, '_shabbat_notification_for_date', return_value='Shabbat'), \
             patch.object(main, '_mark_notification_sent') as mark_sent, \
             patch.object(main, 'send_whatsapp_template_message', return_value=True) as send, \
             patch.object(main, 'SHABBAT_TEMPLATE_NAME', 'approved_shabbat'):
            main._send_shabbat_notification()

        self.assertEqual({call.args[0] for call in send.call_args_list},
                         {'972501234567', '972585231231'})
        mark_sent.assert_called_once()

    def test_custom_schedule_time_returns_to_proposal_state(self):
        user_id = 'schedule-test'
        main.set_user_context(user_id, {
            'type': 'pending_schedule_approval',
            'mode': 'sequential_plan',
            'ui': 'proposal',
            'title': 'לימוד',
            'start_time': '2026-09-26T09:00:00+03:00',
            'duration_minutes': 60,
            'remaining_proposals': [],
            'skipped_proposals': [],
            'accepted_proposals': [],
            'alternatives': [{'title': 'לימוד',
                              'start_time': '2026-09-27T09:00:00+03:00',
                              'duration_minutes': 60}],
        })

        main.process_message('שנה מועד', user_id)
        main.process_message('תאריך אחר', user_id)
        with patch.object(main, '_custom_schedule_time_is_free', return_value=True):
            reply = main.process_message('מחר 16:00', user_id)
        context = main.get_user_context(user_id)

        self.assertIn('16:00', reply)
        self.assertEqual(context['ui'], 'proposal')
        self.assertFalse(context['awaiting_custom_time'])

    def test_selected_alternative_waits_for_explicit_approval(self):
        user_id = 'schedule-alternative-test'
        main.set_user_context(user_id, {
            'type': 'pending_schedule_approval',
            'mode': 'sequential_plan',
            'ui': 'alternatives',
            'title': 'לימוד',
            'start_time': '2026-09-26T09:00:00+03:00',
            'duration_minutes': 60,
            'remaining_proposals': [{'title': 'משימה אחרת',
                                     'start_time': '2026-09-27T09:00:00+03:00',
                                     'duration_minutes': 60}],
            'skipped_proposals': [],
            'accepted_proposals': [],
            'alternatives': [{'title': 'לימוד',
                              'start_time': '2026-09-27T09:00:00+03:00',
                              'duration_minutes': 60}],
        })

        with patch.object(main, 'process_calendar_ai', return_value='created') as create:
            main.process_message('1', user_id)
            revised = main.get_user_context(user_id)
            self.assertEqual(revised['ui'], 'proposal')
            self.assertEqual(revised['title'], 'לימוד')
            create.assert_not_called()

            main.process_message('כן', user_id)
            create.assert_called_once()

    def test_single_task_proposal_does_not_create_event_before_approval(self):
        user_id = 'single-proposal-test'
        with patch.object(main, 'process_calendar_ai') as create:
            proposal = main._tool_propose_event({
                'title': 'לימוד',
                'start_time': '2026-09-27T09:00:00+03:00',
                'duration_minutes': 60,
                'explanation': 'חלון פנוי',
            }, user_id)

        self.assertIn('חלון פנוי', proposal)
        self.assertEqual(main.get_user_context(user_id)['ui'], 'proposal')
        create.assert_not_called()

    def test_interactive_skip_id_maps_to_skip_action(self):
        with patch.object(main, 'register_if_new_user', return_value=False), \
             patch.object(main, 'process_message', return_value='ok') as process, \
             patch.object(main, 'get_user_context', return_value=None), \
             patch.object(main, 'send_whatsapp_message', return_value=True):
            main._handle_message_safely_unlocked({
                'type': 'interactive',
                'interactive': {'list_reply': {'id': 'sched_skip', 'title': 'דלג'}},
            }, '972500000000')

        self.assertEqual(process.call_args.args[0], 'דלג')

    def test_schedule_stop_reports_remaining_tasks_and_clears_context(self):
        user_id = 'schedule-stop-test'
        main.set_user_context(user_id, {
            'type': 'pending_schedule_approval',
            'mode': 'sequential_plan',
            'ui': 'proposal',
            'title': 'לימוד',
            'start_time': '2026-09-26T09:00:00+03:00',
            'duration_minutes': 60,
            'remaining_proposals': [{'title': 'קניות',
                                     'start_time': '2026-09-27T09:00:00+03:00',
                                     'duration_minutes': 60}],
            'skipped_proposals': [],
            'accepted_proposals': [],
            'alternatives': [],
        })

        reply = main.process_message('עצור', user_id)

        self.assertIn('קניות', reply)
        self.assertIsNone(main.get_user_context(user_id))

    def test_whatsapp_interactive_payloads_have_expected_shapes(self):
        main.WHATSAPP_TOKEN = 'test-token'
        main.PHONE_NUMBER_ID = 'test-phone-id'
        with patch.object(main, '_post_whatsapp', return_value=True) as post:
            main.send_whatsapp_list_message(
                '972500000000', 'הצעה', 'פעולות',
                [('sched_confirm', 'אשר', 'שבץ'), ('sched_change', 'שנה', 'זמן')]
            )
            list_payload = post.call_args.args[0]
            main.send_whatsapp_cta_url(
                '972500000000', 'שתף', 'פתח יומן', 'https://calendar.google.com/settings'
            )
            cta_payload = post.call_args.args[0]

        self.assertEqual(list_payload['interactive']['type'], 'list')
        self.assertEqual(len(list_payload['interactive']['action']['sections'][0]['rows']), 2)
        self.assertEqual(cta_payload['interactive']['type'], 'cta_url')
        self.assertEqual(cta_payload['interactive']['action']['name'], 'cta_url')

    def test_calendar_link_returns_forwardable_fallback_when_direct_send_fails(self):
        with patch.object(main, '_service_account_email',
                          return_value='bot@example.iam.gserviceaccount.com'), \
             patch.object(main, 'send_whatsapp_cta_url', return_value=False), \
             patch.object(main, 'send_whatsapp_message', return_value=False):
            reply = main._try_admin_command(
                'חבר יומן 972500000000 friend@gmail.com', '972501234567')

        self.assertIn('לא הצלחתי לשלוח', reply)
        self.assertIn('calendar.google.com', reply)
        self.assertIn('bot@example.iam.gserviceaccount.com', reply)


if __name__ == '__main__':
    unittest.main()
