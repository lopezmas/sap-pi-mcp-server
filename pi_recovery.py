"""Adapter Engine recovery; request shapes verified against the tenant WSDL.

Pablo Lopez | 2026-11-02 | EOIO inspection, verification, and gated recovery.
"""
import datetime
import json
import os
import xml.etree.ElementTree as ET

SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'
API = 'urn:AdapterMessageMonitoringVi'
DATA = 'urn:com.sap.aii.mdt.server.adapterframework.ws'
ARRAY = 'urn:java/lang'


def element(parent, namespace, name, value=None):
    child = ET.SubElement(parent, '{%s}%s' % (namespace, name))
    if value is not None:
        child.text = str(value)
    return child


def rows(root):
    result = []
    for node in root.iter():
        children = {c.tag.split('}')[-1]: c for c in node}
        if 'messageKey' not in children or 'status' not in children:
            continue
        row = {}
        for name in ('messageKey', 'messageID', 'status', 'direction', 'qualityOfService',
                     'sequenceID', 'sequenceNumber', 'serializationContext', 'senderName',
                     'receiverName', 'startTime', 'errorCode', 'errorCategory', 'restartable', 'cancelable'):
            child = children.get(name)
            if child is not None:
                value = next((v.text for v in child if v.tag.split('}')[-1] == 'value'), child.text)
                row[name] = value
        result.append(row)
    return result


def register(mcp, base_url, credentials, http):
    def call(operation, fill):
        envelope = ET.Element('{%s}Envelope' % SOAP)
        body = element(envelope, SOAP, 'Body')
        request = element(body, API, operation)
        fill(request)
        response = http.post(
            base_url() + '/AdapterMessageMonitoring/basic?style=document',
            data=ET.tostring(envelope, encoding='utf-8', xml_declaration=True),
            headers={'Content-Type': 'text/xml;charset=UTF-8', 'SOAPAction': operation},
            auth=credentials, timeout=30)
        # Inspect SOAP faults even when SAP returns HTTP 500. Never retry writes.
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            raise RuntimeError('SAP returned non-XML; HTTP %s' % response.status_code)
        if root.find('.//{%s}Fault' % SOAP) is not None:
            raise RuntimeError('SAP SOAP fault for %s; HTTP %s. Check SAP service logs.' %
                               (operation, response.status_code))
        response.raise_for_status()
        if root.find('.//{%s}%sResponse' % (API, operation)) is None:
            raise RuntimeError('Unexpected SOAP response for ' + operation)
        return root

    @mcp.tool()
    def inspect_eoio_messages(hours_back: int = 24, max_messages: int = 100,
                              sender_component: str = '', sequence_id: str = '', status: str = '',
                              serialization_context: str = '', direction: str = '',
                              receiver_component: str = '') -> str:
        """Read bounded EOIO messages including holding/error states and sequence metadata.
        Results are candidates, not proof of a blocked queue or its first message.
        hours_back=0 omits the time filter. Use sequence/context/direction filters
        to inspect older blockers; SAP's predecessor lookup may return a logged parent.
        """
        if not 0 <= hours_back <= 8760 or not 1 <= max_messages <= 1000:
            raise ValueError('hours_back must be 0..8760; max_messages must be 1..1000')
        now = datetime.datetime.now(datetime.timezone.utc)

        def fill(request):
            f = element(request, API, 'filter')
            values = [('archive', 'false'), ('dateType', 0)]
            if direction:
                values.append(('direction', direction))
            if hours_back:
                values.append(('fromTime', (now - datetime.timedelta(hours=hours_back)).isoformat()))
            values += [('nodeId', 0), ('onlyFaultyMessages', 'false'), ('qualityOfService', 'EOIO')]
            if receiver_component:
                values.append(('receiverName', receiver_component))
            values += [('retries', 0), ('retryInterval', 0)]
            if sender_component:
                values.append(('senderName', sender_component))
            if sequence_id:
                values.append(('sequenceID', sequence_id))
            if serialization_context:
                values.append(('serializationContext', serialization_context))
            if status:
                values.append(('status', status))
            values.append(('timesFailed', 0))
            if hours_back:
                values.append(('toTime', now.isoformat()))
            values.append(('wasEdited', 'false'))
            for name, value in values:
                element(f, DATA, name, value)
            element(request, API, 'maxMessages', max_messages)

        messages = rows(call('getMessageList', fill))
        return json.dumps({'messages': messages, 'count': len(messages),
                           'possibly_truncated': len(messages) >= max_messages,
                           'coverage': 'Bounded database search; not a complete queue inventory.',
                           'hours_back': hours_back}, indent=2)

    @mcp.tool()
    def get_eoio_predecessor(message_id: str, direction: str) -> str:
        """Read SAP's predecessor ID using the message's returned direction.
        May identify a logged parent (logVersion), not the blocking queue entry.
        Inspect the complete sequence/context before selecting a recovery target.
        """
        if not message_id.strip() or not direction.strip():
            raise ValueError('message_id and direction are required')

        def fill(request):
            element(request, API, 'messageId', message_id)
            element(request, API, 'direction', direction)

        root = call('getPredecessorMessageId', fill)
        value = root.find('.//{%s}Response' % API)
        return json.dumps({'message_id': message_id, 'direction': direction,
                           'predecessor_id': value.text if value is not None else None})

    def validate_key(message_key):
        if not message_key.strip() or len(message_key) > 512:
            raise ValueError('Provide the exact messageKey returned by SAP')

    def lookup(message_key):
        validate_key(message_key)

        def fill(request):
            keys = element(request, API, 'filter')
            element(keys, ARRAY, 'String', message_key)
            element(request, API, 'maxMessages', 2)

        current = rows(call('getMessagesByKeys', fill))
        if len(current) != 1 or current[0].get('messageKey') != message_key:
            raise ValueError('SAP must return exactly this message')
        return current[0]

    @mcp.tool()
    def get_pi_message_status(message_key: str) -> str:
        """Read current message status, sequence metadata and recovery eligibility without payload."""
        return json.dumps(lookup(message_key), indent=2)

    def recover(operation, verb, flag, message_key, dry_run, confirmation, second_confirmation):
        validate_key(message_key)
        environment = os.environ.get('SAP_PI_ENVIRONMENT', '').lower()
        preview = {'operation': operation, 'message_key': message_key,
                   'environment': environment or 'unset', 'dry_run': dry_run}
        if dry_run:
            return json.dumps(preview)
        if os.environ.get('SAP_PI_ENABLE_RECOVERY', '').lower() != 'true':
            raise ValueError('Recovery writes are disabled')
        if environment not in ('dev', 'test', 'prod'):
            raise ValueError('SAP_PI_ENVIRONMENT must be dev, test, or prod')
        if confirmation != verb + ' ' + message_key:
            raise ValueError('Explicit user confirmation required: ' + verb + ' <messageKey>')
        if environment == 'prod' and second_confirmation != 'CONFIRM PROD ' + verb + ' ' + message_key:
            raise ValueError('Second separate user confirmation required: CONFIRM PROD ' + verb + ' <messageKey>')

        current = lookup(message_key)
        if current.get(flag) != 'true':
            raise ValueError('SAP must report ' + flag + '=true')
        if current.get('status') in ('success', 'canceled', 'cancelled', 'logVersion', 'delivering', 'toBeDelivered'):
            raise ValueError('Message is terminal or progressing; inspect status instead of recovery')

        def fill(request):
            keys = element(request, API, 'messageKeys')
            element(keys, ARRAY, 'String', message_key)

        root = call(operation, fill)
        outcomes = [node.text for node in root.iter() if node.tag == '{%s}successful' % DATA]
        result_keys = root.findall('.//{%s}keyList/{%s}String' % (DATA, ARRAY))
        accepted = outcomes == ['true'] and [node.text for node in result_keys] == [message_key]
        return json.dumps({'submitted': True, 'accepted': accepted, 'operation': operation,
                           'message_key': message_key,
                           'sap_result_xml': ET.tostring(root, encoding='unicode'),
                           'verification_required': 'Inspect SAP per-message result and subsequent status; submission is not completion.'})

    @mcp.tool()
    def restart_pi_message(message_key: str, dry_run: bool = True,
                           confirmation: str = '', second_confirmation: str = '') -> str:
        """Preview or restart ONE existing message via SAP resendMessages.
        Requires explicit approval and two separate confirmations for PROD.
        Writes require SAP_PI_ENABLE_RECOVERY=true and SAP_PI_ENVIRONMENT=dev/test/prod.
        confirmation: RESTART <messageKey>; second_confirmation: CONFIRM PROD RESTART <messageKey>.
        Does not skip predecessors. A timeout has unknown outcome; inspect before retry.
        accepted=true means SAP accepted the request, not successful business delivery.
        """
        return recover('resendMessages', 'RESTART', 'restartable', message_key,
                       dry_run, confirmation, second_confirmation)

    @mcp.tool()
    def cancel_pi_message(message_key: str, dry_run: bool = True,
                          confirmation: str = '', second_confirmation: str = '') -> str:
        """Preview or cancel ONE message via SAP cancelMessages, skipping its delivery.
        Requires explicit cancellation approval and two separate confirmations for PROD.
        Writes require SAP_PI_ENABLE_RECOVERY=true and SAP_PI_ENVIRONMENT=dev/test/prod.
        confirmation: CANCEL <messageKey>; second_confirmation: CONFIRM PROD CANCEL <messageKey>.
        Retry the blocker first, verify it still fails or remains waiting, then cancel
        only under the user's approved policy. Do not cancel progressing messages.
        Verify canceled status before restarting its successor. No automatic retries.
        """
        return recover('cancelMessages', 'CANCEL', 'cancelable', message_key,
                       dry_run, confirmation, second_confirmation)
