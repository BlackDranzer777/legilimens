"""
Certificate lifecycle for Legilimens: validate, atomically stage, and renew the
loopback WebTransport certificate.

Files in certs_dir():
  cert.pem       ECDSA P-256 leaf, SAN localhost + 127.0.0.1, < 14-day lifetime
  key.pem        the matching private key
  cert-hash.txt  base64 SHA-256(DER cert)   -> serverCertificateHashes (the pin)
  spki-hash.txt  base64 SHA-256(SPKI DER)   -> Chromium launch flag (kept distinct)

WebTransport serverCertificateHashes requires ECDSA P-256 (Chromium rejects RSA),
a leaf cert (CA=false), and a lifetime <= 14 days. The pin is the hash of the whole
DER certificate, NOT the SPKI hash.

A certificate is renewed when it is missing, unparsable, key-mismatched, the wrong
key type, missing the required localhost SANs, not yet valid, expired, or within
RENEWAL_THRESHOLD of expiry. A merely stale/absent hash file is repaired in place
without discarding an otherwise-valid certificate.

Renewal stages and validates a complete generation, then journals the previous
files before replacement. Lock-aware readers recover interrupted transactions
before using the files. Individual renames are atomic; the four-file bundle is not.
"""

import base64
import datetime
import hashlib
import ipaddress
import json
import os
import sys
import time
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

# Windows consoles default to cp1252, which can't encode the Unicode glyphs this
# script prints — force UTF-8 so cert work never dies on a cosmetic print line.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from paths import certs_dir

CERTS_DIR = certs_dir()

CERT_NAME = "cert.pem"
KEY_NAME = "key.pem"
HASH_NAME = "cert-hash.txt"
SPKI_NAME = "spki-hash.txt"
LOCK_NAME = "certs.lock"
JOURNAL_NAME = "certs-transaction.json"
GENERATION_FILES = (KEY_NAME, CERT_NAME, HASH_NAME, SPKI_NAME)

CERT_LIFETIME = datetime.timedelta(days=13)
RENEWAL_THRESHOLD = datetime.timedelta(hours=24)   # renew within a day of expiry
_LOCK_WAIT_SECONDS = 15
MAX_CERT_LIFETIME = datetime.timedelta(days=14)
_REQUIRED_DNS = "localhost"
_REQUIRED_IP = ipaddress.IPv4Address("127.0.0.1")


class CertificateError(Exception):
    """No usable certificate is available and one could not be produced."""


class CertStatus:
    """Result of validating the certificate directory."""

    def __init__(self, *, present, cert_valid_now, near_expiry, stored_hash_ok,
                 cert_hash, not_after, reason):
        self.present = present
        self.cert_valid_now = cert_valid_now      # parses, key matches, right type, SANs, in window
        self.near_expiry = near_expiry            # within RENEWAL_THRESHOLD of expiry
        self.stored_hash_ok = stored_hash_ok      # cert-hash.txt matches DER hash
        self.cert_hash = cert_hash                # actual DER hash (from cert.pem), or None
        self.not_after = not_after                # tz-aware expiry, or None
        self.reason = reason

    @property
    def usable(self) -> bool:
        """Safe to serve right now (a near-expiry cert is still usable)."""
        return self.cert_valid_now

    @property
    def needs_renewal(self) -> bool:
        return (not self.cert_valid_now) or self.near_expiry


# ---------- validation ----------

def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _der_hash_b64(cert: x509.Certificate) -> str:
    der = cert.public_bytes(serialization.Encoding.DER)
    return base64.b64encode(hashlib.sha256(der).digest()).decode()


def _spki_hash_b64(cert: x509.Certificate) -> str:
    spki_der = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(hashlib.sha256(spki_der).digest()).decode()


def _has_required_sans(cert: x509.Certificate) -> bool:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    dns = set(san.get_values_for_type(x509.DNSName))
    ips = set(san.get_values_for_type(x509.IPAddress))
    return _REQUIRED_DNS in dns and _REQUIRED_IP in ips


def _keys_match(cert: x509.Certificate, key) -> bool:
    try:
        return (isinstance(key, ec.EllipticCurvePrivateKey)
                and isinstance(key.curve, ec.SECP256R1)
                and isinstance(cert.public_key(), ec.EllipticCurvePublicKey)
                and key.public_key().public_numbers() == cert.public_key().public_numbers())
    except Exception:
        return False


def _validate(certs_dir_path=None, *, now=None) -> CertStatus:
    """Inspect the certificate directory without changing anything."""
    d = Path(certs_dir_path) if certs_dir_path else CERTS_DIR
    now = now or _now()
    cert_path, key_path = d / CERT_NAME, d / KEY_NAME

    if not cert_path.exists() or not key_path.exists():
        return CertStatus(present=False, cert_valid_now=False, near_expiry=True,
                          stored_hash_ok=False, cert_hash=None, not_after=None,
                          reason="certificate or key file missing")

    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except Exception as e:
        return CertStatus(present=True, cert_valid_now=False, near_expiry=True,
                          stored_hash_ok=False, cert_hash=None, not_after=None,
                          reason=f"certificate does not parse: {e}")
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except Exception as e:
        return CertStatus(present=True, cert_valid_now=False, near_expiry=True,
                          stored_hash_ok=False, cert_hash=None, not_after=None,
                          reason=f"private key does not parse: {e}")

    der_hash = _der_hash_b64(cert)
    not_after = cert.not_valid_after_utc
    not_before = cert.not_valid_before_utc

    try:
        stored_hash = (d / HASH_NAME).read_text().strip()
    except OSError:
        stored_hash = None
    stored_hash_ok = stored_hash == der_hash

    if not _keys_match(cert, key):
        reason = "private key does not match certificate or is not ECDSA P-256"
        return CertStatus(present=True, cert_valid_now=False, near_expiry=True,
                          stored_hash_ok=stored_hash_ok, cert_hash=der_hash,
                          not_after=not_after, reason=reason)
    if not _has_required_sans(cert):
        return CertStatus(present=True, cert_valid_now=False, near_expiry=True,
                          stored_hash_ok=stored_hash_ok, cert_hash=der_hash,
                          not_after=not_after, reason="missing localhost/127.0.0.1 SANs")
    profile_error = None
    if not_after - not_before > MAX_CERT_LIFETIME:
        profile_error = "certificate lifetime exceeds 14 days"
    try:
        if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            profile_error = "certificate must be a non-CA leaf"
        if ExtendedKeyUsageOID.SERVER_AUTH not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value:
            profile_error = "certificate must permit server authentication"
        try:
            if not cert.extensions.get_extension_for_class(x509.KeyUsage).value.digital_signature:
                profile_error = "certificate must permit digital signatures"
        except x509.ExtensionNotFound:
            pass  # An absent KeyUsage extension does not restrict usage.
    except x509.ExtensionNotFound:
        profile_error = "certificate is missing leaf/server authentication constraints"
    if profile_error:
        return CertStatus(present=True, cert_valid_now=False, near_expiry=True,
                          stored_hash_ok=stored_hash_ok, cert_hash=der_hash,
                          not_after=not_after, reason=profile_error)
    if now < not_before:
        return CertStatus(present=True, cert_valid_now=False, near_expiry=True,
                          stored_hash_ok=stored_hash_ok, cert_hash=der_hash,
                          not_after=not_after, reason="certificate not yet valid")
    if now >= not_after:
        return CertStatus(present=True, cert_valid_now=False, near_expiry=True,
                          stored_hash_ok=stored_hash_ok, cert_hash=der_hash,
                          not_after=not_after, reason="certificate expired")

    near_expiry = (not_after - now) <= RENEWAL_THRESHOLD
    reason = "near expiry" if near_expiry else ("stale hash" if not stored_hash_ok else "valid")
    return CertStatus(present=True, cert_valid_now=True, near_expiry=near_expiry,
                      stored_hash_ok=stored_hash_ok, cert_hash=der_hash,
                      not_after=not_after, reason=reason)


# ---------- generation (atomic) ----------

def _build_cert():
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Legilimens Security Tool"),
    ])
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))  # clock-skew tolerance
        .not_valid_after(now + CERT_LIFETIME)
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(_REQUIRED_DNS),
                x509.IPAddress(_REQUIRED_IP),
            ]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    return private_key, cert


def _atomic_replace(target: Path, data: bytes) -> None:
    """Write data to a temp file in the same dir, fsync, then atomically rename.

    On Windows os.replace raises PermissionError if the destination is momentarily
    open (e.g. a concurrent reader validating it), so retry the rename briefly.
    """
    tmp = target.with_suffix(target.suffix + f".tmp-{os.getpid()}-{int(time.time()*1000)}-{os.urandom(2).hex()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(data)
            output.flush()
        os.fsync(fd)
    finally:
        os.close(fd)
    for attempt in range(20):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == 19:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.02)


def _cleanup_temps(d: Path) -> None:
    for p in d.glob("*.tmp-*"):
        try:
            p.unlink()
        except OSError:
            pass


def _write_generation(d: Path, private_key, cert) -> str:
    """Commit a staged generation under _DirLock, with durable rollback data."""
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    der_hash = _der_hash_b64(cert)
    spki_hash = _spki_hash_b64(cert)

    files = {KEY_NAME: key_pem, CERT_NAME: cert_pem,
             HASH_NAME: der_hash.encode(), SPKI_NAME: spki_hash.encode()}
    with tempfile.TemporaryDirectory(prefix="cert-stage-", dir=d) as staging:
        for name, data in files.items():
            _atomic_replace(Path(staging) / name, data)
        if not _validate(staging).usable:
            raise CertificateError("staged certificate failed validation")
        previous = {name: base64.b64encode((d / name).read_bytes()).decode()
                    if (d / name).exists() else None for name in GENERATION_FILES}
        _atomic_replace(d / JOURNAL_NAME, json.dumps(previous).encode())
        try:
            for name, data in files.items():
                _atomic_replace(d / name, data)
            (d / JOURNAL_NAME).unlink()  # Commit only after all four replacements.
        except BaseException:
            _recover_transaction(d)
            raise
    return der_hash


def _recover_transaction(d: Path):
    journal = d / JOURNAL_NAME
    if not journal.exists():
        return
    previous = json.loads(journal.read_text())
    if set(previous) != set(GENERATION_FILES):
        raise CertificateError("invalid certificate recovery journal")
    for name in GENERATION_FILES:
        target = d / name
        data = previous[name]
        if data is None:
            target.unlink(missing_ok=True)
        else:
            data = base64.b64decode(data, validate=True)
            if not target.exists() or target.read_bytes() != data:
                _atomic_replace(target, data)
    journal.unlink()


def _repair_hash(d: Path, cert: x509.Certificate) -> str:
    der_hash = _der_hash_b64(cert)
    _atomic_replace(d / HASH_NAME, der_hash.encode())
    _atomic_replace(d / SPKI_NAME, _spki_hash_b64(cert).encode())
    return der_hash


# ---------- cross-process lock ----------

class _DirLock:
    def __init__(self, d: Path):
        self.path = d / LOCK_NAME
        self.fd = None

    def __enter__(self):
        # OS locks release on process death. Never unlink the lock file: doing so
        # would allow contenders to lock different inodes for the same directory.
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        # Windows supports locks beyond EOF. Writing an initial byte before
        # acquiring the lock races with another contender that already holds it.
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while True:
            try:
                os.lseek(self.fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() > deadline:
                    os.close(self.fd)
                    self.fd = None
                    raise TimeoutError("another process holds the certificate lock")
                time.sleep(0.1)

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                os.lseek(self.fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(self.fd)
                self.fd = None


# ---------- public API ----------

def validate(certs_dir_path=None, *, now=None) -> CertStatus:
    d = Path(certs_dir_path) if certs_dir_path else CERTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    with _DirLock(d):
        _recover_transaction(d)
        return _validate(d, now=now)


def load_cert_chain(configuration, certs_dir_path=None):
    """Keep the lock across both reads so listeners never load a mixed pair."""
    d = Path(certs_dir_path) if certs_dir_path else CERTS_DIR
    with _DirLock(d):
        _recover_transaction(d)
        configuration.load_cert_chain(str(d / CERT_NAME), str(d / KEY_NAME))


def ensure_cert(certs_dir_path=None) -> CertStatus:
    """Guarantee a usable certificate exists, renewing if needed.

    Preserves a valid certificate across restarts. Renews a missing, invalid,
    expired or near-expiry certificate under a lock. If renewal fails but the
    existing certificate is still usable, it is preserved and a warning is logged;
    only a total absence of a usable certificate raises CertificateError.
    """
    d = Path(certs_dir_path) if certs_dir_path else CERTS_DIR
    d.mkdir(parents=True, exist_ok=True)

    try:
        with _DirLock(d):
            _recover_transaction(d)
            _cleanup_temps(d)
            status = _validate(d)
            if status.usable and not status.needs_renewal and status.stored_hash_ok:
                return status

            # Cheap path: cert+key are fine, only the hash files drifted.
            if status.usable and not status.needs_renewal and not status.stored_hash_ok:
                cert = x509.load_pem_x509_certificate((d / CERT_NAME).read_bytes())
                _repair_hash(d, cert)
                return _validate(d)

            try:
                key, cert = _build_cert()
                _write_generation(d, key, cert)
                new_status = _validate(d)
                if not new_status.usable:
                    raise CertificateError(f"generated certificate did not validate: {new_status.reason}")
                return new_status
            except Exception as e:
                _cleanup_temps(d)
                _recover_transaction(d)
                fallback = _validate(d)
                if fallback.usable:
                    print(f"[legilimens] certificate renewal failed ({e}); "
                          f"serving the existing certificate", file=sys.stderr, flush=True)
                    if not fallback.stored_hash_ok:
                        cert = x509.load_pem_x509_certificate((d / CERT_NAME).read_bytes())
                        _repair_hash(d, cert)
                        return _validate(d)
                    return fallback
                raise CertificateError(f"no usable certificate and renewal failed: {e}")
    except (OSError, TimeoutError, ValueError) as e:
        raise CertificateError(f"certificate preparation failed: {e}") from e


# ---------- backward-compatible helpers ----------

def generate_cert(certs_dir_path=None) -> str:
    """Force-generate a fresh certificate (used by the standalone CLI). Returns the pin."""
    d = Path(certs_dir_path) if certs_dir_path else CERTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    with _DirLock(d):
        _recover_transaction(d)
        key, cert = _build_cert()
        return _write_generation(d, key, cert)


def get_cert_hash(certs_dir_path=None) -> str | None:
    """Derive the certificate pin under the recovery lock, or return None."""
    d = Path(certs_dir_path) if certs_dir_path else CERTS_DIR
    try:
        with _DirLock(d):
            _recover_transaction(d)
            return _der_hash_b64(x509.load_pem_x509_certificate((d / CERT_NAME).read_bytes()))
    except (OSError, ValueError):
        return None


def certs_exist(certs_dir_path=None) -> bool:
    d = Path(certs_dir_path) if certs_dir_path else CERTS_DIR
    return (d / CERT_NAME).exists() and (d / KEY_NAME).exists()


if __name__ == "__main__":
    print("[legilimens] Ensuring a valid ECDSA P-256 certificate...")
    st = ensure_cert()
    spki = (CERTS_DIR / SPKI_NAME).read_text().strip()
    print(f"\n  Status:   {st.reason}")
    print(f"  Expires:  {st.not_after.isoformat() if st.not_after else 'unknown'}")
    print(f"\n[legilimens] Certificates in: {CERTS_DIR}")
    print(f"\n  Cert hash (serverCertificateHashes, served at /cert-hash):\n    {st.cert_hash}")
    print(f"\n  SPKI hash (--ignore-certificate-errors-spki-list flag):\n    {spki}")
    print(f"\n[legilimens] Launch Chromium with:")
    print(f"  chromium --origin-to-force-quic-on=127.0.0.1:4433,127.0.0.1:4434 "
          f"--ignore-certificate-errors-spki-list={spki} http://localhost:5180")
