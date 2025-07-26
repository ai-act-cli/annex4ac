#!/usr/bin/env python3
"""
Test JWT license validation functionality.
"""

import os
import time
import tempfile
import pytest
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import jwt

# Import the function to test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from annex4ac import _check_license

def generate_test_keys():
    """Generate test RSA key pair."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    public_key = private_key.public_key()
    
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    return private_pem.decode(), public_pem.decode()

def create_test_token(private_key, email="test@example.com", plan="pro", months=12):
    """Create a test JWT token."""
    now = int(time.time())
    payload = {
        "sub": email,
        "plan": plan,
        "iat": now,
        "exp": now + 60*60*24*30*months,
        "iss": "annex4ac.io",
        "aud": "annex4ac-cli"
    }
    
    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": "2025-01"}
    )

def test_license_validation():
    """Test JWT license validation."""
    # Generate test keys
    private_key, public_key = generate_test_keys()
    
    # Create test token
    token = create_test_token(private_key)
    
    # Mock the public key file
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create annex4ac package structure
        annex4ac_dir = Path(temp_dir) / "annex4ac"
        annex4ac_dir.mkdir()
        
        # Write public key
        (annex4ac_dir / "lic_pub.pem").write_text(public_key)
        
        # Mock importlib.resources.files
        import importlib.resources
        original_files = importlib.resources.files
        
        def mock_files(package_name):
            class MockFiles:
                def joinpath(self, path):
                    return Path(annex4ac_dir) / path
            return MockFiles()
        
        importlib.resources.files = mock_files
        
        try:
            # Set environment variable
            os.environ["ANNEX4AC_LICENSE"] = token
            
            # Test should pass
            _check_license()
            
        finally:
            # Restore original function
            importlib.resources.files = original_files
            if "ANNEX4AC_LICENSE" in os.environ:
                del os.environ["ANNEX4AC_LICENSE"]

def test_expired_license():
    """Test expired license rejection."""
    private_key, public_key = generate_test_keys()
    
    # Create expired token (expired 1 hour ago)
    now = int(time.time())
    payload = {
        "sub": "test@example.com",
        "plan": "pro",
        "iat": now - 3600,
        "exp": now - 3600,  # Expired
        "iss": "annex4ac.io",
        "aud": "annex4ac-cli"
    }
    
    token = jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": "2025-01"}
    )
    
    with tempfile.TemporaryDirectory() as temp_dir:
        annex4ac_dir = Path(temp_dir) / "annex4ac"
        annex4ac_dir.mkdir()
        (annex4ac_dir / "lic_pub.pem").write_text(public_key)
        
        import importlib.resources
        original_files = importlib.resources.files
        
        def mock_files(package_name):
            class MockFiles:
                def joinpath(self, path):
                    return Path(annex4ac_dir) / path
            return MockFiles()
        
        importlib.resources.files = mock_files
        
        try:
            os.environ["ANNEX4AC_LICENSE"] = token
            
            # Should raise typer.Exit(1) for expired license
            with pytest.raises(SystemExit) as exc_info:
                _check_license()
            assert exc_info.value.code == 1
            
        finally:
            importlib.resources.files = original_files
            if "ANNEX4AC_LICENSE" in os.environ:
                del os.environ["ANNEX4AC_LICENSE"]

def test_invalid_plan():
    """Test invalid plan rejection."""
    private_key, public_key = generate_test_keys()
    
    # Create token with invalid plan
    token = create_test_token(private_key, plan="free")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        annex4ac_dir = Path(temp_dir) / "annex4ac"
        annex4ac_dir.mkdir()
        (annex4ac_dir / "lic_pub.pem").write_text(public_key)
        
        import importlib.resources
        original_files = importlib.resources.files
        
        def mock_files(package_name):
            class MockFiles:
                def joinpath(self, path):
                    return Path(annex4ac_dir) / path
            return MockFiles()
        
        importlib.resources.files = mock_files
        
        try:
            os.environ["ANNEX4AC_LICENSE"] = token
            
            # Should raise typer.Exit(1) for invalid plan
            with pytest.raises(SystemExit) as exc_info:
                _check_license()
            assert exc_info.value.code == 1
            
        finally:
            importlib.resources.files = original_files
            if "ANNEX4AC_LICENSE" in os.environ:
                del os.environ["ANNEX4AC_LICENSE"]

if __name__ == "__main__":
    pytest.main([__file__]) 