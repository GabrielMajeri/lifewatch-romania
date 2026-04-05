from dataclasses import dataclass

from paramiko import PKey


@dataclass
class SFTPCredentials:
    """Class holding all information required to identify and connect to
    a SFTP (FTP over SSH) server.
    """

    host: str
    port: int
    username: str
    password: str | None
    private_key: PKey | None
