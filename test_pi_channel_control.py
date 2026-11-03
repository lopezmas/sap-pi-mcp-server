import os
import unittest
from unittest.mock import Mock, patch

from requests.exceptions import Timeout

from pi_channel_control import register


def status(state, party="", control="WEBSERVICE"):
    return Mock(status_code=200, content=f"""
    <ChannelAdminResult><Channels><Channel>
    <Party>{party}</Party><Service>COMP</Service><ChannelName>CC</ChannelName>
    <ActivationState>{state}</ActivationState><Control>{control}</Control>
    </Channel></Channels></ChannelAdminResult>""".encode())


class ChannelControlTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"SAP_PI_ENVIRONMENT": "test"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        mcp, self.http = Mock(), Mock()
        self.tools = {}
        mcp.tool.return_value = lambda f: self.tools.setdefault(f.__name__, f)
        register(mcp, lambda: "https://pi.invalid", ("fake", "fake"), self.http)
        self.args = ("", "COMP", "CC")
        self.start = self.tools["start_pi_channel"]
        self.stop = self.tools["stop_pi_channel"]

    def execute(self, tool, **kwargs):
        preview = tool(*self.args)
        return tool(*self.args, dry_run=False,
                    confirmation=preview["required_confirmation"], **kwargs)

    def test_preview_and_invalid_targets_make_no_requests(self):
        self.assertFalse(self.start(*self.args)["submitted"])
        for target in [("*", "COMP", "CC"), ("", "", "CC"),
                       ("", "COMP", "C?"), ("", "COMP", "%2A"),
                       ("", "COMP", "CC,OTHER")]:
            with self.assertRaises(ValueError):
                self.start(*target)
        self.assertEqual(self.http.mock_calls, [])

    def test_disabled_and_prod_gates(self):
        with self.assertRaises(ValueError):
            self.execute(self.start)
        os.environ.update(SAP_PI_ENVIRONMENT="prod", SAP_PI_ENABLE_CHANNEL_CONTROL="true")
        second = self.start(*self.args)["required_second_confirmation"]
        with self.assertRaises(ValueError):
            self.execute(self.start, second_confirmation=second)
        os.environ["SAP_PI_ENABLE_PROD_CHANNEL_CONTROL"] = "true"
        with self.assertRaises(ValueError):
            self.execute(self.start)
        self.assertEqual(self.http.mock_calls, [])

    def test_start_and_stop_verify_exact_target(self):
        os.environ["SAP_PI_ENABLE_CHANNEL_CONTROL"] = "true"
        for tool, verb, before, after in [
            (self.start, "start", "STOPPED", "STARTED"),
            (self.stop, "stop", "STARTED", "STOPPED"),
        ]:
            self.http.reset_mock()
            self.http.get.side_effect = [status(before), status(after)]
            self.http.post.return_value = Mock(status_code=200)
            self.assertTrue(self.execute(tool)["verified"])
            self.http.post.assert_called_once_with(
                "https://pi.invalid/AdapterFramework/ChannelAdminServlet",
                params=dict(party="", service="COMP", channel="CC", action=verb),
                auth=("fake", "fake"), timeout=10, allow_redirects=False)
            self.assertEqual([c[0] for c in self.http.mock_calls], ["get", "post", "get"])

    def test_wrong_party_and_manual_mode_block_write(self):
        os.environ["SAP_PI_ENABLE_CHANNEL_CONTROL"] = "true"
        for response in [status("STOPPED", party="OTHER"), status("STOPPED", control="MANUAL")]:
            self.http.get.return_value = response
            self.assertFalse(self.execute(self.start)["verified"])
        self.http.post.assert_not_called()

    def test_timeout_reads_back_without_retry_or_leaking_error(self):
        os.environ["SAP_PI_ENABLE_CHANNEL_CONTROL"] = "true"
        self.http.get.side_effect = [status("STARTED"), status("STARTED")]
        self.http.post.side_effect = Timeout("sensitive details")
        result = self.execute(self.stop)
        self.assertFalse(result["verified"])
        self.assertNotIn("sensitive", str(result))
        self.http.post.assert_called_once()
        self.assertEqual(self.http.get.call_count, 2)
