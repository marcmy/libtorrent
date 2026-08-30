/*

Copyright (c) 2026
All rights reserved.

You may use, distribute and modify this code under the terms of the BSD license,
see LICENSE file.
*/

#include <atomic>
#include <chrono>
#include <fstream>
#include <iterator>
#include <thread>
#include <vector>

#include "libtorrent/aux_/path.hpp"
#include "libtorrent/alert_types.hpp"
#include "libtorrent/download_priority.hpp"
#include "libtorrent/session.hpp"
#include "libtorrent/settings_pack.hpp"
#include "libtorrent/torrent_flags.hpp"
#include "libtorrent/torrent_handle.hpp"

#include "settings.hpp"
#include "setup_transfer.hpp"
#include "test.hpp"
#include "test_utils.hpp"

using namespace lt;
using namespace std::chrono_literals;

TORRENT_TEST(selective_recheck_missing_seed_file)
{
	char const* const dir = "tmp_selective_recheck";
	error_code ec;
	remove_all(dir, ec);
	create_directory(dir, ec);
	TEST_CHECK(!ec);

	std::string const file_path = combine_path(dir, "temporary");
	std::ofstream file(file_path.c_str(), std::ios::binary);
	TEST_CHECK(file.good());

	auto p = ::create_torrent(&file, "temporary", 16 * 1024, 4, false
		, create_torrent::v1_only);
	file.close();

	std::ifstream input(file_path.c_str(), std::ios::binary);
	std::vector<char> const original_data{
		std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
	TEST_CHECK(!original_data.empty());
	input.close();

	settings_pack pack = settings();
	pack.set_str(settings_pack::listen_interfaces, test_listen_interface());
	pack.set_int(settings_pack::max_retry_port_bind, 10);
	pack.set_bool(settings_pack::enable_dht, false);
	pack.set_bool(settings_pack::enable_lsd, false);
	pack.set_bool(settings_pack::enable_upnp, false);
	pack.set_bool(settings_pack::enable_natpmp, false);
	lt::session ses(pack);

	p.save_path = dir;
	// Match qBittorrent's manual test: the completed torrent is paused.
	p.flags |= torrent_flags::paused;
	p.flags &= ~torrent_flags::auto_managed;
	p.flags |= torrent_flags::seed_mode;
	torrent_handle h = ses.add_torrent(std::move(p), ec);
	TEST_CHECK(!ec);
	TEST_CHECK(h.is_valid());
	TEST_CHECK(h.have_piece(0_piece));

	// Force the backing file to be opened first. On Windows an already-open
	// handle can remain readable after the path is moved/deleted, which used to
	// let selective recheck hash stale storage and incorrectly keep the piece.
	h.read_piece(0_piece);
	alert const* const a = wait_for_alert(ses, read_piece_alert::alert_type, "ses");
	TEST_CHECK(a);
	if (a)
	{
		read_piece_alert const* const rp = alert_cast<read_piece_alert>(a);
		TEST_CHECK(rp);
		if (rp) TEST_CHECK(!rp->error);
	}

	remove(file_path, ec);
	TEST_CHECK(!ec);

	std::atomic<int> progress_completed{-2};
	std::atomic<int> progress_total{-2};
	auto const on_progress = [&progress_completed, &progress_total](int const completed, int const total)
	{
		progress_completed.store(completed);
		progress_total.store(total);
	};

	h.recheck_files({0_file}, on_progress);

	bool invalidated = false;
	for (int i = 0; i < 100; ++i)
	{
		if (!h.have_piece(0_piece))
		{
			invalidated = true;
			break;
		}
		std::this_thread::sleep_for(50ms);
	}

	TEST_CHECK(invalidated);
	for (int i = 0; i < 100 && progress_completed.load() != progress_total.load(); ++i)
		std::this_thread::sleep_for(10ms);
	TEST_CHECK(progress_total.load() > 0);
	TEST_EQUAL(progress_completed.load(), progress_total.load());
	TEST_CHECK(h.is_valid());

	// Keep the file skipped while it is absent. This makes storage route it to
	// the part file, reproducing qBittorrent's unchecked-file case.
	h.file_priority(0_file, dont_download);
	h.flush_cache();
	alert const* const flushed = wait_for_alert(ses, cache_flushed_alert::alert_type, "ses");
	TEST_CHECK(flushed);
	TEST_EQUAL(h.file_priority(0_file), dont_download);

	// Restore the exact bytes under a different filename and remap the missing
	// torrent file to it. This matches qBittorrent's Content rename/remap flow:
	// the original path remains absent while the renamed target already exists.
	std::string const replacement_name = "replacement";
	std::string const replacement_path = combine_path(dir, replacement_name);
	std::ofstream restored(replacement_path.c_str(), std::ios::binary);
	TEST_CHECK(restored.good());
	restored.write(original_data.data(), static_cast<std::streamsize>(original_data.size()));
	restored.close();

	h.rename_file(0_file, replacement_name);
	alert const* const renamed = wait_for_alert(ses, file_renamed_alert::alert_type, "ses");
	TEST_CHECK(renamed);
	TEST_CHECK(!exists(file_path, ec));
	TEST_CHECK(!ec);
	TEST_CHECK(exists(replacement_path, ec));
	TEST_CHECK(!ec);
	TEST_EQUAL(h.file_priority(0_file), dont_download);

	progress_completed.store(-2);
	progress_total.store(-2);
	h.recheck_files({0_file}, on_progress);

	bool repromoted = false;
	for (int i = 0; i < 100; ++i)
	{
		if (h.have_piece(0_piece))
		{
			repromoted = true;
			break;
		}
		std::this_thread::sleep_for(50ms);
	}

	TEST_CHECK(repromoted);
	for (int i = 0; i < 100 && progress_completed.load() != progress_total.load(); ++i)
		std::this_thread::sleep_for(10ms);
	TEST_CHECK(progress_total.load() > 0);
	TEST_EQUAL(progress_completed.load(), progress_total.load());
	TEST_CHECK(h.is_valid());
	TEST_EQUAL(h.file_priority(0_file), dont_download);

	remove_all(dir, ec);
}
