# JWT License System

## Overview

annex4ac uses JWT (JSON Web Tokens) with RSA-256 signatures for license validation. This provides secure, offline license checking without requiring network connectivity.

## Architecture

### Key Components

1. **Private Key** (`private.pem`) - Used by the license server to sign tokens
2. **Public Key** (`annex4ac/lic_pub.pem`) - Distributed with the package for token validation
3. **JWT Token** - Contains license claims and is validated by the CLI

### Security Features

- **RSA-256** signatures for tamper-proof tokens
- **Key ID (kid)** support for future key rotation
- **Hardcoded algorithm** to prevent "alg: none" attacks
- **Required claims**: `iss`, `aud`, `iat`, `exp`
- **Offline validation** - no network required

## For Users

### Setting Up Your License

1. **Obtain your JWT token** from support
2. **Set environment variable**:
   ```bash
   # Linux/Mac
   export ANNEX4AC_LICENSE="your_jwt_token_here"
   
   # Windows PowerShell
   $env:ANNEX4AC_LICENSE="your_jwt_token_here"
   
   # Windows CMD
   set ANNEX4AC_LICENSE=your_jwt_token_here
   ```

3. **Use Pro features**:
   ```bash
   annex4ac generate -i my_annex.yaml -o annex_iv.pdf
   ```

### Token Format

Your JWT token contains:
- **Subject** (`sub`): Your email address
- **Plan** (`plan`): License tier (currently "pro")
- **Issued** (`iat`): Token creation timestamp
- **Expires** (`exp`): Token expiration timestamp
- **Issuer** (`iss`): "annex4ac.io"
- **Audience** (`aud`): "annex4ac-cli"

## For Developers

### Generating Keys

```bash
# Generate RSA key pair
python generate_keys.py
```

This creates:
- `private.pem` - Keep secure, use for signing
- `annex4ac/lic_pub.pem` - Include in package

### Generating Licenses

```bash
# Generate license token
python generate_license.py user@example.com --plan pro --months 12
```

### Key Rotation

1. Generate new key pair with new `kid`
2. Add new public key to `pub_map` in `_check_license()`
3. Old tokens remain valid until expiration
4. New tokens use new private key

### Testing

```bash
# Run license tests
python -m pytest tests/test_license.py
```

## Security Best Practices

1. **Keep private key secure** - Never commit to repository
2. **Use strong RSA keys** - 2048-bit minimum
3. **Rotate keys regularly** - Annual rotation recommended
4. **Validate all claims** - Check issuer, audience, expiration
5. **Hardcode algorithms** - Prevent algorithm confusion attacks

## Troubleshooting

### Common Errors

- **"License environment variable not set"** - Set `ANNEX4AC_LICENSE`
- **"License expired"** - Token has passed expiration date
- **"Invalid license token"** - Token signature or format is invalid
- **"No public key found for kid"** - Key ID not recognized

### Debug Mode

To see token details without validation:
```python
import jwt
token = "your_token_here"
decoded = jwt.decode(token, options={"verify_signature": False})
print(decoded)
``` 