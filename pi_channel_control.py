"""Exact, opt-in Adapter Framework communication channel control."""

import json
import os
import re
import xml.etree.ElementTree as ET

from requests.exceptions import RequestException


def register(mcp, base_url, credentials, http):
    def control(action, party, component, channel, dry_run, confirmation, second_confirmation):
        # Restrict identifiers to literal names; only party can be empty.
        if (not component or not channel or any(
                not re.fullmatch(r"[A-Za-z0-9_.:/~-]*", value)
                for value in (party, component, channel))):
            raise ValueError("Use exact literal identifiers; only party may be empty")

        environment = os.environ.get("SAP_PI_ENVIRONMENT", "").lower()
        target = {"party": party, "service": component, "channel": channel}
        phrase = f"{environment.upper()} {action.upper()} " + json.dumps(
            [party, component, channel], separators=(",", ":"))
        prod_phrase = "CONFIRM PROD " + phrase[5:]
        result = dict(target, action=action, environment=environment or "unset",
                      dry_run=dry_run, submitted=False)
        if dry_run:
            return dict(result, required_confirmation=phrase,
                        required_second_confirmation=prod_phrase if environment == "prod" else None)
        if environment not in {"dev", "test", "prod"}:
            raise ValueError("SAP_PI_ENVIRONMENT must be dev, test, or prod")
        if os.environ.get("SAP_PI_ENABLE_CHANNEL_CONTROL", "").lower() != "true":
            raise ValueError("Channel writes are disabled")
        if confirmation != phrase:
            raise ValueError("Explicit user approval of the exact preview is required")
        if environment == "prod" and (
                os.environ.get("SAP_PI_ENABLE_PROD_CHANNEL_CONTROL", "").lower() != "true"
                or second_confirmation != prod_phrase):
            raise ValueError("PROD writes require separate opt-in and a second separate user approval")

        url = base_url() + "/AdapterFramework/ChannelAdminServlet"

        def request(method, verb):
            response = method(url, params=dict(target, action=verb), auth=credentials,
                              timeout=10, allow_redirects=False)
            if response.status_code != 200:
                raise ValueError(f"SAP returned HTTP {response.status_code}")
            return response

        def status():
            response = request(http.get, "status")
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError:
                raise ValueError("Invalid SAP status XML") from None
            for elem in root.iter():
                elem.tag = elem.tag.rsplit("}", 1)[-1]
            rows = list(root.iter("Channel"))
            if root.tag != "ChannelAdminResult" or len(rows) != 1:
                raise ValueError("SAP did not return exactly one channel")
            row = rows[0]
            for tag, expected in zip(("Party", "Service", "ChannelName"), target.values()):
                fields = row.findall(tag)
                if len(fields) != 1 or (fields[0].text or "") != expected:
                    raise ValueError("SAP returned a different or ambiguous channel identity")
            state = row.findtext("ActivationState")
            if state not in {"STARTED", "STOPPED"}:
                raise ValueError("SAP activation state is unrecognized")
            return state, row.findtext("Control") == "WEBSERVICE"

        desired = "STARTED" if action == "start" else "STOPPED"
        try:
            before, external = status()
        except (RequestException, ValueError):
            return dict(result, verified=False, error="Exact channel status precheck failed")
        if before == desired:
            return dict(result, before=before, after=before, verified=True)
        if not external:
            return dict(result, before=before, verified=False,
                        error="Channel must be in SAP External Control mode")

        write_response_ok = False
        try:
            request(http.post, action)
            write_response_ok = True
        except (RequestException, ValueError):
            pass  # The write may have reached SAP. Verify without replaying it.
        try:
            after, _ = status()
        except (RequestException, ValueError):
            after = None
        return dict(result, submitted=True, before=before, after=after,
                    write_http_ok=write_response_ok, verified=after == desired,
                    error=None if after == desired else
                    "Requested state not verified; inspect SAP status before retrying")

    @mcp.tool()
    def start_pi_channel(party: str, component: str, channel: str, dry_run: bool = True,
                         confirmation: str = "", second_confirmation: str = "") -> dict:
        """Preview starting one exact SAP PI channel. Use empty party for a partyless channel.
        To execute, obtain explicit user approval, pass the preview confirmation and set
        dry_run=False. PROD requires a second separate user approval and confirmation.
        Verification checks activation state; it does not establish adapter health.
        """
        return control("start", party, component, channel, dry_run, confirmation, second_confirmation)

    @mcp.tool()
    def stop_pi_channel(party: str, component: str, channel: str, dry_run: bool = True,
                        confirmation: str = "", second_confirmation: str = "") -> dict:
        """Preview stopping one exact SAP PI channel. Use empty party for a partyless channel.
        To execute, obtain explicit user approval, pass the preview confirmation and set
        dry_run=False. PROD requires a second separate user approval and confirmation.
        """
        return control("stop", party, component, channel, dry_run, confirmation, second_confirmation)
