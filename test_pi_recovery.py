import json
import os
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from mcp.server.fastmcp import FastMCP
from pi_recovery import register, SOAP, API, DATA, ARRAY


def response(operation, content=''):
    r = Mock(status_code=200)
    r.content = ('<s:Envelope xmlns:s="%s" xmlns:a="%s" xmlns:d="%s">'
                 '<s:Body><a:%sResponse>%s</a:%sResponse></s:Body></s:Envelope>' %
                 (SOAP, API, DATA, operation, content, operation)).encode()
    return r


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        m = FastMCP('test')
        register(m, lambda: 'https://example.invalid', ('test', 'test'), self.http)
        self.tools = m._tools

    def test_preview_and_disabled_write_never_connect(self):
        self.assertTrue(json.loads(self.tools['restart_pi_message']('key'))['dry_run'])
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            self.tools['restart_pi_message']('key', False)
        self.http.post.assert_not_called()

    def test_prod_requires_second_confirmation_before_network(self):
        with patch.dict(os.environ, {'SAP_PI_ENABLE_RECOVERY': 'true', 'SAP_PI_ENVIRONMENT': 'prod'}):
            with self.assertRaises(ValueError):
                self.tools['restart_pi_message']('key', False, 'RESTART key')
        self.http.post.assert_not_called()

    def test_not_restartable_prevents_resend(self):
        self.http.post.return_value = response('getMessagesByKeys',
            '<d:item><d:messageKey>key</d:messageKey><d:status>holding</d:status>'
            '<d:restartable><d:value>false</d:value></d:restartable></d:item>')
        with patch.dict(os.environ, {'SAP_PI_ENABLE_RECOVERY': 'true', 'SAP_PI_ENVIRONMENT': 'test'}):
            with self.assertRaises(ValueError):
                self.tools['restart_pi_message']('key', False, 'RESTART key')
        self.assertEqual(self.http.post.call_count, 1)

    def test_resend_one_key_and_do_not_retry_timeout(self):
        self.http.post.side_effect = [response('getMessagesByKeys',
            '<d:item><d:messageKey>k&amp;x</d:messageKey><d:status>error</d:status>'
            '<d:restartable><d:value>true</d:value></d:restartable></d:item>'), TimeoutError()]
        with patch.dict(os.environ, {'SAP_PI_ENABLE_RECOVERY': 'true', 'SAP_PI_ENVIRONMENT': 'test'}):
            with self.assertRaises(TimeoutError):
                self.tools['restart_pi_message']('k&x', False, 'RESTART k&x')
        self.assertEqual(self.http.post.call_count, 2)
        root = ET.fromstring(self.http.post.call_args.kwargs['data'])
        self.assertEqual(root.find('.//{%s}String' % ARRAY).text, 'k&x')

    def test_inspection_filter_namespaces_order_and_limit(self):
        self.http.post.return_value = response('getMessageList')
        self.tools['inspect_eoio_messages'](24, 5, 'sender', 'sequence', 'holding')
        root = ET.fromstring(self.http.post.call_args.kwargs['data'])
        f = root.find('.//{%s}filter' % API)
        self.assertTrue(all(c.tag.startswith('{%s}' % DATA) for c in f))
        names = [c.tag.split('}')[-1] for c in f]
        self.assertLess(names.index('sequenceID'), names.index('status'))
        self.assertEqual(root.find('.//{%s}maxMessages' % API).text, '5')
        self.assertEqual(f.find('{%s}onlyFaultyMessages' % DATA).text, 'false')

    def test_soap_fault_on_http_500_is_not_success(self):
        r = response('getMessageList')
        r.status_code = 500
        r.content = ('<s:Envelope xmlns:s="%s"><s:Body><s:Fault/></s:Body></s:Envelope>' % SOAP).encode()
        self.http.post.return_value = r
        with self.assertRaisesRegex(RuntimeError, 'SOAP fault'):
            self.tools['inspect_eoio_messages']()

    def test_all_age_sequence_inspection_omits_dates_and_preserves_wsdl_order(self):
        self.http.post.return_value = response('getMessageList')
        self.tools['inspect_eoio_messages'](0, 10, 'sender', 'seq', '', 'ctx', 'OUTBOUND', 'receiver')
        root = ET.fromstring(self.http.post.call_args.kwargs['data'])
        f = root.find('.//{%s}filter' % API)
        names = [c.tag.split('}')[-1] for c in f]
        self.assertNotIn('fromTime', names)
        self.assertNotIn('toTime', names)
        self.assertLess(names.index('direction'), names.index('nodeId'))
        self.assertLess(names.index('receiverName'), names.index('retries'))
        self.assertLess(names.index('sequenceID'), names.index('serializationContext'))

    def test_result_limit_signals_incomplete_search(self):
        self.http.post.return_value = response('getMessageList',
            '<d:item><d:messageKey>key</d:messageKey><d:status>holding</d:status></d:item>')
        result = json.loads(self.tools['inspect_eoio_messages'](0, 1))
        self.assertTrue(result['possibly_truncated'])

    def test_status_reads_nested_eligibility_and_missing_key_fails(self):
        self.http.post.return_value = response('getMessagesByKeys',
            '<d:item><d:messageKey>key</d:messageKey><d:status>waiting</d:status>'
            '<d:cancelable><d:value>true</d:value></d:cancelable></d:item>')
        result = json.loads(self.tools['get_pi_message_status']('key'))
        self.assertEqual(result['cancelable'], 'true')
        with self.assertRaises(ValueError):
            self.tools['get_pi_message_status']('other')

    def test_cancel_preview_disabled_and_both_prod_confirmations(self):
        result = json.loads(self.tools['cancel_pi_message']('key'))
        self.assertEqual(result['operation'], 'cancelMessages')
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            self.tools['cancel_pi_message']('key', False)
        with patch.dict(os.environ, {'SAP_PI_ENABLE_RECOVERY': 'true', 'SAP_PI_ENVIRONMENT': 'prod'}):
            for first, second in [('', ''), ('CANCEL key', ''), ('RESTART key', 'CONFIRM PROD CANCEL key')]:
                with self.assertRaises(ValueError):
                    self.tools['cancel_pi_message']('key', False, first, second)
        self.http.post.assert_not_called()

    def test_restart_and_cancel_refuse_terminal_or_progressing_messages(self):
        with patch.dict(os.environ, {'SAP_PI_ENABLE_RECOVERY': 'true', 'SAP_PI_ENVIRONMENT': 'test'}):
            for tool, verb in [('restart_pi_message', 'RESTART'), ('cancel_pi_message', 'CANCEL')]:
                for status in ('success', 'canceled', 'logVersion', 'delivering', 'toBeDelivered'):
                    self.http.reset_mock()
                    self.http.post.return_value = response('getMessagesByKeys',
                        '<d:item><d:messageKey>key</d:messageKey><d:status>%s</d:status>'
                        '<d:cancelable><d:value>true</d:value></d:cancelable>'
                        '<d:restartable><d:value>true</d:value></d:restartable></d:item>' % status)
                    with self.assertRaises(ValueError):
                        self.tools[tool]('key', False, verb + ' key')
                    self.assertEqual(self.http.post.call_count, 1)

    def test_cancel_accepted_rejected_and_mismatched_key_results(self):
        with patch.dict(os.environ, {'SAP_PI_ENABLE_RECOVERY': 'true', 'SAP_PI_ENVIRONMENT': 'prod'}):
            for outcome, key, accepted in [('true', 'key', True), ('false', 'key', False), ('true', 'other', False)]:
                self.http.post.side_effect = [response('getMessagesByKeys',
                    '<d:item><d:messageKey>key</d:messageKey><d:status>waiting</d:status>'
                    '<d:cancelable><d:value>true</d:value></d:cancelable></d:item>'),
                    response('cancelMessages',
                        '<d:keyList xmlns:j="%s"><j:String>%s</j:String></d:keyList>'
                        '<d:resultList><d:AdminActionResult><d:successful>%s</d:successful>'
                        '</d:AdminActionResult></d:resultList>' % (ARRAY, key, outcome))]
                result = json.loads(self.tools['cancel_pi_message'](
                    'key', False, 'CANCEL key', 'CONFIRM PROD CANCEL key'))
                self.assertEqual(result['accepted'], accepted)
                self.assertEqual(self.http.post.call_args.kwargs['headers']['SOAPAction'], 'cancelMessages')

    def test_cancel_disallowed_by_sap_and_unknown_environment_make_no_write(self):
        self.http.post.return_value = response('getMessagesByKeys',
            '<d:item><d:messageKey>key</d:messageKey><d:status>waiting</d:status>'
            '<d:cancelable><d:value>false</d:value></d:cancelable></d:item>')
        with patch.dict(os.environ, {'SAP_PI_ENABLE_RECOVERY': 'true', 'SAP_PI_ENVIRONMENT': 'test'}):
            with self.assertRaises(ValueError):
                self.tools['cancel_pi_message']('key', False, 'CANCEL key')
        self.assertEqual(self.http.post.call_count, 1)
        self.http.reset_mock()
        with patch.dict(os.environ, {'SAP_PI_ENABLE_RECOVERY': 'true', 'SAP_PI_ENVIRONMENT': 'unknown'}):
            with self.assertRaises(ValueError):
                self.tools['restart_pi_message']('key', False, 'RESTART key')
        self.http.post.assert_not_called()

    def test_predecessor_lookup_returns_id_without_assuming_queue_order(self):
        self.http.post.return_value = response('getPredecessorMessageId', '<a:Response>parent-id</a:Response>')
        result = json.loads(self.tools['get_eoio_predecessor']('message-id', 'OUTBOUND'))
        self.assertEqual(result['predecessor_id'], 'parent-id')

    def test_new_tools_are_exposed_through_mcp(self):
        m = FastMCP('test')
        register(m, lambda: 'https://example.invalid', ('test', 'test'), self.http)
        result = m._handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        names = {t['name'] for t in result['result']['tools']}
        self.assertEqual(names, {'inspect_eoio_messages', 'get_eoio_predecessor',
                                'get_pi_message_status', 'restart_pi_message', 'cancel_pi_message'})


if __name__ == '__main__':
    unittest.main()
