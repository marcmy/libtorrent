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


# --- part_file: expose durable per-piece backing ownership -----------------
replace_once(
    "include/libtorrent/aux_/part_file.hpp",
    "\t\tint hash2(hasher256& ph, std::ptrdiff_t len, piece_index_t piece, int offset, error_code& ec);\n",
    "\t\tint hash2(hasher256& ph, std::ptrdiff_t len, piece_index_t piece, int offset, error_code& ec);\n"
    "\n"
    "\t\t// True when this piece has an allocated slot in the part-file header.\n"
    "\t\t// Recovery uses this as a durable per-piece routing override so a\n"
    "\t\t// boundary piece rebuilt from a deleted .unwanted file continues to\n"
    "\t\t// read its skipped bytes from .parts after recovery mode clears.\n"
    "\t\tbool has_piece(piece_index_t piece);\n")

replace_once(
    "src/part_file.cpp",
    "\tvoid part_file::free_piece(piece_index_t const piece)\n",
    "\tbool part_file::has_piece(piece_index_t const piece)\n"
    "\t{\n"
    "\t\tstd::lock_guard<std::mutex> l(m_mutex);\n"
    "\t\treturn m_piece_map.find(piece) != m_piece_map.end();\n"
    "\t}\n"
    "\n"
    "\tvoid part_file::free_piece(piece_index_t const piece)\n")

# If the .parts file was deleted while the process was alive, rewriting an
# already-known slot must also rewrite the header on the next metadata flush.
replace_count(
    "src/part_file.cpp",
    "\t\tslot_index_t const slot = (i == m_piece_map.end())\n"
    "\t\t\t? allocate_slot(piece) : i->second;\n"
    "\n"
    "\t\tl.unlock();\n",
    "\t\tslot_index_t const slot = (i == m_piece_map.end())\n"
    "\t\t\t? allocate_slot(piece) : i->second;\n"
    "\t\t// A live deletion can recreate the payload file while the in-memory\n"
    "\t\t// map still knows this slot. Mark metadata dirty on every write so\n"
    "\t\t// the recreated file gets a valid header as well.\n"
    "\t\tm_dirty_metadata = true;\n"
    "\n"
    "\t\tl.unlock();\n",
    2)

# A tiny persistence/routing test for has_piece().
replace_once(
    "test/test_part_file.cpp",
    "\t\tpf.write(buf, 10_piece, 0, ec);\n"
    "\t\tif (ec) std::printf(\"part_file::write: %s\\n\", ec.message().c_str());\n",
    "\t\tpf.write(buf, 10_piece, 0, ec);\n"
    "\t\tif (ec) std::printf(\"part_file::write: %s\\n\", ec.message().c_str());\n"
    "\t\tTEST_CHECK(pf.has_piece(10_piece));\n"
    "\t\tTEST_CHECK(!pf.has_piece(11_piece));\n")

replace_once(
    "test/test_part_file.cpp",
    "\t\taux::part_file pf(combine_path(cwd, \"partfile_test_dir2\"), \"partfile.parts\", 100, piece_size);\n"
    "\n"
    "\t\tbuf.fill(0);\n",
    "\t\taux::part_file pf(combine_path(cwd, \"partfile_test_dir2\"), \"partfile.parts\", 100, piece_size);\n"
    "\t\tTEST_CHECK(pf.has_piece(10_piece));\n"
    "\t\tTEST_CHECK(!pf.has_piece(11_piece));\n"
    "\n"
    "\t\tbuf.fill(0);\n")

replace_once(
    "test/test_part_file.cpp",
    "\t\tpf.free_piece(10_piece);\n"
    "\n"
    "\t\tpf.flush_metadata(ec);\n",
    "\t\tpf.free_piece(10_piece);\n"
    "\t\tTEST_CHECK(!pf.has_piece(10_piece));\n"
    "\n"
    "\t\tpf.flush_metadata(ec);\n")


# --- pread/default backend -------------------------------------------------
replace_once(
    "include/libtorrent/aux_/pread_storage.hpp",
    "\t\tvoid need_partfile();\n",
    "\t\tvoid need_partfile();\n"
    "\t\tbool skipped_file(file_index_t file) const;\n"
    "\t\tbool use_partfile_for_piece(file_index_t file, piece_index_t piece);\n"
    "\t\tvoid mark_auxiliary_read_failure(file_index_t file, piece_index_t piece, error_code const& ec);\n")

# Serialize lazy construction with the existing recovery mutex. The part-file
# object is never replaced during normal I/O, only under storage fences.
replace_once(
    "src/pread_storage.cpp",
    "\tvoid pread_storage::need_partfile()\n"
    "\t{\n"
    "\t\tif (m_part_file) return;\n"
    "\n"
    "\t\tm_part_file = std::make_unique<part_file>(\n"
    "\t\t\tm_part_file_dir.empty() ? m_save_path : combine_path(m_save_path, m_part_file_dir)\n"
    "\t\t\t, m_part_file_name\n"
    "\t\t\t, files().num_pieces(), files().piece_length());\n"
    "\t}\n",
    "\tvoid pread_storage::need_partfile()\n"
    "\t{\n"
    "\t\tstd::lock_guard<std::mutex> l(m_partfile_repair_mutex);\n"
    "\t\tif (m_part_file) return;\n"
    "\n"
    "\t\tm_part_file = std::make_unique<part_file>(\n"
    "\t\t\tm_part_file_dir.empty() ? m_save_path : combine_path(m_save_path, m_part_file_dir)\n"
    "\t\t\t, m_part_file_name\n"
    "\t\t\t, files().num_pieces(), files().piece_length());\n"
    "\t}\n")

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
    "\tbool pread_storage::use_partfile_for_piece(file_index_t const file, piece_index_t const piece)\n"
    "\t{\n"
    "\t\tif (!skipped_file(file)) return false;\n"
    "\t\tif (use_partfile(file) || partfile_repair(piece)) return true;\n"
    "\t\treturn m_part_file && m_part_file->has_piece(piece);\n"
    "\t}\n"
    "\n"
    "\tvoid pread_storage::mark_auxiliary_read_failure(file_index_t const file\n"
    "\t\t, piece_index_t const piece, error_code const& e)\n"
    "\t{\n"
    "\t\tif (!m_v1 || !skipped_file(file)) return;\n"
    "\t\tif (e != boost::system::errc::no_such_file_or_directory\n"
    "\t\t\t&& e != boost::asio::error::eof\n"
    "\t\t\t&& e != lt::errors::file_too_short)\n"
    "\t\t\treturn;\n"
    "\n"
    "\t\tneed_partfile();\n"
    "\t\tset_partfile_repair(piece, true);\n"
    "\t}\n")

# When an ignored file is selected again, export every part-file fragment for
# it, including per-piece recovery fragments that were created while the file
# still used old-style/.unwanted backing globally.
replace_once(
    "src/pread_storage.cpp",
    "\t\t\t\tif (m_part_file && use_partfile(i))\n",
    "\t\t\t\tif (m_part_file)\n")

# Always instantiate the lightweight part_file object for skipped files. It
# loads any durable per-piece recovery map if a .parts file exists, but does
# not create a disk file until the first write.
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

# Interrupted-check reconstruction now protects any mixed wanted/skipped piece,
# not only pieces whose skipped file was already globally assigned to .parts.
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
    "\t\t\t// Protect any mixed wanted/skipped piece when the wanted backing is\n"
    "\t\t\t// physically complete. The skipped bytes may have lived in .parts or\n"
    "\t\t\t// in qBittorrent's .unwanted file; recovery always rebuilds only that\n"
    "\t\t\t// auxiliary range into .parts. If the wanted bytes are genuinely bad,\n"
    "\t\t\t// the backing-storage hash will still fail and the following attempt\n"
    "\t\t\t// is allowed to perform an ordinary wanted-file repair.\n"
    "\t\t\tif (has_wanted && has_skipped && wanted_backing_complete)\n"
    "\t\t\t{\n"
    "\t\t\t\tneed_partfile();\n"
    "\t\t\t\tset_partfile_repair(piece, true);\n"
    "\t\t\t}\n")

# Route skipped slices through .parts when either the file normally uses it,
# this piece is actively recovering, or this piece already owns a durable slot.
replace_count(
    "src/pread_storage.cpp",
    "\t\t\tif (file_index < m_file_priority.end_index()\n"
    "\t\t\t\t&& m_file_priority[file_index] == dont_download\n"
    "\t\t\t\t&& use_partfile(file_index))\n",
    "\t\t\tif (use_partfile_for_piece(file_index, piece))\n",
    5)

# If old-style/.unwanted backing disappears during a live check, classify the
# failure immediately so Start uses the protected recovery path.
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


# --- mmap backend: keep semantics aligned ---------------------------------
replace_once(
    "include/libtorrent/aux_/mmap_storage.hpp",
    "\t\tvoid need_partfile();\n",
    "\t\tvoid need_partfile();\n"
    "\t\tbool skipped_file(file_index_t file) const;\n"
    "\t\tbool use_partfile_for_piece(file_index_t file, piece_index_t piece);\n")

replace_once(
    "src/mmap_storage.cpp",
    "\tvoid mmap_storage::need_partfile()\n"
    "\t{\n"
    "\t\tif (m_part_file) return;\n"
    "\n"
    "\t\tm_part_file = std::make_unique<part_file>(\n"
    "\t\t\tm_part_file_dir.empty() ? m_save_path : combine_path(m_save_path, m_part_file_dir)\n"
    "\t\t\t, m_part_file_name\n"
    "\t\t\t, files().num_pieces(), files().piece_length());\n"
    "\t}\n",
    "\tvoid mmap_storage::need_partfile()\n"
    "\t{\n"
    "\t\tstd::lock_guard<std::mutex> l(m_partfile_repair_mutex);\n"
    "\t\tif (m_part_file) return;\n"
    "\n"
    "\t\tm_part_file = std::make_unique<part_file>(\n"
    "\t\t\tm_part_file_dir.empty() ? m_save_path : combine_path(m_save_path, m_part_file_dir)\n"
    "\t\t\t, m_part_file_name\n"
    "\t\t\t, files().num_pieces(), files().piece_length());\n"
    "\t}\n")

replace_once(
    "src/mmap_storage.cpp",
    "\tvoid mmap_storage::set_partfile_repair(piece_index_t const piece, bool const enabled)\n"
    "\t{\n"
    "\t\tstd::lock_guard<std::mutex> l(m_partfile_repair_mutex);\n"
    "\t\tif (m_partfile_repair.empty())\n"
    "\t\t\tm_partfile_repair.resize(files().num_pieces(), false);\n"
    "\t\tm_partfile_repair[piece] = enabled;\n"
    "\t}\n",
    "\tvoid mmap_storage::set_partfile_repair(piece_index_t const piece, bool const enabled)\n"
    "\t{\n"
    "\t\tstd::lock_guard<std::mutex> l(m_partfile_repair_mutex);\n"
    "\t\tif (m_partfile_repair.empty())\n"
    "\t\t\tm_partfile_repair.resize(files().num_pieces(), false);\n"
    "\t\tm_partfile_repair[piece] = enabled;\n"
    "\t}\n"
    "\n"
    "\tbool mmap_storage::skipped_file(file_index_t const file) const\n"
    "\t{\n"
    "\t\treturn file < m_file_priority.end_index()\n"
    "\t\t\t&& m_file_priority[file] == dont_download;\n"
    "\t}\n"
    "\n"
    "\tbool mmap_storage::use_partfile_for_piece(file_index_t const file, piece_index_t const piece)\n"
    "\t{\n"
    "\t\tif (!skipped_file(file)) return false;\n"
    "\t\tif (use_partfile(file) || partfile_repair(piece)) return true;\n"
    "\t\treturn m_part_file && m_part_file->has_piece(piece);\n"
    "\t}\n")

replace_once(
    "src/mmap_storage.cpp",
    "\t\t\t\tif (m_part_file && use_partfile(i))\n",
    "\t\t\t\tif (m_part_file)\n")
replace_once(
    "src/mmap_storage.cpp",
    "\t\t\tif (m_file_priority[i] != dont_download || fs.pad_file_at(i))\n"
    "\t\t\t\tcontinue;\n"
    "\n"
    "\t\t\terror_code err;\n",
    "\t\t\tif (m_file_priority[i] != dont_download || fs.pad_file_at(i))\n"
    "\t\t\t\tcontinue;\n"
    "\n"
    "\t\t\tneed_partfile();\n"
    "\t\t\terror_code err;\n")
replace_once(
    "src/mmap_storage.cpp",
    "\t\t\tbool has_wanted = false;\n"
    "\t\t\tbool has_partfile = false;\n"
    "\t\t\tbool wanted_backing_complete = true;\n",
    "\t\t\tbool has_wanted = false;\n"
    "\t\t\tbool has_skipped = false;\n"
    "\t\t\tbool wanted_backing_complete = true;\n")
replace_once(
    "src/mmap_storage.cpp",
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
    "src/mmap_storage.cpp",
    "\t\t\t// Only protect a mixed wanted/.parts piece when the wanted backing\n"
    "\t\t\t// file is still physically complete. If a wanted file is genuinely\n"
    "\t\t\t// missing or short, normal repair must be allowed to create it.\n"
    "\t\t\t// If wanted bytes merely hash wrong, the protected backing-storage\n"
    "\t\t\t// verification will fail, clear recovery, and the next attempt becomes\n"
    "\t\t\t// an ordinary repair.\n"
    "\t\t\tif (has_wanted && has_partfile && wanted_backing_complete)\n"
    "\t\t\t\tset_partfile_repair(piece, true);\n",
    "\t\t\t// A previously checked mixed piece may have lost either .parts or\n"
    "\t\t\t// old-style/.unwanted skipped backing. Rebuild only the skipped range\n"
    "\t\t\t// into .parts while leaving physically complete wanted files alone.\n"
    "\t\t\tif (has_wanted && has_skipped && wanted_backing_complete)\n"
    "\t\t\t{\n"
    "\t\t\t\tneed_partfile();\n"
    "\t\t\t\tset_partfile_repair(piece, true);\n"
    "\t\t\t}\n")

# mmap has four skipped-file routing sites: read, write, hash and hash2.
replace_count(
    "src/mmap_storage.cpp",
    "\t\t\tif (file_index < m_file_priority.end_index()\n"
    "\t\t\t\t&& m_file_priority[file_index] == dont_download\n"
    "\t\t\t\t&& use_partfile(file_index))\n",
    "\t\t\tif (use_partfile_for_piece(file_index, piece))\n",
    4)

# mmap read needs piece captured now that routing is per-piece.
replace_once(
    "src/mmap_storage.cpp",
    "\t\t\t, [this, mode, flags, &sett](file_index_t const file_index\n",
    "\t\t\t, [this, piece, mode, flags, &sett](file_index_t const file_index\n")

# Source-only changes should be clean before the workflow commits them.
