from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .config import atomic_write, validate_name, validate_host


def ensure_identity(root: Path) -> str:
    tls = Path(root) / "tls"
    tls.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        tls.chmod(0o700)
    path = tls / "identity.pem"
    if not path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CrossScreen")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=3650)).sign(key, hashes.SHA256()))
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption()) + cert.public_bytes(serialization.Encoding.PEM)
        atomic_write(path, pem.decode("ascii"))
    if os.name != "nt":
        path.chmod(0o600)
    raw = path.read_bytes()
    key = serialization.load_pem_private_key(raw, password=None)
    cert = x509.load_pem_x509_certificate(raw)
    public_format = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if key.public_key().public_bytes(*public_format) != cert.public_key().public_bytes(*public_format):
        raise ValueError("本机证书与密钥不匹配；请保留现有文件并重新生成身份。")
    return cert.fingerprint(hashes.SHA256()).hex()


def parse_pairing(text: str) -> dict:
    try:
        if not isinstance(text, str) or len(text) > 2048:
            raise ValueError()
        data = json.loads(text)
        if not isinstance(data, dict) or set(data) != {"version", "name", "host", "port", "fingerprint", "role"}:
            raise ValueError()
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError()
        validate_name(data["name"])
        validate_host(data["host"])
        if data["role"] not in ("server", "client"):
            raise ValueError()
        if type(data["port"]) is not int or not 1024 <= data["port"] <= 65535:
            raise ValueError()
        if not isinstance(data["fingerprint"], str) or not re.fullmatch(r"[a-f0-9]{64}", data["fingerprint"]):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("配对信息无效，请复制另一台电脑的完整配对信息。") from exc


def make_pairing(name: str, host: str, port: int, fingerprint: str, role: str) -> str:
    data = dict(version=1, name=name, host=host, port=port, fingerprint=fingerprint, role=role)
    text = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    parse_pairing(text)
    return text


def trust_peer(root: Path, fingerprint: str, peer_role: str):
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise ValueError("配对指纹不正确。")
    if peer_role not in ("server", "client"):
        raise ValueError("设备角色不正确。")
    filename = "trusted-servers" if peer_role == "server" else "trusted-clients"
    atomic_write(Path(root) / "tls" / filename, f"v2:sha256:{fingerprint}\n")
