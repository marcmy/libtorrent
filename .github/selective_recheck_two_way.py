from pathlib import Path

source = Path("src/torrent_handle.cpp")
text = source.read_text(encoding="utf-8")
start_marker = "\tvoid aux::torrent::recheck_files(std::vector<file_index_t> files)\n\t{"
end_marker = "\n\tvoid torrent_handle::resume() const\n"
start = text.index(start_marker)
end = text.index(end_marker, start)
new_function = r'''	void aux::torrent::recheck_files(std::vector<file_index_t> files)
	{
		TORRENT_ASSERT(is_single_thread());

		if (m_abort || m_deleted || !valid_metadata() || !m_storage)
			return;

		if (state() == torrent_status::checking_files
			|| state() == torrent_status::checking_resume_data)
			return;

		std::vector<piece_index_t> pieces;
		file_storage const& fs = torrent_file().layout();
		for (file_index_t const file : files)
		{
			if (file < file_index_t{0} || file >= fs.end_file()) continue;
			if (fs.pad_file_at(file) || fs.file_size(file) == 0) continue;

			piece_index_t const first = torrent_file().map_file(file, 0, 1).piece;
			piece_index_t const last = torrent_file().map_file(
				file, fs.file_size(file) - 1, 1).piece;

			for (piece_index_t p = first; p <= last; ++p)
				pieces.push_back(p);
		}

		std::sort(pieces.begin(), pieces.end());
		pieces.erase(std::unique(pieces.begin(), pieces.end()), pieces.end());
		if (pieces.empty()) return;

		disk_job_flags_t flags = disk_interface::sequential_access
			| disk_interface::volatile_read | disk_interface::flush_piece;
		if (torrent_file().info_hashes().has_v1() && !m_disable_v1_hashes)
			flags |= disk_interface::v1_hash;

		// Reopen files by path before hashing. Cached file handles may remain
		// valid after a file is moved or replaced externally, notably on Windows.
		m_ses.disk_thread().async_release_files(m_storage);

		struct recheck_state
		{
			std::vector<piece_index_t> pieces;
			std::size_t next_piece;
			disk_job_flags_t flags;
			std::uint8_t generation;
		};

		auto const state = std::make_shared<recheck_state>(recheck_state{
			std::move(pieces), 0, flags, m_picker_generation});
		auto const schedule = std::make_shared<std::function<void()>>();
		std::weak_ptr<std::function<void()>> const weak_schedule = schedule;

		*schedule = [self = shared_from_this(), state, weak_schedule]
		{
			if (self->m_abort || self->m_deleted
				|| state->generation != self->m_picker_generation
				|| state->next_piece >= state->pieces.size())
				return;

			piece_index_t const piece = state->pieces[state->next_piece++];
			aux::vector<sha256_hash> hashes;
			if (self->torrent_file().info_hashes().has_v2())
				hashes.resize(self->torrent_file().layout().blocks_in_piece2(piece));

			auto const keep_alive = weak_schedule.lock();
			if (!keep_alive) return;

			span<sha256_hash> v2_span(hashes);
			self->m_ses.disk_thread().async_hash(self->m_storage, piece, v2_span, state->flags,
				[self, state, keep_alive, hashes1 = std::move(hashes)](
					piece_index_t const p, sha1_hash const& h,
					storage_error const& error) mutable
				{
					if (self->m_abort || self->m_deleted
						|| state->generation != self->m_picker_generation)
						return;

					if (error)
					{
						if (error.ec == boost::system::errc::no_such_file_or_directory
							|| error.ec == boost::asio::error::eof
							|| error.ec == lt::errors::file_too_short
#ifdef TORRENT_WINDOWS
							|| error.ec == error_code(ERROR_HANDLE_EOF, system_category())
#endif
							)
							self->partfile_read_failed(p);
						else
							self->handle_disk_error("selective_recheck", error);

						(*keep_alive)();
						return;
					}

					if (self->settings().get_bool(settings_pack::disable_hash_checks))
					{
						(*keep_alive)();
						return;
					}

					boost::tribool v1_passed = boost::indeterminate;
					boost::tribool v2_passed = boost::indeterminate;
					if (self->torrent_file().info_hashes().has_v1()
						&& !self->m_disable_v1_hashes)
						v1_passed = h == self->torrent_file().hash_for_piece(p);

					if (self->torrent_file().info_hashes().has_v2()
						&& !bool(v1_passed == false))
						v2_passed = self->on_blocks_hashed(p, hashes1);

					if ((v1_passed && !v2_passed) || (!v1_passed && v2_passed))
					{
						self->handle_inconsistent_hashes(p);
						return;
					}

					if (bool(v1_passed == false) || bool(v2_passed == false))
					{
						self->partfile_read_failed(p);
					}
					else if ((bool(v1_passed == true) || bool(v2_passed == true))
						&& !self->have_piece(p))
					{
						bool const was_finished = self->is_finished();
						self->need_picker();
						if (!self->m_picker->have_piece(p))
						{
#if TORRENT_USE_INVARIANT_CHECKS
							TORRENT_ASSERT(!self->m_file_progress.have_piece(p));
#endif
							// This piece already exists on disk and just passed its hash.
							// piece_flushed() is the supported open -> owned transition.
							self->m_picker->piece_flushed(p);
							self->update_gauge();
							self->we_have(p);
							self->update_peer_interest(was_finished);
							self->update_want_peers();
						}
					}

					(*keep_alive)();
				});
			self->m_ses.deferred_submit_jobs();
		};

		// Keep selective checks bounded. Large file/folder selections should not
		// enqueue every piece hash (and its v2 hash buffer) simultaneously.
		constexpr std::size_t max_in_flight = 4;
		std::size_t const initial = std::min(max_in_flight, state->pieces.size());
		for (std::size_t i = 0; i < initial; ++i)
			(*schedule)();
	}
'''
source.write_text(text[:start] + new_function + text[end:], encoding="utf-8")

header = Path("include/libtorrent/torrent_handle.hpp")
text = header.read_text(encoding="utf-8")
old = '''\t\t// Re-hash only pieces overlapping the specified files. Pieces that are
\t\t// currently missing are skipped. Pieces that pass remain untouched while
\t\t// pieces that fail are marked missing and become eligible for download.
'''
new = '''\t\t// Re-hash only pieces overlapping the specified files. Owned pieces that
\t\t// fail are marked missing and become eligible for download. Missing pieces
\t\t// that pass are restored as owned, allowing externally restored files to
\t\t// be recovered without a full-torrent recheck.
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one public recheck_files documentation block, found {text.count(old)}")
header.write_text(text.replace(old, new), encoding="utf-8")

test = Path("test/test_selective_recheck.cpp")
text = test.read_text(encoding="utf-8")
include_old = '#include <chrono>\n#include <fstream>\n#include <thread>\n'
include_new = '#include <chrono>\n#include <fstream>\n#include <iterator>\n#include <thread>\n#include <vector>\n'
if text.count(include_old) != 1:
    raise SystemExit("unexpected selective recheck test include block")
text = text.replace(include_old, include_new)

capture_old = '''\tauto p = ::create_torrent(&file, "temporary", 16 * 1024, 4, false
\t\t, create_torrent::v1_only);
\tfile.close();

\tsettings_pack pack = settings();
'''
capture_new = '''\tauto p = ::create_torrent(&file, "temporary", 16 * 1024, 4, false
\t\t, create_torrent::v1_only);
\tfile.close();

\tstd::ifstream input(file_path.c_str(), std::ios::binary);
\tstd::vector<char> const original_data{
\t\tstd::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
\tTEST_CHECK(!original_data.empty());
\tinput.close();

\tsettings_pack pack = settings();
'''
if text.count(capture_old) != 1:
    raise SystemExit("unexpected selective recheck fixture block")
text = text.replace(capture_old, capture_new)

end_old = '''\tTEST_CHECK(invalidated);
\tTEST_CHECK(h.is_valid());

\tremove_all(dir, ec);
}
'''
end_new = '''\tTEST_CHECK(invalidated);
\tTEST_CHECK(h.is_valid());

\t// Put the exact bytes back and verify that selective recheck can promote
\t// the missing piece again without requiring a full force_recheck().
\tstd::ofstream restored(file_path.c_str(), std::ios::binary);
\tTEST_CHECK(restored.good());
\trestored.write(original_data.data(), static_cast<std::streamsize>(original_data.size()));
\trestored.close();

\th.recheck_files({0_file});

\tbool repromoted = false;
\tfor (int i = 0; i < 100; ++i)
\t{
\t\tif (h.have_piece(0_piece))
\t\t{
\t\t\trepromoted = true;
\t\t\tbreak;
\t\t}
\t\tstd::this_thread::sleep_for(50ms);
\t}

\tTEST_CHECK(repromoted);
\tTEST_CHECK(h.is_valid());

\tremove_all(dir, ec);
}
'''
if text.count(end_old) != 1:
    raise SystemExit("unexpected selective recheck test tail")
test.write_text(text.replace(end_old, end_new), encoding="utf-8")
