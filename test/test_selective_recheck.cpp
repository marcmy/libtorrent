/*

Copyright (c) 2026
All rights reserved.

You may use, distribute and modify this code under the terms of the BSD license,
see LICENSE file.
*/

#include <chrono>
#include <fstream>
#include <thread>

#include "libtorrent/aux_/path.hpp"
#include "libtorrent/session.hpp"
#include "libtorrent/settings_pack.hpp"
#include "libtorrent/torrent_flags.hpp"
#include "libtorrent/torrent_handle.hpp"

#include "settings.hpp"
#include "setup_transfer.hpp"
#include "test.hpp"

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

	settings_pack pack = settings();
	pack.set_str(settings_pack::listen_interfaces, test_listen_interface());
	pack.set_int(settings_pack::max_retry_port_bind, 10);
	pack.set_bool(settings_pack::enable_dht, false);
	pack.set_bool(settings_pack::enable_lsd, false);
	pack.set_bool(settings_pack::enable_upnp, false);
	pack.set_bool(settings_pack::enable_natpmp, false);
	lt::session ses(pack);

	p.save_path = dir;
	p.flags &= ~torrent_flags::paused;
	p.flags &= ~torrent_flags::auto_managed;
	p.flags |= torrent_flags::seed_mode;
	torrent_handle h = ses.add_torrent(std::move(p), ec);
	TEST_CHECK(!ec);
	TEST_CHECK(h.is_valid());
	TEST_CHECK(h.have_piece(0_piece));

	remove(file_path, ec);
	TEST_CHECK(!ec);

	h.recheck_files({0_file});

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
	TEST_CHECK(h.is_valid());

	remove_all(dir, ec);
}
