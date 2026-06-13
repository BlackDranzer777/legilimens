"""
Certificate generation for Legilimens.

WebTransport serverCertificateHashes requires:
- ECDSA P-256 (Chromium rejects RSA)
- Leaf cert only (basicConstraints: CA=false)
- Valid <= 14 days (use 13)
- cert hash = SHA-256(entire DER cert), NOT the SPKI hash
"""

import base64
import datetime
import hashlib
import ipaddress
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERTS_DIR = Path(__file__).parent / "certs"


def generate_cert() -> str:
    """Generate ECDSA P-256 self-signed cert, return base64 SHA-256(DER) hash."""
    CERTS_DIR.mkdir(exist_ok=True)

    private_key = ec.generate_private_key(ec.SECP256R1())

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Legilimens Security Tool"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=13))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )

    (CERTS_DIR / "cert.pem").write_bytes(cert_pem)
    (CERTS_DIR / "key.pem").write_bytes(key_pem)

    # SHA-256 of entire DER certificate — this is what serverCertificateHashes wants.
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    cert_hash_bytes = hashlib.sha256(cert_der).digest()
    cert_hash_b64 = base64.b64encode(cert_hash_bytes).decode()

    (CERTS_DIR / "cert-hash.txt").write_text(cert_hash_b64)

    # SPKI hash = SHA-256(SubjectPublicKeyInfo DER) — only needed for the
    # --ignore-certificate-errors-spki-list Chromium launch flag.
    spki_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    spki_hash_b64 = base64.b64encode(hashlib.sha256(spki_der).digest()).decode()
    (CERTS_DIR / "spki-hash.txt").write_text(spki_hash_b64)

    return cert_hash_b64


def get_cert_hash() -> str | None:
    """Read the stored cert hash, or None if certs haven't been generated."""
    p = CERTS_DIR / "cert-hash.txt"
    return p.read_text().strip() if p.exists() else None


def certs_exist() -> bool:
    return (CERTS_DIR / "cert.pem").exists() and (CERTS_DIR / "key.pem").exists()


if __name__ == "__main__":
    print("[legilimens] Generating self-signed ECDSA P-256 certificate...")
    h = generate_cert()
    spki = (CERTS_DIR / "spki-hash.txt").read_text().strip()
    print(f"\n  Key type: ECDSA / secp256r1  ← must be ec / prime256v1")
    print(f"  CA cert:  False  ← must be false")
    print(f"  Period:   13 days  (limit: 14)")
    print(f"\n[legilimens] Certificates written to: {CERTS_DIR}")
    print(f"\n  Cert hash (serverCertificateHashes, served at /cert-hash):")
    print(f"    {h}")
    print(f"\n  SPKI hash (--ignore-certificate-errors-spki-list flag):")
    print(f"    {spki}")
    print(f"\n[legilimens] Launch Chromium with:")
    print(f"  chromium --origin-to-force-quic-on=127.0.0.1:4433,127.0.0.1:4434 --ignore-certificate-errors-spki-list={spki} http://localhost:5173")
    print(f"\n[legilimens] Certificate valid for 13 days. Re-run before expiry.")
