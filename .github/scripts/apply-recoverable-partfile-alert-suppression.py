#!/usr/bin/env python3
from pathlib import Path

p = Path("src/peer_connection.cpp")
s = p.read_text()
old = '''\t\t\tbool const missing_partfile = error.operation == operation_t::partfile_read
\t\t\t\t&& (error.ec == boost::system::errc::no_such_file_or_directory
\t\t\t\t\t|| error.ec == boost::asio::error::eof
\t\t\t\t\t|| error.ec == errors::file_too_short);
\t\t\tif (missing_partfile)
\t\t\t\tt->partfile_read_failed(r.piece);

\t\t\twrite_dont_have(r.piece);
\t\t\twrite_reject_request(r);
\t\t\tif (t->alerts().should_post<file_error_alert>())
\t\t\t\tt->alerts().emplace_alert<file_error_alert>(error.ec
\t\t\t\t\t, t->resolve_filename(error.file())
\t\t\t\t\t, error.operation, t->get_handle());

\t\t\t++m_disk_read_failures;
\t\t\tif (m_disk_read_failures > 100) disconnect(error.ec, operation_t::file_read);
\t\t\treturn;
'''
new = '''\t\t\tbool const missing_partfile = error.operation == operation_t::partfile_read
\t\t\t\t&& (error.ec == boost::system::errc::no_such_file_or_directory
\t\t\t\t\t|| error.ec == boost::asio::error::eof
\t\t\t\t\t|| error.ec == errors::file_too_short);
\t\t\tif (missing_partfile)
\t\t\t{
\t\t\t\t// Missing auxiliary backing is a recoverable local condition, not a
\t\t\t\t// peer or disk failure. Demote the piece and reject this request while
\t\t\t\t// the part-file recovery path rebuilds the skipped ranges. Do not emit
\t\t\t\t// file_error_alert or count this toward peer disconnection.
\t\t\t\tt->partfile_read_failed(r.piece);
\t\t\t\twrite_dont_have(r.piece);
\t\t\t\twrite_reject_request(r);
\t\t\t\tm_disk_read_failures = 0;
\t\t\t\treturn;
\t\t\t}

\t\t\twrite_dont_have(r.piece);
\t\t\twrite_reject_request(r);
\t\t\tif (t->alerts().should_post<file_error_alert>())
\t\t\t\tt->alerts().emplace_alert<file_error_alert>(error.ec
\t\t\t\t\t, t->resolve_filename(error.file())
\t\t\t\t\t, error.operation, t->get_handle());

\t\t\t++m_disk_read_failures;
\t\t\tif (m_disk_read_failures > 100) disconnect(error.ec, operation_t::file_read);
\t\t\treturn;
'''
count = s.count(old)
if count != 1:
    raise SystemExit(f"expected one peer read-error anchor, found {count}")
p.write_text(s.replace(old, new, 1))
