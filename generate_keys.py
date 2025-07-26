#!/usr/bin/env python3
"""
Generate RSA key pair for JWT license validation.
Private key for signing licenses, public key for CLI validation.
"""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import pathlib

def generate_rsa_keys():
    """Generate RSA-2048 key pair."""
    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    # Get public key
    public_key = private_key.public_key()
    
    # Save private key (PEM format)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    # Save public key (PEM format)
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    # Write files
    pathlib.Path("private.pem").write_bytes(private_pem)
    pathlib.Path("annex4ac/lic_pub.pem").write_bytes(public_pem)
    
    print("✅ RSA key pair generated:")
    print("  private.pem (keep secure!)")
    print("  annex4ac/lic_pub.pem (included in package)")

if __name__ == "__main__":
    generate_rsa_keys() 