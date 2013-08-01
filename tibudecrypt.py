#!/usr/bin/env python
"""
 File Format
 Information taken from Christian Egger's G+ page.
 https://plus.google.com/101760059763010172705/posts/MQBmYhKDex5
=====
 "TB_ARMOR_V1" '\\n'
 pass_hmac_key '\\n'
 pass_hmac_result '\\n'
 public_key '\\n'
 enc_privkey_spec '\\n'
 enc_sesskey_spec '\\n'
 Data
=====
 Each of the 5 "variables" (pass_hmac_key, pass_hmac_result,
 public_key, enc_privkey_spec, enc_sesskey_spec) is stored in
 Base64 format without linewraps (of course) and can be decoded with:
 Base64.decode( pass_hmac_key, Base64.NO_WRAP)

 Then the user-supplied passphrase (String) can be verified as follows:
 Mac mac = Mac.getInstance("HmacSHA1");
 mac.init(new SecretKeySpec(pass_hmac_key, "HmacSHA1"));
 byte[] sigBytes = mac.doFinal(passphrase.getBytes("UTF-8"));
 boolean passphraseMatches = Arrays.equals(sigBytes, pass_hmac_result);

 Then the passphrase is independently hashed with SHA-1. We append 0x00 bytes
 to the 160-bit result to constitute the 256-bit AES key which is used to
 decrypt "enc_privkey_spec" (with an IV of 0x00 bytes).

Then we build the KeyPair object as follows:
 KeyFactory keyFactory = KeyFactory.getInstance("RSA");
 PrivateKey privateKey2 = keyFactory.generatePrivate(
   new PKCS8EncodedKeySpec(privateKey));
 PublicKey public_key2 = keyFactory.generatePublic(
   new X509EncodedKeySpec(public_key));
 KeyPair keyPair = new KeyPair(public_key2, privateKey2);

Then we decrypt the session key as follows:
 Cipher rsaDecrypt = Cipher.getInstance("RSA/NONE/PKCS1Padding");
 rsaDecrypt.init(Cipher.DECRYPT_MODE, keyPair.getPrivate());
 ByteArrayOutputStream baos = new ByteArrayOutputStream();
 CipherOutputStream cos = new CipherOutputStream(baos, rsaDecrypt);
 cos.write(enc_sesskey_spec); cos.close();
 byte[] sessionKey = baos.toByteArray();

 And finally, we decrypt the data itself with the session key (which can be
 either a 128-bit, 192-bit or 256-bit key) and with a 0x00 IV.

 While the "zero" IV is suboptimal from a security standpoint, it allows
 files to be encoded faster - because every little bit counts, especially
 when we store backups with LZO compression.

 Use:
 As of 2013/03/04 this script requires the pkcs8 branch of
 https://github.com/Legrandin/pycrypto in order to run correctly.
 Standard PyCrypto does not yet support PKCS#8

 ./tibudecrypt.py filename
"""

from __future__ import print_function
import os
import sys
import base64
import getpass
import hashlib
import hmac
import six
import Crypto.Cipher.AES
import Crypto.Cipher.PKCS1_v1_5
import Crypto.PublicKey.RSA


def pkcs5_unpad(data):
    """Return data after PKCS5 unpadding

    With python3 bytes are already treated as arrays of ints so
    we don't have to convert them with ord.
    """
    if not six.PY3:
        return data[0:-ord(data[-1])]
    else:
        return data[0:-data[-1]]


def aes_decrypt(key, data):
    """
    Decrypt AES encrypted data.
    IV is 16 bytes of 0x00 as specified by Titanium.
    Performs PKCS5 unpadding when required.
    """
    iv = 16 * chr(0x00)
    dec = Crypto.Cipher.AES.new(
        key,
        mode=Crypto.Cipher.AES.MODE_CBC,
        IV=iv)
    decrypted = dec.decrypt(data)
    return pkcs5_unpad(decrypted)


class InvalidHeader(Exception):
    """
    Raised when the header for a file doesn't match a valid
    Titanium Backup header.
    """


class PasswordMismatchError(Exception):
    """
    Raised when the given password is incorrect
    (hmac digest doesn't match expected digest)
    """


class TiBUFile(object):
    """
    Class for performing decryption on Titanium Backup encrypted files.
    """
    def __init__(self, filename):
        self._VALID_HEADER = 'TB_ARMOR_V1'
        self.hashed_pass = None
        self.filepart = None
        self.filename = filename
        self.check_header()
        self.read_file()

    def check_header(self):
        """
        Checks that the file header matches the Titanium Armor header
        raises the InvalidHeader exception if there is no match.
        """
        header_len = len(self._VALID_HEADER)
        with open(self.filename, 'rb') as in_file:
            data = in_file.read(header_len).decode('utf-8')

        if not (len(data) == header_len
                and data == self._VALID_HEADER):
            raise InvalidHeader('Invalid header')

    def check_password(self, password):
        """
        Performs HMAC password verification and hashes the password
        for use when decrypting the private key and session key.
        """
        mac = hmac.new(
            self.filepart['pass_hmac_key'],
            bytes(password),
            hashlib.sha1)
        if mac.digest() == self.filepart['pass_hmac_result']:
            sha1 = hashlib.sha1()
            sha1.update(password)
            self.hashed_pass = sha1.digest().ljust(
                32, bytes(chr(0x00).encode('ascii')))
        else:
            raise PasswordMismatchError('Password Mismatch')

    def decrypt(self):
        """
        Decrypts the encrypted data using the private keys provided
        in the encrypted Titanium Backup file.
        """
        dec_privkey_spec = aes_decrypt(
            self.hashed_pass,
            self.filepart['enc_privkey_spec'])

        rsa_privkey = Crypto.PublicKey.RSA.importKey(
            dec_privkey_spec)
        # Public key isn't used for decryption.
        #rsaPublicKey = Crypto.PublicKey.RSA.importKey(
        #        self.filepart['public_key'])
        cipher = Crypto.Cipher.PKCS1_v1_5.new(rsa_privkey)
        dec_sesskey = cipher.decrypt(
            self.filepart['enc_sesskey_spec'],
            None)
        decrypted_data = aes_decrypt(
            dec_sesskey,
            self.filepart['enc_data'])

        return decrypted_data

    def read_file(self):
        """
        Reads the encrypted file and splits out the 7 sections that
        we're interested in.
        """
        try:
            with open(self.filename, 'rb') as in_file:
                (header, pass_hmac_key,
                 pass_hmac_result, public_key,
                 enc_privkey_spec, enc_sesskey_spec,
                 enc_data) = in_file.read().split(b'\n', 6)
        except:
            raise

        self.filepart = {
            'header': header,
            'pass_hmac_key': base64.b64decode(pass_hmac_key),
            'pass_hmac_result': base64.b64decode(pass_hmac_result),
            'public_key': base64.b64decode(public_key),
            'enc_privkey_spec': base64.b64decode(enc_privkey_spec),
            'enc_sesskey_spec': base64.b64decode(enc_sesskey_spec),
            'enc_data': enc_data
        }


def main(args):
    try:
        filename = args[1]
    except NameError:
        return "Supply a file to decrypt."

    try:
        encrypted_file = TiBUFile(filename)
    except InvalidHeader as exc:
        return "Not a Titanium Backup encrypted file: {e}".format(e=exc)
    except IOError as exc:
        return "Error. {e}".format(e=exc)

    try:
        password = getpass.getpass()
        encrypted_file.check_password(bytes(password.encode('utf-8')))
    except PasswordMismatchError as exc:
        return "Error: {e}".format(e=exc)

    decrypted_file = encrypted_file.decrypt()

    try:
        decrypted_filename = "decrypted-{filename}".format(
            filename=os.path.basename(filename))
        with open(decrypted_filename, 'wb') as out_file:
            out_file.write(decrypted_file)
    except IOError as exc:
        return "Error while writing decrypted data: {e}".format(
            e=exc.strerror)

    print("Success. Decrypted file '{decrypted_filename}' written.".format(
        decrypted_filename=decrypted_filename))

if __name__ == '__main__':
    sys.exit(main(sys.argv))
