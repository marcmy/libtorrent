/*

Copyright (c) 2026
All rights reserved.

You may use, distribute and modify this code under the terms of the BSD license,
see LICENSE file.
*/

#include "test.hpp"
#include "disk_io_test.hpp"
#include "settings.hpp"
#include "setup_transfer.hpp"

#include "libtorrent/add_torrent_params.hpp"
#include "libtorrent/alert_types.hpp"
#include "libtorrent/aux_/path.hpp"
#include "libtorrent/bencode.hpp"
#include "libtorrent/create_torrent.hpp"
#include "libtorrent/hasher.hpp"
#include "libtorrent/load_torrent.hpp"
#include "libtorrent/read_resume_data.hpp"
#include "libtorrent/session.hpp"
#include "libtorrent/session_params.hpp"
#include "libtorrent/torrent_info.hpp"
#include "libtorrent/torrent_status.hpp"
#include "libtorrent/write_resume_data.hpp"

#include <fstream>
#include <vector>

using namespace lt;

namespace {

void test_unavailable_storage(disk_io_constructor_type const& disk_io
	, bool const blocked_path, bool const seed_mode, bool const force_recheck = false)
{
	std::string const save_path = lt::complete("offline-storage");
	std::string const available_path = lt::complete("online-storage");
	error_code ec;
	lt::create_directory(save_path, ec);
	TEST_CHECK(!ec);

	int const piece_size = 16 * 1024;
	std::vector<char> const data(2 * piece_size, 'x');
	std::ofstream file(lt::combine_path(save_path, "seed.bin"), std::ios::binary);
	file.write(data.data(), data.size());
	file.close();

	std::vector<create_file_entry> files;
	files.emplace_back("seed.bin", data.size());
	lt::create_torrent torrent(std::move(files), piece_size, lt::create_torrent::v1_only);
	sha1_hash const hash = hasher(span<char const>(data.data(), piece_size)).final();
	for (auto const piece : torrent.piece_range()) torrent.set_hash(piece, hash);
	add_torrent_params params = load_torrent_buffer(bencode(torrent.generate()));
	params.save_path = save_path;
	params.have_pieces.resize(params.ti->num_pieces(), true);
	params.verified_pieces = params.have_pieces;
	params.completed_time = 123456;
	params.flags &= ~(torrent_flags::paused | torrent_flags::auto_managed);
	if (seed_mode) params.flags |= torrent_flags::seed_mode;

	// Disconnect the storage without deleting any torrent content. A blocked
	// directory also exercises real access errors rather than just ENOENT.
	lt::rename(save_path, available_path, ec);
	TEST_CHECK(!ec);
	if (blocked_path)
	{
		std::ofstream blocker(save_path);
		blocker << "not a directory";
	}

	session_params sp(settings());
	sp.settings.set_str(settings_pack::listen_interfaces, "127.0.0.1:0");
	sp.disk_io_constructor = disk_io;
	session ses(std::move(sp));
	torrent_handle handle = ses.add_torrent(params);
	auto const* rejected = wait_for_alert(ses, fastresume_rejected_alert::alert_type);
	TEST_CHECK(rejected != nullptr);
	if (!rejected) return;
	auto state = handle.status();
	TEST_CHECK(state.errc);
	TEST_CHECK(state.flags & torrent_flags::paused);
	TEST_CHECK(!(state.flags & torrent_flags::auto_managed));
	TEST_EQUAL(state.state, torrent_status::checking_files);
	TEST_EQUAL(state.total_payload_download, 0);

	handle.save_resume_data(torrent_handle::save_info_dict);
	auto const* saved_alert = alert_cast<save_resume_data_alert>(
		wait_for_alert(ses, save_resume_data_alert::alert_type));
	TEST_CHECK(saved_alert != nullptr);
	if (!saved_alert) return;
	add_torrent_params saved = read_resume_data(write_resume_data_buf(saved_alert->params));
	// Resume format 2 packs bits into whole bytes; read_resume_data() retains
	// padding bits until initialization clamps the map to the torrent size.
	TEST_CHECK(saved.have_pieces.size() >= params.have_pieces.size());
	TEST_CHECK(saved.verified_pieces.size() >= params.verified_pieces.size());
	for (auto const piece : params.have_pieces.range())
	{
		TEST_CHECK(saved.have_pieces[piece]);
		TEST_CHECK(saved.verified_pieces[piece]);
	}
	TEST_EQUAL(saved.completed_time, params.completed_time);

	// Restart while the storage is still offline using the actual serialized
	// resume data. Retrying must neither erase progress nor begin a download.
	ses.remove_torrent(handle);
	TEST_CHECK(wait_for_alert(ses, torrent_removed_alert::alert_type) != nullptr);
	saved.flags &= ~(torrent_flags::paused | torrent_flags::auto_managed);
	handle = ses.add_torrent(saved);
	TEST_CHECK(wait_for_alert(ses, fastresume_rejected_alert::alert_type) != nullptr);
	handle.resume();
	TEST_CHECK(wait_for_alert(ses, fastresume_rejected_alert::alert_type) != nullptr);
	state = handle.status();
	TEST_CHECK(state.errc);
	TEST_CHECK(state.flags & torrent_flags::paused);
	TEST_EQUAL(state.total_payload_download, 0);

	if (blocked_path)
	{
		lt::remove(save_path, ec);
		TEST_CHECK(!ec);
	}
	lt::rename(available_path, save_path, ec);
	TEST_CHECK(!ec);
	if (force_recheck) handle.force_recheck();
	handle.resume();
	wait_for_seeding(ses, "restored storage");
	state = handle.status();
	TEST_CHECK(!state.errc);
	TEST_CHECK(state.is_seeding);
	TEST_EQUAL(state.num_pieces, params.ti->num_pieces());
	TEST_EQUAL(state.total_payload_download, 0);
}

} // anonymous namespace

TORRENT_TEST_DISK_IO(resume_unavailable_directory)
{
	test_unavailable_storage(disk_io, false, false);
}

TORRENT_TEST_DISK_IO(resume_inaccessible_directory)
{
	test_unavailable_storage(disk_io, true, false);
}

TORRENT_TEST_DISK_IO(resume_unavailable_seed_mode)
{
	test_unavailable_storage(disk_io, false, true);
}

TORRENT_TEST_DISK_IO(recheck_reconnected_storage)
{
	test_unavailable_storage(disk_io, false, false, true);
}
