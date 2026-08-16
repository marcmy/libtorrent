from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    s = p.read_text()
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{path}: expected one anchor, found {n}")
    p.write_text(s.replace(old, new, 1))


# Distinguish "this piece really has backing in .parts" from merely having a
# priority-0 file configured to use the part file. This prevents interrupted
# checks from classifying unrelated wanted-byte corruption as auxiliary loss.
replace_once(
    "include/libtorrent/aux_/part_file.hpp",
    "\t\tint hash2(hasher256& ph, std::ptrdiff_t len, piece_index_t piece, int offset, error_code& ec);\n",
    "\t\tint hash2(hasher256& ph, std::ptrdiff_t len, piece_index_t piece, int offset, error_code& ec);\n"
    "\n"
    "\t\t// Whether the loaded part-file header maps this piece to a slot.\n"
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

# Only commit an old-style/.unwanted file to global .parts routing if the
# backing file is still missing/short for this slice. If it exists and is
# complete, leave it alone. Files already using .parts remain unchanged.
replace_once(
    "src/pread_storage.cpp",
    "\tvoid pread_storage::commit_partfile_recovery(piece_index_t const piece)\n"
    "\t{\n"
    "\t\tfor (file_slice const& slice : files().map_block(piece, 0, files().piece_size(piece)))\n"
    "\t\t{\n"
    "\t\t\tfile_index_t const file = slice.file_index;\n"
    "\t\t\tif (files().pad_file_at(file) || !skipped_file(file)) continue;\n"
    "\t\t\tuse_partfile(file, true);\n"
    "\t\t}\n"
    "\t}\n",
    "\tvoid pread_storage::commit_partfile_recovery(piece_index_t const piece)\n"
    "\t{\n"
    "\t\tfilenames const fs = names();\n"
    "\t\tfor (file_slice const& slice : files().map_block(piece, 0, files().piece_size(piece)))\n"
    "\t\t{\n"
    "\t\t\tfile_index_t const file = slice.file_index;\n"
    "\t\t\tif (fs.pad_file_at(file) || !skipped_file(file) || use_partfile(file)) continue;\n"
    "\n"
    "\t\t\tfile_status st;\n"
    "\t\t\terror_code stat_error;\n"
    "\t\t\tstat_file(fs.file_path(file, m_save_path), &st, stat_error);\n"
    "\t\t\tif (stat_error || st.file_size < slice.offset + slice.size)\n"
    "\t\t\t\tuse_partfile(file, true);\n"
    "\t\t}\n"
    "\t}\n")

# Refine interrupted-check reconstruction. A cleared mixed piece becomes an
# auxiliary-recovery candidate only when its skipped backing is actually absent:
# no slot in .parts, or an old-style/.unwanted file missing/short for the slice.
replace_once(
    "src/pread_storage.cpp",
    "\t\t\tbool has_wanted = false;\n"
    "\t\t\tbool has_skipped = false;\n"
    "\t\t\tbool wanted_backing_complete = true;\n",
    "\t\t\tbool has_wanted = false;\n"
    "\t\t\tbool auxiliary_backing_missing = false;\n"
    "\t\t\tbool wanted_backing_complete = true;\n")

replace_once(
    "src/pread_storage.cpp",
    "\t\t\t\tif (skipped)\n"
    "\t\t\t\t{\n"
    "\t\t\t\t\thas_skipped = true;\n"
    "\t\t\t\t\tcontinue;\n"
    "\t\t\t\t}\n",
    "\t\t\t\tif (skipped)\n"
    "\t\t\t\t{\n"
    "\t\t\t\t\tif (use_partfile(file))\n"
    "\t\t\t\t\t{\n"
    "\t\t\t\t\t\tif (!m_part_file || !m_part_file->has_piece(piece))\n"
    "\t\t\t\t\t\t\tauxiliary_backing_missing = true;\n"
    "\t\t\t\t\t}\n"
    "\t\t\t\t\telse\n"
    "\t\t\t\t\t{\n"
    "\t\t\t\t\t\terror_code stat_error;\n"
    "\t\t\t\t\t\tauto const actual_size = m_stat_cache.get_filesize(file, fs, m_save_path, stat_error);\n"
    "\t\t\t\t\t\tif (stat_error || actual_size < slice.offset + slice.size)\n"
    "\t\t\t\t\t\t\tauxiliary_backing_missing = true;\n"
    "\t\t\t\t\t}\n"
    "\t\t\t\t\tcontinue;\n"
    "\t\t\t\t}\n")

replace_once(
    "src/pread_storage.cpp",
    "\t\t\t// The missing auxiliary bytes may have lived in .parts or in an\n"
    "\t\t\t// old-style/qBittorrent .unwanted file. If wanted backing is still\n"
    "\t\t\t// physically complete, recover only the priority-0 ranges into .parts.\n"
    "\t\t\tif (has_wanted && has_skipped && wanted_backing_complete)\n"
    "\t\t\t\tset_partfile_repair(piece, true);\n",
    "\t\t\t// Protect only when the missing piece can actually be explained by\n"
    "\t\t\t// absent auxiliary backing. This avoids blaming peers for a recovery\n"
    "\t\t\t// hash that failed because wanted bytes were genuinely corrupt.\n"
    "\t\t\tif (has_wanted && auxiliary_backing_missing && wanted_backing_complete)\n"
    "\t\t\t\tset_partfile_repair(piece, true);\n")
