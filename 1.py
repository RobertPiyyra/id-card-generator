# from werkzeug.security import generate_password_hash
# print(generate_password_hash("secret123", method='pbkdf2:sha256'))
import secrets
print(secrets.token_hex(32))