from ftplib import FTP, FTP_TLS
from ssl import SSLSocket


class ReusedSslSocket(SSLSocket):
    def unwrap(self) -> None:
        pass


class Fixed_FTP_TLS(FTP_TLS):
    """Explicit FTPS, with shared TLS session.

    Based on https://stackoverflow.com/a/53456626/5723188,
    which is itself inspired from https://stackoverflow.com/a/43301750/5723188.
    """

    def ntransfercmd(self, cmd, rest=None):
        conn, size = FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:  # pyright: ignore[reportAttributeAccessIssue]
            conn = self.context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=self.sock.session,  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess]
            )  # reuses TLS session
            conn.__class__ = ReusedSslSocket  # we should not close reused ssl socket when file transfers finish
        return conn, size
