"""Certificate validation, renewal, repair, atomic staging, and concurrency.

Every test uses a fresh temporary certificate directory. The developer's real
certificates are never touched, and no TLS-verification bypass is used.
"""

import base64
import datetime
import hashlib
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import certs
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _stage(d, *, not_before=None, not_after=None, sans=True, matching_key=True, write_hash=True,
           ca=False, server_auth=True):
    """Write a certificate/key (and optionally hash files) with chosen validity."""
    d = Path(d)
    nb = not_before if not_before is not None else _now() - datetime.timedelta(minutes=5)
    na = not_after if not_after is not None else _now() + certs.CERT_LIFETIME
    key = ec.generate_private_key(ec.SECP256R1())
    san_list = []
    if sans:
        san_list = [x509.DNSName("localhost"), x509.IPAddress(certs._REQUIRED_IP)]
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(nb)
        .not_valid_after(na)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([
            ExtendedKeyUsageOID.SERVER_AUTH if server_auth else ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
    )
    if san_list:
        builder = builder.add_extension(x509.SubjectAlternativeName(san_list), critical=False)
    cert = builder.sign(key, hashes.SHA256())

    (d / certs.CERT_NAME).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    write_key = key if matching_key else ec.generate_private_key(ec.SECP256R1())
    (d / certs.KEY_NAME).write_bytes(write_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    if write_hash:
        der = cert.public_bytes(serialization.Encoding.DER)
        (d / certs.HASH_NAME).write_text(base64.b64encode(hashlib.sha256(der).digest()).decode())
    return cert


class CertLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _der_hash(self):
        cert = x509.load_pem_x509_certificate((self.d / certs.CERT_NAME).read_bytes())
        der = cert.public_bytes(serialization.Encoding.DER)
        return base64.b64encode(hashlib.sha256(der).digest()).decode()

    def test_fresh_generation_valid_and_consistent(self):
        st = certs.ensure_cert(self.d)
        self.assertTrue(st.usable)
        self.assertTrue(st.stored_hash_ok)
        self.assertEqual(st.cert_hash, self._der_hash())
        self.assertEqual(certs.get_cert_hash(self.d), st.cert_hash)

    def test_valid_cert_preserved_across_calls(self):
        first = certs.ensure_cert(self.d).cert_hash
        second = certs.ensure_cert(self.d).cert_hash
        self.assertEqual(first, second)

    def test_expired_cert_is_renewed(self):
        _stage(self.d, not_before=_now() - datetime.timedelta(days=20),
               not_after=_now() - datetime.timedelta(days=1))
        old = self._der_hash()
        st = certs.ensure_cert(self.d)
        self.assertTrue(st.usable)
        self.assertNotEqual(st.cert_hash, old)
        self.assertGreater(st.not_after, _now())

    def test_near_expiry_cert_is_renewed(self):
        _stage(self.d, not_after=_now() + datetime.timedelta(hours=1))
        old = self._der_hash()
        st = certs.ensure_cert(self.d)
        self.assertTrue(st.usable)
        self.assertNotEqual(st.cert_hash, old)
        self.assertGreater(st.not_after - _now(), certs.RENEWAL_THRESHOLD)

    def test_corrupt_cert_is_renewed(self):
        (self.d / certs.CERT_NAME).write_text("-----BEGIN CERTIFICATE-----\nnope\n")
        (self.d / certs.KEY_NAME).write_text("garbage")
        st = certs.ensure_cert(self.d)
        self.assertTrue(st.usable)

    def test_key_mismatch_is_renewed(self):
        _stage(self.d, matching_key=False)
        pre = certs.validate(self.d)
        self.assertFalse(pre.usable)
        st = certs.ensure_cert(self.d)
        self.assertTrue(st.usable)
        self.assertTrue(certs.validate(self.d).usable)

    def test_missing_sans_is_renewed(self):
        _stage(self.d, sans=False)
        self.assertFalse(certs.validate(self.d).usable)
        self.assertTrue(certs.ensure_cert(self.d).usable)

    def test_stale_hash_repaired_without_new_cert(self):
        certs.ensure_cert(self.d)
        keep = self._der_hash()
        (self.d / certs.HASH_NAME).write_text("STALE")
        st = certs.ensure_cert(self.d)
        self.assertEqual(st.cert_hash, keep)          # same certificate kept
        self.assertTrue(st.stored_hash_ok)
        self.assertEqual((self.d / certs.HASH_NAME).read_text().strip(), keep)

    def test_missing_hash_file_repaired(self):
        certs.ensure_cert(self.d)
        keep = self._der_hash()
        (self.d / certs.HASH_NAME).unlink()
        st = certs.ensure_cert(self.d)
        self.assertEqual(st.cert_hash, keep)
        self.assertTrue(st.stored_hash_ok)

    def test_der_hash_distinct_from_spki(self):
        certs.ensure_cert(self.d)
        der = (self.d / certs.HASH_NAME).read_text().strip()
        spki = (self.d / certs.SPKI_NAME).read_text().strip()
        self.assertNotEqual(der, spki)
        self.assertEqual(der, self._der_hash())

    def test_renewal_failure_preserves_previous_usable_cert(self):
        # A still-valid (near-expiry) cert exists; generation fails -> keep serving it.
        _stage(self.d, not_after=_now() + datetime.timedelta(hours=2))
        kept = self._der_hash()
        original = certs._write_generation
        try:
            certs._write_generation = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
            st = certs.ensure_cert(self.d)
        finally:
            certs._write_generation = original
        self.assertTrue(st.usable)
        self.assertEqual(st.cert_hash, kept)          # previous generation preserved

    def test_no_usable_cert_and_generation_failure_raises(self):
        original = certs._write_generation
        try:
            certs._write_generation = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
            with self.assertRaises(certs.CertificateError):
                certs.ensure_cert(self.d)
        finally:
            certs._write_generation = original

    def test_mid_commit_failure_restores_entire_previous_generation(self):
        for name in certs.GENERATION_FILES:
            with self.subTest(failed_file=name):
                _stage(self.d, not_after=_now() + datetime.timedelta(hours=2))
                before = {n: (self.d / n).read_bytes() if (self.d / n).exists() else None
                          for n in certs.GENERATION_FILES}
                original = certs._atomic_replace
                failed = False

                def replace(target, data):
                    nonlocal failed
                    if target == self.d / name and not failed:
                        failed = True
                        raise OSError("injected commit write failure")
                    return original(target, data)

                with patch.object(certs, '_atomic_replace', side_effect=replace):
                    self.assertTrue(certs.ensure_cert(self.d).usable)
                self.assertTrue(failed)
                self.assertEqual(before, {n: (self.d / n).read_bytes() if (self.d / n).exists() else None
                                          for n in certs.GENERATION_FILES})
                self.assertFalse((self.d / certs.JOURNAL_NAME).exists())

    def test_process_death_recovers_journal_and_releases_lock(self):
        _stage(self.d, not_after=_now() + datetime.timedelta(hours=2))
        old = self._der_hash()
        code = '''
import os, sys
from pathlib import Path
import certs
d = Path(sys.argv[1])
original = certs._atomic_replace
def replace(target, data):
    original(target, data)
    if target == d / certs.KEY_NAME:
        os._exit(17)
certs._atomic_replace = replace
certs.ensure_cert(d)
'''
        p = subprocess.run([sys.executable, '-c', code, str(self.d)],
                           cwd=str(Path(certs.__file__).parent), timeout=10, capture_output=True)
        self.assertEqual(p.returncode, 17, p.stderr)
        self.assertTrue((self.d / certs.JOURNAL_NAME).exists())
        self.assertTrue(certs.validate(self.d).usable)
        self.assertEqual(self._der_hash(), old)
        self.assertFalse((self.d / certs.JOURNAL_NAME).exists())

    def test_listener_load_recovers_interrupted_pair(self):
        _stage(self.d)
        import json
        from aioquic.quic.configuration import QuicConfiguration
        previous = {n: base64.b64encode((self.d / n).read_bytes()).decode()
                    if (self.d / n).exists() else None for n in certs.GENERATION_FILES}
        (self.d / certs.JOURNAL_NAME).write_text(json.dumps(previous))
        (self.d / certs.KEY_NAME).write_text('broken key')
        config = QuicConfiguration(is_client=False)
        certs.load_cert_chain(config, self.d)
        self.assertEqual(config.certificate.public_key().public_numbers(),
                         config.private_key.public_key().public_numbers())

    def test_unsupported_certificate_profiles_are_renewed(self):
        for options in ({'not_after': _now() + datetime.timedelta(days=365)},
                        {'ca': True}, {'server_auth': False}):
            with self.subTest(options=options):
                _stage(self.d, **options)
                self.assertFalse(certs.validate(self.d).usable)
                self.assertTrue(certs.ensure_cert(self.d).usable)

    def test_fourteen_day_boundary_is_accepted(self):
        start = _now() - datetime.timedelta(minutes=5)
        _stage(self.d, not_before=start, not_after=start + datetime.timedelta(days=14))
        self.assertTrue(certs.validate(self.d).usable)

    def test_leftover_temp_files_are_cleaned(self):
        certs.ensure_cert(self.d)
        (self.d / "cert.pem.tmp-999-1").write_text("orphan")
        (self.d / certs.HASH_NAME).write_text("STALE")  # force the locked path to run
        certs.ensure_cert(self.d)
        self.assertEqual(list(self.d.glob("*.tmp-*")), [])

    def test_concurrent_ensure_generates_single_cert(self):
        results, errors = [], []

        def worker():
            try:
                results.append(certs.ensure_cert(self.d).cert_hash)
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1, "concurrent generators produced different certs")
        self.assertTrue(certs.validate(self.d).usable)
        self.assertEqual(list(self.d.glob("*.tmp-*")), [])
        # The persistent lock inode must remain; the OS lock itself is released.
        with certs._DirLock(self.d):
            pass

    def test_lock_does_not_write_before_acquiring_ownership(self):
        with patch.object(certs.os, 'write', side_effect=AssertionError('unowned lock-file write')):
            with certs._DirLock(self.d):
                self.assertEqual((self.d / certs.LOCK_NAME).stat().st_size, 0)
            with certs._DirLock(self.d):
                pass

    def test_validate_flags_expiry_with_injected_clock(self):
        _stage(self.d)
        future = _now() + certs.CERT_LIFETIME - datetime.timedelta(hours=1)
        st = certs.validate(self.d, now=future)
        self.assertTrue(st.near_expiry)
        past_expiry = _now() + certs.CERT_LIFETIME + datetime.timedelta(hours=1)
        st2 = certs.validate(self.d, now=past_expiry)
        self.assertFalse(st2.usable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
