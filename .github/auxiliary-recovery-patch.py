from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, found {count}")
    p.write_text(text.replace(old, new, 1))


def replace_count(path, old, new, expected):
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{path}: expected {expected} anchors, found {count}")
    p.write_text(text.replace(old, new))


def replace_in_section(path, start_marker, end_marker, old, new, expected=1):
    p = Path(path)
    text = p.read_text()
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    section = text[start:end]
    count = section.count(old)
    if count != expected:
        raise SystemExit(f"{path} section {start_marker!r}: expected {expected} anchors, found {count}")
    section = section.replace(old, new, expected)
    p.write_text(text[:start] + section + text[end:])


# ---------------------------------------------------------------------------
# part_file: if the user deletes .parts while qBittorrent is running, the
# in-memory piece->slot map survives. A subsequent write to an existing slot
# recreates the payload file, but historically did not mark the metadata dirty,
# so the recreated file could be left without a valid header. Always dirty the
# header on writes. This is cheap and makes live deletion recovery durable.
# ---------------------------------------------------------------------------
replace_count(
    "src/part_file.cpp",
    "\t\tslot_index_t const slot = (i == m_piece_map.end())\n"
    "\t\t\t? allocate_slot(piece) : i->second;\n"
    "\n"
    "\t\tl.unlock();\n",
    "\t\tslot_index_t const slot = (i == m_piece_map.end())\n"
    "\t\t\t? allocate_slot(piece) : i->second;\n"
    "\t\t// The on-disk file may have been deleted while this part_file stayed\n"
    "\t\t// alive. Rewriting an already-known slot must recreate the header too.\n"
    "\t\tm_dirty_metadata = true;\n"
    "\n"
    "\t\tl.unlock();\n",
    2)

# Focused regression test for the live-delete/header case.
test = Path("test/test_part_file.cpp")
text = test.read_text()
if "TORRENT_TEST(part_file_live_delete_recreate)" in text:
    raise SystemExit("part_file_live_delete_recreate already exists")
text += r'''

TORRENT_TEST(part_file_live_delete_recreate)
{
	error_code ec;
	std::string const cwd = complete(".");
	std::string const dir = combine_path(cwd, "partfile_live_delete_dir");
	std::string const filename = combine_path(dir, "partfile.parts");
	int const piece_size = 16 * 0x4000;

	remove_all(dir, ec);
	if (ec == boost::system::errc::no_such_file_or_directory) ec.clear();
	TEST_CHECK(!ec);
	create_directory(dir, ec);
	TEST_CHECK(!ec);

	std::array<char, 1024> buf;
	for (int i = 0; i < int(buf.size()); ++i)
		buf[std::size_t(i)] = static_cast<char>(i);

	{
		aux::part_file pf(dir, "partfile.parts", 100, piece_size);
		TEST_EQUAL(pf.write(buf, 10_piece, 0, ec), int(buf.size()));
		TEST_CHECK(!ec);
		pf.flush_metadata(ec);
		TEST_CHECK(!ec);

		remove(filename, ec);
		TEST_CHECK(!ec);

		// The in-memory map still knows piece 10. Recreate the same slot and
		// flush; the header must be written again as well as the payload.
		TEST_EQUAL(pf.write(buf, 10_piece, 0, ec), int(buf.size()));
		TEST_CHECK(!ec);
		pf.flush_metadata(ec);
		TEST_CHECK(!ec);
	}

	{
		aux::part_file pf(dir, "partfile.parts", 100, piece_size);
		std::array<char, 1024> out;
		out.fill(0);
		TEST_EQUAL(pf.read(out, 10_piece, 0, ec), int(out.size()));
		TEST_CHECK(!ec);
		TEST_CHECK(out == buf);
	}

	remove_all(dir, ec);
	TEST_CHECK(!ec);
}
'''
test.write_text(text)


# ---------------------------------------------------------------------------
# Default Windows backend (pread): generalize the existing one-shot
# partfile_repair state from "missing .parts bytes" to "missing auxiliary bytes
# from any priority-0 file". During recovery, skipped ranges are redirected to
# .parts and wanted ranges are discarded. After the backing-only hash succeeds,
# commit those skipped files to part-file routing before clearing recovery.
# ---------------------------------------------------------------------------
replace_once(
    "include/libtorrent/aux_/pread_storage.hpp",
    "\t\tbool partfile_repair(piece_index_t piece) const;\n"
    "\t\tvoid set_partfile_repair(piece_index_t piece, bool enabled);\n",
    "\t\tbool partfile_repair(piece_index_t piece) const;\n"
    "\t\tvoid set_partfile_repair(piece_index_t piece, bool enabled);\n"
    "\t\t// Called by the fenced recovery hash after a complete backing read.\n"
    "\t\t// Priority-0 files touched by this piece now use .parts permanently.\n"
    "\t\tvoid commit_partfile_recovery(piece_index_t piece);\n")

replace_once(
    "include/libtorrent/aux_/pread_storage.hpp",
    "\t\tvoid need_partfile();\n",
    "\t\tvoid need_partfile();\n"
    "\t\tbool skipped_file(file_index_t file) const;\n"
    "\t\tbool use_partfile_for_piece(file_index_t file, piece_index_t piece) const;\n"
    "\t\tvoid mark_auxiliary_read_failure(file_index_t file, piece_index_t piece\n"
    "\t\t\t, error_code const& ec);\n")

replace_once(
    "src/pread_storage.cpp",
    "\tvoid pread_storage::set_partfile_repair(piece_index_t const piece, bool const enabled)\n"
    "\t{\n"
    "\t\tstd::lock_guard<std::mutex> l(m_partfile_repair_mutex);\n"
    "\t\tif (m_partfile_repair.empty())\n"
    "\t\t\tm_partfile_repair.resize(files().num_pieces(), false);\n"
    "\t\tm_partfile_repair[piece] = enabled;\n"
    "\t}\n",
    "\tvoid pread_storage::set_partfile_repair(piece_index_t const piece, bool const enabled)\n"
    "\t{\n"
    "\t\tstd::lock_guard<std::mutex> l(m_partfile_repair_mutex);\n"
    "\t\tif (m_partfile_repair.empty())\n"
    "\t\t\tm_partfile_repair.resize(files().num_pieces(), false);\n"
    "\t\tm_partfile_repair[piece] = enabled;\n"
    "\t}\n"
    "\n"
    "\tbool pread_storage::skipped_file(file_index_t const file) const\n"
    "\t{\n"
    "\t\treturn file < m_file_priority.end_index()\n"
    "\t\t\t&& m_file_priority[file] == dont_download;\n"
    "\t}\n"
    "\n"
    "\tbool pread_storage::use_partfile_for_piece(file_index_t const file\n"
    "\t\t, piece_index_t const piece) const\n"
    "\t{\n"
    "\t\treturn skipped_file(file) && (use_partfile(file) || partfile_repair(piece));\n"
    "\t}\n"
    "\n"
    "\tvoid pread_storage::mark_auxiliary_read_failure(file_index_t const file\n"
    "\t\t, piece_index_t const piece, error_code const& e)\n"
    "\t{\n"
    "\t\tif (!m_v1 || !skipped_file(file)) return;\n"
    "\t\tif (e == boost::system::errc::no_such_file_or_directory\n"
    "\t\t\t|| e == boost::asio::error::eof\n"
    "\t\t\t|| e == lt::errors::file_too_short)\n"
    "\t\t\tset_partfile_repair(piece, true);\n"
    "\t}\n"
    "\n"
    "\tvoid pread_storage::commit_partfile_recovery(piece_index_t const piece)\n"
    "\t{\n"
    "\t\tfor (file_slice const& slice : files().map_block(piece, 0, files().piece_size(piece)))\n"
    "\t\t{\n"
    "\t\t\tfile_index_t const file = slice.file_index;\n"
    "\t\t\tif (files().pad_file_at(file) || !skipped_file(file)) continue;\n"
    "\t\t\tuse_partfile(file, true);\n"
    "\t\t}\n"
    "\t}\n")

# Keep a part_file object available whenever priority-0 files exist. Constructing
# it only reads metadata; the actual .parts file is still created lazily on write.
replace_once(
    "src/pread_storage.cpp",
    "\t\t\tif (m_file_priority[i] == dont_download && use_partfile(i))\n"
    "\t\t\t{\n"
    "\t\t\t\tneed_partfile();\n"
    "\t\t\t}\n",
    "\t\t\tif (m_file_priority[i] == dont_download)\n"
    "\t\t\t\tneed_partfile();\n")

replace_once(
    "src/pread_storage.cpp",
    "\t\t\tif (m_file_priority[i] != dont_download || fs.pad_file_at(i))\n"
    "\t\t\t\tcontinue;\n"
    "\n"
    "\t\t\terror_code err;\n",
    "\t\t\tif (m_file_priority[i] != dont_download || fs.pad_file_at(i))\n"
    "\t\t\t\tcontinue;\n"
    "\n"
    "\t\t\tneed_partfile();\n"
    "\t\t\terror_code err;\n")

# Resume an interrupted check based on mixed wanted/skipped ownership, not on
# whether the skipped file happened to already be globally routed to .parts.
replace_once(
    "src/pread_storage.cpp",
    "\t\t\tbool has_wanted = false;\n"
    "\t\t\tbool has_partfile = false;\n"
    "\t\t\tbool wanted_backing_complete = true;\n",
    "\t\t\tbool has_wanted = false;\n"
    "\t\t\tbool has_skipped = false;\n"
    "\t\t\tbool wanted_backing_complete = true;\n")
replace_once(
    "src/pread_storage.cpp",
    "\t\t\t\tif (skipped)\n"
    "\t\t\t\t{\n"
    "\t\t\t\t\tif (use_partfile(file)) has_partfile = true;\n"
    "\t\t\t\t\tcontinue;\n"
    "\t\t\t\t}\n",
    "\t\t\t\tif (skipped)\n"
    "\t\t\t\t{\n"
    "\t\t\t\t\thas_skipped = true;\n"
    "\t\t\t\t\tcontinue;\n"
    "\t\t\t\t}\n")
replace_once(
    "src/pread_storage.cpp",
    "\t\t\t// Only protect a mixed wanted/.parts piece when the wanted backing\n"
    "\t\t\t// file is still physically complete. If a wanted file is genuinely\n"
    "\t\t\t// missing or short, normal repair must be allowed to create it.\n"
    "\t\t\t// If wanted bytes merely hash wrong, the protected backing-storage\n"
    "\t\t\t// verification will fail, clear recovery, and the next attempt becomes\n"
    "\t\t\t// an ordinary repair.\n"
    "\t\t\tif (has_wanted && has_partfile && wanted_backing_complete)\n"
    "\t\t\t\tset_partfile_repair(piece, true);\n",
    "\t\t\t// The missing auxiliary bytes may have lived in .parts or in an\n"
    "\t\t\t// old-style/qBittorrent .unwanted file. If wanted backing is still\n"
    "\t\t\t// physically complete, recover only the priority-0 ranges into .parts.\n"
    "\t\t\tif (has_wanted && has_skipped && wanted_backing_complete)\n"
    "\t\t\t\tset_partfile_repair(piece, true);\n")

# Four callback-based routing sites (read, writev, write, hash).
replace_count(
    "src/pread_storage.cpp",
    "\t\t\tif (file_index < m_file_priority.end_index()\n"
    "\t\t\t\t&& m_file_priority[file_index] == dont_download\n"
    "\t\t\t\t&& use_partfile(file_index))\n",
    "\t\t\tif (use_partfile_for_piece(file_index, piece))\n",
    4)
# hash2 is direct code rather than a callback.
replace_once(
    "src/pread_storage.cpp",
    "\t\tif (file_index < m_file_priority.end_index()\n"
    "\t\t\t&& m_file_priority[file_index] == dont_download\n"
    "\t\t\t&& use_partfile(file_index))\n",
    "\t\tif (use_partfile_for_piece(file_index, piece))\n")

# Ordinary skipped-file reads/hashes (the .unwanted/legacy path) now seed the
# same recovery marker when that backing vanishes or becomes too short.
replace_in_section(
    "src/pread_storage.cpp",
    "\tint pread_storage::read(",
    "\n\tint pread_storage::write(",
    "\t\t\tauto handle = open_file(sett, file_index, mode, ec);\n"
    "\t\t\tif (ec) return -1;\n",
    "\t\t\tauto handle = open_file(sett, file_index, mode, ec);\n"
    "\t\t\tif (ec)\n"
    "\t\t\t{\n"
    "\t\t\t\tmark_auxiliary_read_failure(file_index, piece, ec.ec);\n"
    "\t\t\t\treturn -1;\n"
    "\t\t\t}\n")
replace_in_section(
    "src/pread_storage.cpp",
    "\tint pread_storage::read(",
    "\n\tint pread_storage::write(",
    "\t\t\tif (ec.ec) {\n"
    "\t\t\t\tec.file(file_index);\n"
    "\t\t\t\treturn ret;\n"
    "\t\t\t}\n",
    "\t\t\tif (ec.ec) {\n"
    "\t\t\t\tmark_auxiliary_read_failure(file_index, piece, ec.ec);\n"
    "\t\t\t\tec.file(file_index);\n"
    "\t\t\t\treturn ret;\n"
    "\t\t\t}\n")
replace_in_section(
    "src/pread_storage.cpp",
    "\tint pread_storage::hash(",
    "\n\tint pread_storage::hash2(",
    "\t\t\tauto handle = open_file(sett, file_index, mode, ec);\n"
    "\t\t\tif (ec) return -1;\n",
    "\t\t\tauto handle = open_file(sett, file_index, mode, ec);\n"
    "\t\t\tif (ec)\n"
    "\t\t\t{\n"
    "\t\t\t\tmark_auxiliary_read_failure(file_index, piece, ec.ec);\n"
    "\t\t\t\treturn -1;\n"
    "\t\t\t}\n")
replace_in_section(
    "src/pread_storage.cpp",
    "\tint pread_storage::hash(",
    "\n\tint pread_storage::hash2(",
    "\t\t\tif (ec.ec)\n"
    "\t\t\t{\n"
    "\t\t\t\tec.file(file_index);\n"
    "\t\t\t\tec.operation = operation_t::file_read;\n"
    "\t\t\t\treturn ret;\n"
    "\t\t\t}\n",
    "\t\t\tif (ec.ec)\n"
    "\t\t\t{\n"
    "\t\t\t\tmark_auxiliary_read_failure(file_index, piece, ec.ec);\n"
    "\t\t\t\tec.file(file_index);\n"
    "\t\t\t\tec.operation = operation_t::file_read;\n"
    "\t\t\t\treturn ret;\n"
    "\t\t\t}\n")

# The backing-only recovery hash runs behind a storage fence. Commit its skipped
# files to .parts while still fenced, then clear the one-shot write suppression.
replace_once(
    "src/pread_disk_io.cpp",
    "\t\t\tj->storage->drop_precomputed_v2(a.piece);\n"
    "\t\t\tj->storage->set_partfile_repair(a.piece, false);\n",
    "\t\t\tj->storage->drop_precomputed_v2(a.piece);\n"
    "\t\t\tj->storage->commit_partfile_recovery(a.piece);\n"
    "\t\t\tj->storage->set_partfile_repair(a.piece, false);\n")
