import requests
import xml.etree.ElementTree as ET
import os
from xml.sax.saxutils import escape as escape_xml


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def env_set(name):
    value = os.environ.get(name, "")
    return {item.strip() for item in value.split(",") if item.strip()}


PI_HOST = required_env("SAP_PI_HOST")
PI_USER = required_env("SAP_PI_USER")
PI_PWD = required_env("SAP_PI_PASSWORD")
PI_SCHEME = os.environ.get("SAP_PI_SCHEME", "http").strip().lower()
INTERNAL_COMPONENTS = env_set("SAP_PI_INTERNAL_COMPONENTS")
DESCOPED_COMPONENTS = env_set("SAP_PI_DESCOPED_COMPONENTS")


def pi_base_url():
    host = PI_HOST.rstrip("/")
    if host.startswith(("http://", "https://")):
        return host
    if PI_SCHEME not in ("http", "https"):
        raise RuntimeError("SAP_PI_SCHEME must be either http or https")
    return f"{PI_SCHEME}://{host}"


url = f"{pi_base_url()}/IntegratedConfigurationInService/IntegratedConfigurationInImplBean"
headers = {'Content-Type': 'text/xml;charset=UTF-8', 'SOAPAction': 'http://sap.com/xi/WebService/soap1.1'}

query_soap = """
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:bas="http://sap.com/xi/BASIS">
   <soapenv:Header/>
   <soapenv:Body>
      <bas:IntegratedConfigurationQueryRequest>
      </bas:IntegratedConfigurationQueryRequest>
   </soapenv:Body>
</soapenv:Envelope>
"""

print("Querying all ICOs...")
resp = requests.post(url, data=query_soap, headers=headers, auth=(PI_USER, PI_PWD), timeout=30)
root = ET.fromstring(resp.text)

icos = root.findall('.//IntegratedConfigurationID')
print(f"Found {len(icos)} ICOs.")

batch_size = 50
md_lines = []
md_lines.append("# SAP PI Integrated Configurations (ICOs)")
md_lines.append(f"**Total Count:** {len(icos)}\n")
md_lines.append("| Sender Component | Interface Name | Interface Namespace | Sync/Async | Receiver Component | Operation Mapping | Sender Channel | Receiver Channel |")
md_lines.append("|---|---|---|---|---|---|---|---|")

def escape_md(s):
    if not s: return ""
    return s.replace('|', '&#124;')

def chunker(seq, size):
    return (seq[pos:pos + size] for pos in range(0, len(seq), size))

rows = []

for batch in chunker(icos, batch_size):
    read_req = ['<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:bas="http://sap.com/xi/BASIS"><soapenv:Header/><soapenv:Body><bas:IntegratedConfigurationReadRequest><ReadContext>Active</ReadContext>']
    for ico_id in batch:
        sender_party = ico_id.findtext('SenderPartyID', default='')
        sender_comp = ico_id.findtext('SenderComponentID', default='')
        intf_name = ico_id.findtext('InterfaceName', default='')
        intf_ns = ico_id.findtext('InterfaceNamespace', default='')
        rec_party = ico_id.findtext('ReceiverPartyID', default='')
        rec_comp = ico_id.findtext('ReceiverComponentID', default='')
        
        read_req.append("<IntegratedConfigurationID>")
        if sender_party: read_req.append(f"<SenderPartyID>{escape_xml(sender_party)}</SenderPartyID>")
        read_req.append(f"<SenderComponentID>{escape_xml(sender_comp)}</SenderComponentID>")
        read_req.append(f"<InterfaceName>{escape_xml(intf_name)}</InterfaceName>")
        read_req.append(f"<InterfaceNamespace>{escape_xml(intf_ns)}</InterfaceNamespace>")
        if rec_party: read_req.append(f"<ReceiverPartyID>{escape_xml(rec_party)}</ReceiverPartyID>")
        if rec_comp: read_req.append(f"<ReceiverComponentID>{escape_xml(rec_comp)}</ReceiverComponentID>")
        read_req.append("</IntegratedConfigurationID>")

    read_req.append('</bas:IntegratedConfigurationReadRequest></soapenv:Body></soapenv:Envelope>')
    
    read_soap = "".join(read_req)
    read_resp = requests.post(url, data=read_soap, headers=headers, auth=(PI_USER, PI_PWD), timeout=60)
    
    try:
        read_root = ET.fromstring(read_resp.text)
    except Exception as e:
        print("Error parsing read response")
        continue

    for ic in read_root.findall('.//IntegratedConfiguration'):
        sender_comp = ic.findtext('.//IntegratedConfigurationID/SenderComponentID', default='')
        intf_name = ic.findtext('.//IntegratedConfigurationID/InterfaceName', default='')
        intf_ns = ic.findtext('.//IntegratedConfigurationID/InterfaceNamespace', default='')
        snd_channel = ic.findtext('.//InboundProcessing/CommunicationChannel/ChannelID', default='')
        
        receivers = ic.findall('.//ReceiverInterfaces')
        for rec in receivers:
            rec_comp = rec.findtext('.//Receiver/ComponentID', default='')
            qos = rec.findtext('.//QualityOfService', default='')
            sync_async = "Async" if qos in ['EO', 'EOIO'] else "Sync" if qos == 'BE' else qos
            op_mapping = rec.findtext('.//ReceiverInterfaceRule/Mapping/Name', default='')
            
            out_channel = ''
            for out_p in ic.findall('.//OutboundProcessing'):
                if out_p.findtext('.//Receiver/ComponentID') == rec_comp:
                    out_channel = out_p.findtext('.//CommunicationChannel/ChannelID', default='')
                    break
            
            rec_type = ''
            if out_channel:
                parts = out_channel.split('_')
                if len(parts) > 1:
                    rec_type = parts[1].upper()
                    
            snd_type = ''
            if snd_channel:
                parts = snd_channel.split('_')
                if len(parts) > 1:
                    snd_type = parts[1].upper()
                    
            in_scope = 'Descoped' if sender_comp in DESCOPED_COMPONENTS or rec_comp in DESCOPED_COMPONENTS else 'In Scope'
            
            direction = 'Unknown'
            if sender_comp in INTERNAL_COMPONENTS:
                direction = 'Outbound'
            elif rec_comp in INTERNAL_COMPONENTS:
                direction = 'Inbound'
            else:
                direction = 'External to External'
            
            rows.append({
                'sender_comp': sender_comp,
                'intf_name': intf_name,
                'intf_ns': intf_ns,
                'sync_async': sync_async,
                'rec_comp': rec_comp,
                'op_mapping': op_mapping,
                'snd_channel': snd_channel,
                'snd_type': snd_type,
                'out_channel': out_channel,
                'rec_type': rec_type,
                'in_scope': in_scope,
                'direction': direction
            })

rows.sort(key=lambda x: x['sender_comp'].lower())

descoped_count = sum(1 for r in rows if r['in_scope'] == 'Descoped')

md_lines = []
md_lines.append("# SAP PI Integrated Configurations (ICOs)")
md_lines.append(f"**Total Count:** {len(icos)}")
md_lines.append(f"**Descoped Count:** {descoped_count}\n")
md_lines.append("| Sender Component | Interface Name | Interface Namespace | Sync/Async | Receiver Component | Operation Mapping | Sender Channel | Sender Channel Type | Receiver Channel | Receiver Channel Type | Direction | In Scope |")
md_lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")

for r in rows:
    md_lines.append(f"| {escape_md(r['sender_comp'])} | {escape_md(r['intf_name'])} | {escape_md(r['intf_ns'])} | {escape_md(r['sync_async'])} | {escape_md(r['rec_comp'])} | {escape_md(r['op_mapping'])} | {escape_md(r['snd_channel'])} | {escape_md(r['snd_type'])} | {escape_md(r['out_channel'])} | {escape_md(r['rec_type'])} | {escape_md(r['direction'])} | {escape_md(r['in_scope'])} |")

with open('all_icos.md', 'w', encoding='utf-8') as f:
    f.write("\n".join(md_lines))

import csv
csv_headers = ["Sender Component", "Interface Name", "Interface Namespace", "Sync/Async", "Receiver Component", "Operation Mapping", "Sender Channel", "Sender Channel Type", "Receiver Channel", "Receiver Channel Type", "Direction", "In Scope"]
with open('all_icos.csv', 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(csv_headers)
    for r in rows:
        writer.writerow([r['sender_comp'], r['intf_name'], r['intf_ns'], r['sync_async'], r['rec_comp'], r['op_mapping'], r['snd_channel'], r['snd_type'], r['out_channel'], r['rec_type'], r['direction'], r['in_scope']])

print("Done. Saved to all_icos.md")
