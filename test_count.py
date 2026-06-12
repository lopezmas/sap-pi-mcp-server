import requests
import datetime
import xml.etree.ElementTree as ET
import os


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main():
    pi_host = required_env("SAP_PI_HOST")
    pi_user = required_env("SAP_PI_USER")
    pi_pwd = required_env("SAP_PI_PASSWORD")
    pi_scheme = os.environ.get("SAP_PI_SCHEME", "http").strip().lower()
    if pi_host.startswith(("http://", "https://")):
        pi_base_url = pi_host.rstrip("/")
    else:
        if pi_scheme not in ("http", "https"):
            raise RuntimeError("SAP_PI_SCHEME must be either http or https")
        pi_base_url = f"{pi_scheme}://{pi_host.rstrip('/')}"
    now = datetime.datetime.utcnow()
    start_time = (now - datetime.timedelta(hours=1)).isoformat() + 'Z'
    end_time = now.isoformat() + 'Z'

    soap_body = f"""
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" 
                  xmlns:urn="urn:AdapterMessageMonitoringVi" 
                  xmlns:urn1="urn:com.sap.aii.mdt.server.adapterframework.ws">
   <soapenv:Body>
      <urn:getMessageList>
         <filter>
            <urn1:archive>false</urn1:archive>
            <urn1:dateType>0</urn1:dateType>
            <urn1:nodeId>0</urn1:nodeId>
            <urn1:onlyFaultyMessages>false</urn1:onlyFaultyMessages>
            <urn1:retries>0</urn1:retries>
            <urn1:retryInterval>0</urn1:retryInterval>
            <urn1:timesFailed>0</urn1:timesFailed>
            <urn1:wasEdited>false</urn1:wasEdited>
            <urn1:status>success</urn1:status>
            <urn1:fromTime>{start_time}</urn1:fromTime>
            <urn1:toTime>{end_time}</urn1:toTime>
         </filter>
         <maxMessages>10000</maxMessages>
      </urn:getMessageList>
   </soapenv:Body>
</soapenv:Envelope>
"""
    headers = {'Content-Type': 'text/xml;charset=UTF-8', 'SOAPAction': 'getMessageList'}
    url = f'{pi_base_url}/AdapterMessageMonitoring/basic?style=document'
    try:
        response = requests.post(url, data=soap_body, headers=headers, auth=(pi_user, pi_pwd), timeout=30)
        count = response.text.count('messageKey') // 2
        if 'messageKey' in response.text:
            print(f'Processed messages (last hour): {count}')
        else:
            print(response.text)
    except Exception as e:
        print(f'Error: {e}')


if __name__ == "__main__":
    main()
