from pathlib import Path

source = Path("src/torrent_handle.cpp")
text = source.read_text(encoding="utf-8")
old = '''\t\tdisk_job_flags_t flags = disk_interface::sequential_access
\t\t\t| disk_interface::volatile_read | disk_interface::flush_piece;
\t\tif (torrent_file().info_hashes().has_v1() && !m_disable_v1_hashes)
\t\t\tflags |= disk_interface::v1_hash;

\t\tstd::uint8_t const gen = m_picker_generation;
'''
new = '''\t\tdisk_job_flags_t flags = disk_interface::sequential_access
\t\t\t| disk_interface::volatile_read | disk_interface::flush_piece;
\t\tif (torrent_file().info_hashes().has_v1() && !m_disable_v1_hashes)
\t\t\tflags |= disk_interface::v1_hash;

\t\t// Reopen files by path before hashing. Cached file handles may remain
\t\t// valid after a file is moved or replaced externally, notably on Windows.
\t\tm_ses.disk_thread().async_release_files(m_storage);

\t\tstd::uint8_t const gen = m_picker_generation;
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one selective recheck scheduling block, found {text.count(old)}")
source.write_text(text.replace(old, new), encoding="utf-8")

test = Path("test/test_selective_recheck.cpp")
text = test.read_text(encoding="utf-8")
old = '#include "libtorrent/aux_/path.hpp"\n#include "libtorrent/session.hpp"\n'
new = '#include "libtorrent/aux_/path.hpp"\n#include "libtorrent/alert_types.hpp"\n#include "libtorrent/session.hpp"\n'
if text.count(old) != 1:
    raise SystemExit(f"expected one include block, found {text.count(old)}")
text = text.replace(old, new)

old = '''\tp.flags &= ~torrent_flags::paused;
\tp.flags &= ~torrent_flags::auto_managed;
\tp.flags |= torrent_flags::seed_mode;
\ttorrent_handle h = ses.add_torrent(std::move(p), ec);
\tTEST_CHECK(!ec);
\tTEST_CHECK(h.is_valid());
\tTEST_CHECK(h.have_piece(0_piece));

\tremove(file_path, ec);
'''
new = '''\t// Match qBittorrent's manual test: the completed torrent is paused.
\tp.flags |= torrent_flags::paused;
\tp.flags &= ~torrent_flags::auto_managed;
\tp.flags |= torrent_flags::seed_mode;
\ttorrent_handle h = ses.add_torrent(std::move(p), ec);
\tTEST_CHECK(!ec);
\tTEST_CHECK(h.is_valid());
\tTEST_CHECK(h.have_piece(0_piece));

\t// Force the backing file to be opened first. On Windows an already-open
\t// handle can remain readable after the path is moved/deleted, which used to
\t// let selective recheck hash stale storage and incorrectly keep the piece.
\th.read_piece(0_piece);
\talert const* const a = wait_for_alert(ses, read_piece_alert::alert_type, "ses");
\tTEST_CHECK(a);
\tif (a)
\t{
\t\tread_piece_alert const* const rp = alert_cast<read_piece_alert>(a);
\t\tTEST_CHECK(rp);
\t\tif (rp) TEST_CHECK(!rp->error);
\t}

\tremove(file_path, ec);
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one seed setup block, found {text.count(old)}")
text = text.replace(old, new)
test.write_text(text, encoding="utf-8")
