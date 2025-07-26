#!/usr/bin/env python3
"""
Generate JWT license tokens for annex4ac CLI.
Usage: python generate_license.py user@example.com [plan] [months]
"""

import jwt
import json
import time
import uuid
import pathlib
import argparse
from cryptography.hazmat.primitives import serialization

# Configuration
PRIV_KEY_PATH = "private.pem"
KID = "2025-01"  # Key ID for rotation support

def load_private_key():
    """Load private key from file."""
    try:
        with open(PRIV_KEY_PATH, 'r') as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Private key not found: {PRIV_KEY_PATH}")

def make_token(email, plan="pro", months=12):
    """Generate JWT license token."""
    now = int(time.time())
    payload = {
        "sub": email,
        "plan": plan,
        "iat": now,
        "exp": now + 60*60*24*30*months,  # months * 30 days
        "iss": "annex4ac.io",
        "aud": "annex4ac-cli"
    }
    
    private_key = load_private_key()
    
    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": KID}
    )

def main():
    parser = argparse.ArgumentParser(description="Generate annex4ac license token")
    parser.add_argument("email", help="User email address")
    parser.add_argument("--plan", default="pro", choices=["pro"], help="License plan")
    parser.add_argument("--months", type=int, default=12, help="License duration in months")
    
    args = parser.parse_args()
    
    try:
        token = make_token(args.email, args.plan, args.months)
        print(f"License token for {args.email}:")
        print(token)
        
        # Also show decoded payload for verification
        decoded = jwt.decode(token, options={"verify_signature": False})
        print(f"\nToken details:")
        print(f"  Plan: {decoded['plan']}")
        print(f"  Expires: {time.ctime(decoded['exp'])}")
        print(f"  Issued: {time.ctime(decoded['iat'])}")
        
    except Exception as e:
        print(f"Error generating token: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main()) 