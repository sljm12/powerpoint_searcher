import getpass
import hashlib
import secrets

phrase = getpass.getpass("Team passphrase (12+ characters): ")
if len(phrase) < 12:
    raise SystemExit("Passphrase must contain at least 12 characters.")
salt = secrets.token_hex(24)
digest = hashlib.scrypt(phrase.encode(), salt=salt.encode(), n=2**14, r=8, p=1).hex()
print(f"TEAM_PASSPHRASE_SALT={salt}\nTEAM_PASSPHRASE_HASH={digest}")
