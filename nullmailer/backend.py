from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
import threading
import os
import time
import rfc822

NULLMAILER_SPOOLDIR = getattr(settings, 'NULLMAILER_SPOOLDIR', '/var/spool/nullmailer')

__nullmailer_threads_dict__ = {}

def to_utf8(addr):
    # RFC 6531 says that unicode email addresses must be encoded as utf8.
    # Also, according to this stackoverflow answer, if we have an unicode
    # email address, the only reasonable thing to do is to encode in utf8 and
    # hope that the target SMTP supports RFC 6531:
    #
    #  https://tools.ietf.org/html/rfc6531
    #  http://stackoverflow.com/a/14778640
    if isinstance(addr, unicode):
        return addr.encode('utf-8')
    return addr

def get_email_address(s):
    """
    Extract "foo@bar.com" from "Mr. Foo <foo@bar.com>"
    """
    # it seems that not all SMTP servers accept full names as
    # senders/recipient. At least the gmail and aruba require to use only the
    # "email address" part of the sender/recipient fields
    name, email = rfc822.parseaddr(s)
    return to_utf8(email)

class EmailBackend(BaseEmailBackend):

    __queue__ = "%s/queue" % NULLMAILER_SPOOLDIR
    __tmp__ = "%s/tmp" % NULLMAILER_SPOOLDIR
    __trigger__ = "%s/trigger" % NULLMAILER_SPOOLDIR

    def send_messages(self, email_messages):
        pid = os.getpid()
        tid = threading.current_thread().ident
        num_sent = 0
        if not email_messages:
            return
        for email_message in email_messages:
            to = email_message.to
            cc = email_message.cc or []
            bcc = email_message.bcc or []
            recipients = to + cc + bcc
            from_email = get_email_address(email_message.from_email)
            to_lines = '\n'.join([get_email_address(addr) for addr in recipients])
            msg = "%s\n%s\n\n%s" % ( from_email, to_lines, email_message.message().as_string())
            if self._send(msg, pid, tid):
                num_sent += 1
        return num_sent

    def fsyncspool(self):
        """
        Call fsync() on the queue directory
        """
        fd = -1
        try:
            fd = os.open(self.__queue__, os.O_RDONLY)
            os.fsync(fd)
        finally:
            if fd > -1: os.close(fd)

    def trigger(self):
        """
        Wakeup nullmailer writing to its trigger fifo
        """
        fd = -1
        try:
            fd = os.open(self.__trigger__, os.O_WRONLY|os.O_NONBLOCK)
            os.write(fd, b"\0")
        finally:
            if fd > -1: os.close(fd)


    def _send(self, data, pid, tid):
        global __nullmailer_threads_dict__
        if not tid in __nullmailer_threads_dict__:
            __nullmailer_threads_dict__[tid] = 0
        __nullmailer_threads_dict__[tid] += 1
        filename = "%f_%s_%d_%d_%d" % (time.time(), time.strftime("%Y.%m.%d.%H.%M.%S"), pid, tid, __nullmailer_threads_dict__[tid])
        tmp = "%s/%s" % (self.__tmp__, filename)
        spool = "%s/%s" % (self.__queue__, filename)

        with open(tmp, 'w') as f:
            f.write(data)

        try:
            os.link(tmp, spool)
            self.fsyncspool()
            self.trigger()
        finally:
            os.unlink(tmp)

        return True
