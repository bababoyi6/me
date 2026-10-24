import unittest
from e2e_lab import sanitize_startup_log
from telemt_probe import SECRETS,TOKEN


class StartupDiagnosticsTests(unittest.TestCase):
    def test_startup_reason_survives_without_synthetic_credentials_or_long_hex(self):
        text='\x1b[31mERROR Permission denied\x1b[0m token='+TOKEN+' secret='+next(iter(SECRETS.values())).hex()
        text+=' checksum='+'a'*64+' private_key=arbitrary-private-value'
        result=sanitize_startup_log(text)
        self.assertIn('ERROR Permission denied',result)
        for private in (TOKEN,next(iter(SECRETS.values())).hex(),'a'*64,'arbitrary-private-value','\x1b'):
            self.assertNotIn(private,result)

    def test_diagnostic_output_has_a_hard_text_bound(self):
        self.assertLessEqual(len(sanitize_startup_log('x'*20000)),6000)
