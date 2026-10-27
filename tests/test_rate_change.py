import unittest

from workbench.nanodaq_source import NanoDAQSource


class _StopStreamLoop(Exception):
    pass


class _RateClient:
    def __init__(self, rate_ack: bool):
        self.rate_ack = rate_ack
        self.requested_rate = None

    def set_timeout(self, _timeout):
        pass

    def stream_off_quiesce(self, _channel):
        return True

    def flush_input(self):
        pass

    def set_rate(self, rate, _channel):
        self.requested_rate = rate
        return self.rate_ack

    def stream_on(self, _channel):
        return True

    def read_raw(self):
        raise _StopStreamLoop


class RateChangeTest(unittest.TestCase):
    def _source(self):
        source = NanoDAQSource(ring=None, ip="127.0.0.1", rate_hz=100)
        source._msg_head = "nanoDAQ"
        source._msg_tail = ""
        return source

    def _process_request(self, source, client):
        with self.assertRaises(_StopStreamLoop):
            source._stream_loop(client, channels=16, full_scale=1.0, to_pa=1.0)

    def test_rate_stays_confirmed_value_while_request_is_pending(self):
        source = self._source()

        self.assertTrue(source.set_rate_hz(200))

        status = source.status()
        self.assertEqual(status["rate"], "pending")
        self.assertEqual(status["rate_requested_hz"], 200)
        self.assertEqual(status["rate_hz"], 100.0)

    def test_rejected_rate_keeps_previous_confirmed_value(self):
        source = self._source()
        source.set_rate_hz(200)

        self._process_request(source, _RateClient(rate_ack=False))

        status = source.status()
        self.assertEqual(status["rate"], "failed")
        self.assertEqual(status["rate_requested_hz"], 200)
        self.assertEqual(status["rate_hz"], 100.0)
        self.assertIn("거부됨", status["rate_msg"])

    def test_accepted_rate_updates_confirmed_value(self):
        source = self._source()
        source.set_rate_hz(200)

        self._process_request(source, _RateClient(rate_ack=True))

        status = source.status()
        self.assertEqual(status["rate"], "done")
        self.assertEqual(status["rate_requested_hz"], 200)
        self.assertEqual(status["rate_hz"], 200.0)


if __name__ == "__main__":
    unittest.main()
