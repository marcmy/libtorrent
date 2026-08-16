from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one anchor, found {count}")
    p.write_text(text.replace(old, new, 1))


# file_progress needs the inverse of update() so a formerly-HAVE piece can
# be demoted without leaving per-file progress at 100%.
replace_once(
    "include/libtorrent/aux_/file_progress.hpp",
    "\t\tvoid update(file_storage const& fs, piece_index_t index\n"
    "\t\t\t, std::function<void(file_index_t)> const& completed_cb);\n",
    "\t\tvoid update(file_storage const& fs, piece_index_t index\n"
    "\t\t\t, std::function<void(file_index_t)> const& completed_cb);\n"
    "\n"
    "\t\t// Remove one previously-accounted HAVE piece. This is the exact\n"
    "\t\t// inverse of update() and is used when backing data disappears.\n"
    "\t\tvoid lost_piece(file_storage const& fs, piece_index_t index);\n")

replace_once(
    "src/file_progress.cpp",
    "\t}\n\n#if TORRENT_USE_INVARIANT_CHECKS\n\tvoid file_progress::check_invariant() const\n",
    "\t}\n\n"
    "\tvoid file_progress::lost_piece(file_storage const& fs, piece_index_t const index)\n"
    "\t{\n"
    "\t\tINVARIANT_CHECK;\n"
    "\t\tif (m_file_progress.empty()) return;\n"
    "\n"
    "#if TORRENT_USE_INVARIANT_CHECKS\n"
    "\t\tTORRENT_ASSERT(m_have_pieces.get_bit(index));\n"
    "\t\tm_have_pieces.clear_bit(index);\n"
    "#endif\n"
    "\n"
    "\t\tint const piece_size = fs.piece_length();\n"
    "\t\tstd::int64_t off = std::int64_t(static_cast<int>(index)) * piece_size;\n"
    "\t\tfile_index_t file_index = fs.file_index_at_offset(off);\n"
    "\t\tstd::int64_t size = fs.piece_size(index);\n"
    "\t\tfor (; size > 0; ++file_index)\n"
    "\t\t{\n"
    "\t\t\tstd::int64_t const file_offset = off - fs.file_offset(file_index);\n"
    "\t\t\tTORRENT_ASSERT(file_index != fs.end_file());\n"
    "\t\t\tTORRENT_ASSERT(file_offset <= fs.file_size(file_index));\n"
    "\t\t\tstd::int64_t const sub = std::min(fs.file_size(file_index)\n"
    "\t\t\t\t- file_offset, size);\n"
    "\n"
    "\t\t\tTORRENT_ASSERT(m_file_progress[file_index] >= sub);\n"
    "\t\t\tif (!fs.pad_file_at(file_index))\n"
    "\t\t\t{\n"
    "\t\t\t\tTORRENT_ASSERT(m_total_on_disk >= sub);\n"
    "\t\t\t\tm_total_on_disk -= sub;\n"
    "\t\t\t}\n"
    "\n"
    "\t\t\tm_file_progress[file_index] -= sub;\n"
    "\t\t\tsize -= sub;\n"
    "\t\t\toff += sub;\n"
    "\t\t\tTORRENT_ASSERT(size >= 0);\n"
    "\t\t}\n"
    "\t}\n\n"
    "#if TORRENT_USE_INVARIANT_CHECKS\n\tvoid file_progress::check_invariant() const\n")

# Focused accounting test: the removed piece overlaps both a normal file and
# a pad file, then update() restores exactly the original accounting.
test = Path("test/test_file_progress.cpp")
text = test.read_text()
if "TORRENT_TEST(lost_piece)" in text:
    raise SystemExit("test/test_file_progress.cpp: lost_piece test already exists")
text += '''

TORRENT_TEST(lost_piece)
{
\tint const piece_size = 256;

\tfile_storage fs;
\tfs.add_file("torrent/1", 200);
\tfs.add_file("torrent/2", 100, file_storage::flag_pad_file);
\tfs.add_file("torrent/3", 300);
\tfs.set_piece_length(piece_size);
\tfs.set_num_pieces(aux::calc_num_pieces(fs));

\tpiece_index_t const piece{0};
\taux::piece_picker picker(fs.total_size(), fs.piece_length());
\tpicker.piece_flushed(piece);

\taux::file_progress fp;
\tfp.init(picker, fs);

\taux::vector<std::int64_t, file_index_t> before;
\tfp.export_progress(before);
\tTEST_EQUAL(before[file_index_t{0}], 200);
\tTEST_EQUAL(before[file_index_t{1}], 56);
\tTEST_EQUAL(before[file_index_t{2}], 0);
\tTEST_EQUAL(fp.total_on_disk(), 200);

\tfp.lost_piece(fs, piece);

\taux::vector<std::int64_t, file_index_t> lost;
\tfp.export_progress(lost);
\tTEST_EQUAL(lost[file_index_t{0}], 0);
\tTEST_EQUAL(lost[file_index_t{1}], 0);
\tTEST_EQUAL(lost[file_index_t{2}], 0);
\tTEST_EQUAL(fp.total_on_disk(), 0);

\tfp.update(fs, piece, std::function<void(file_index_t)>{});

\taux::vector<std::int64_t, file_index_t> restored;
\tfp.export_progress(restored);
\tTEST_EQUAL(restored[file_index_t{0}], before[file_index_t{0}]);
\tTEST_EQUAL(restored[file_index_t{1}], before[file_index_t{1}]);
\tTEST_EQUAL(restored[file_index_t{2}], before[file_index_t{2}]);
\tTEST_EQUAL(fp.total_on_disk(), 200);
}
'''
test.write_text(text)

# Expose a narrow torrent-level transition for a live missing-partfile read.
replace_once(
    "include/libtorrent/aux_/torrent.hpp",
    "\t\tvoid handle_disk_error(string_view job_name\n"
    "\t\t\t, storage_error const& error, peer_connection* c = nullptr\n"
    "\t\t\t, disk_class rw = disk_class::none);\n",
    "\t\t// A live read discovered that skipped-file backing for a piece\n"
    "\t\t// disappeared. Demote the piece so its part-file ranges can be\n"
    "\t\t// reconstructed without rewriting wanted-file bytes.\n"
    "\t\tvoid partfile_read_failed(piece_index_t piece);\n"
    "\n"
    "\t\tvoid handle_disk_error(string_view job_name\n"
    "\t\t\t, storage_error const& error, peer_connection* c = nullptr\n"
    "\t\t\t, disk_class rw = disk_class::none);\n")

replace_once(
    "src/torrent.cpp",
    "\tvoid torrent::handle_disk_error(string_view job_name\n",
    "\tvoid torrent::partfile_read_failed(piece_index_t const piece)\n"
    "\t{\n"
    "\t\tif (m_abort || !valid_metadata()\n"
    "\t\t\t|| piece < piece_index_t{0}\n"
    "\t\t\t|| piece >= m_torrent_file->end_piece())\n"
    "\t\t\treturn;\n"
    "\n"
    "\t\tbool const was_finished = is_finished();\n"
    "\n"
    "\t\t// Materialize the full ownership map before dropping m_have_all,\n"
    "\t\t// then remove this piece from picker and file-progress together.\n"
    "\t\tif (m_seed_mode) leave_seed_mode(seed_mode_t::skip_checking);\n"
    "\t\tneed_picker();\n"
    "\t\tif (!m_picker->have_piece(piece)) return;\n"
    "\n"
    "\t\tm_file_progress.lost_piece(m_torrent_file->layout(), piece);\n"
    "\t\tm_picker->we_dont_have(piece);\n"
    "\t\tset_have_all(false);\n"
    "\t\tupdate_gauge();\n"
    "\n"
    "\t\tset_need_save_resume(torrent_handle::if_download_progress);\n"
    "\t\tupdate_peer_interest(was_finished);\n"
    "\t\tstate_updated();\n"
    "\t\tupdate_want_peers();\n"
    "\t}\n"
    "\n"
    "\tvoid torrent::handle_disk_error(string_view job_name\n")

# The existing recovery marker was set only while hashing. A normal upload
# read is the live signal that a deleted/short .parts file has disappeared.
replace_once(
    "src/pread_storage.cpp",
    "\t\t\t\tif (e)\n"
    "\t\t\t\t{\n"
    "\t\t\t\t\tif ((flags & disk_interface::v1_hash)\n"
    "\t\t\t\t\t\t&& (e == boost::system::errc::no_such_file_or_directory\n"
    "\t\t\t\t\t\t\t|| e == boost::asio::error::eof\n"
    "\t\t\t\t\t\t\t|| e == lt::errors::file_too_short))\n"
    "\t\t\t\t\t\tset_partfile_repair(piece, true);\n",
    "\t\t\t\tif (e)\n"
    "\t\t\t\t{\n"
    "\t\t\t\t\tif (m_v1\n"
    "\t\t\t\t\t\t&& (e == boost::system::errc::no_such_file_or_directory\n"
    "\t\t\t\t\t\t\t|| e == boost::asio::error::eof\n"
    "\t\t\t\t\t\t\t|| e == lt::errors::file_too_short))\n"
    "\t\t\t\t\t\tset_partfile_repair(piece, true);\n")

# Convert that live part-file upload failure into an internal piece demotion,
# in addition to rejecting the peer's current request.
replace_once(
    "src/peer_connection.cpp",
    "\t\t\twrite_dont_have(r.piece);\n"
    "\t\t\twrite_reject_request(r);\n",
    "\t\t\tbool const missing_partfile = error.operation == operation_t::partfile_read\n"
    "\t\t\t\t&& (error.ec == boost::system::errc::no_such_file_or_directory\n"
    "\t\t\t\t\t|| error.ec == boost::asio::error::eof\n"
    "\t\t\t\t\t|| error.ec == errors::file_too_short);\n"
    "\t\t\tif (missing_partfile)\n"
    "\t\t\t\tt->partfile_read_failed(r.piece);\n"
    "\n"
    "\t\t\twrite_dont_have(r.piece);\n"
    "\t\t\twrite_reject_request(r);\n")
