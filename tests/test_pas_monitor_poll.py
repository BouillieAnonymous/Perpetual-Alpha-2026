"""A blocked renewal must not alternate base/full incident fingerprints per poll."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_funding_live import NOW

from perpetual_alpha.funding_live.policy import Policy
from perpetual_alpha.funding_live.preparation import Monitor, main


class PollTests(unittest.TestCase):
    def test_repeated_blocked_renewal_enqueues_one_complete_incident(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'smtp.json').write_text(json.dumps({'recipient':'test@example.invalid'}))
            (root/'recipient.txt').write_text('test@example.invalid')
            stop=Mock()
            stop.is_set.side_effect=[False,False,True]
            client=Mock(team='0x'+'1'*40)
            with (patch('perpetual_alpha.funding_live.preparation.load_policy',return_value=Policy('pas','USDP')),
                  patch('perpetual_alpha.funding_live.preparation.TestnetVenue',return_value=client),
                  patch('perpetual_alpha.funding_live.preparation.threading.Event',return_value=stop),
                  patch('perpetual_alpha.funding_live.preparation.signal.signal'),
                  patch('perpetual_alpha.funding_live.preparation.expected_position',return_value='0'),
                  patch('perpetual_alpha.funding_live.preparation.runtime_issues',return_value=['paused']),
                  patch('perpetual_alpha.funding_live.preparation.preflight',side_effect=lambda *a,**k:{'time_ms':NOW,'issues':[]}),
                  patch('perpetual_alpha.funding_live.renewal.maybe_renew',side_effect=ValueError('expired')),
                  patch.object(Monitor,'deliver',return_value='idle')):
                main(['watch','--config','unused','--root',str(root),'--smtp-file',str(root/'smtp.json'),
                      '--renewal-permission-file','unused','--trading-approval-file','unused'])
            monitor=Monitor(root/'monitor.sqlite3')
            try:
                rows=monitor.db.execute('SELECT value FROM outbox').fetchall()
                self.assertEqual(len(rows),1)
                self.assertEqual(json.loads(rows[0][0])['issues'],['paused','PAS automatic renewal blocked: expired'])
            finally:
                monitor.close()
