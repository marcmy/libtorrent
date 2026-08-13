/*

Copyright (c) 2026, marcmy
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the author nor the names of its
      contributors may be used to endorse or promote products derived
      from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

*/

#define main checking_benchmark_phase3_main
#include "checking_benchmark_phase3.cpp"
#undef main

#include <filesystem>
#include <fstream>
#include <thread>

namespace
{
    namespace fs = std::filesystem;

    struct phase4_boundary
    {
        lt::piece_index_t piece;
        std::int64_t start;
        int size;
        int skipped_file;
        int wanted_file;
    };

    phase4_boundary find_phase4_boundary(lt::torrent_info const& ti)
    {
        auto const& files = ti.layout();
        if (files.num_files() < 5)
            throw std::runtime_error("phase4 requires the five-file WOF fixture");

        // The diagnostic fixture deliberately makes file 3 (medium-wof.bin)
        // end in the middle of a piece, immediately before wanted file 4.
        int const skipped = 3;
        int const wanted = 4;
        if (files.pad_file_at(lt::file_index_t{skipped})
            || files.pad_file_at(lt::file_index_t{wanted}))
            throw std::runtime_error("phase4 boundary unexpectedly contains a pad file");

        std::int64_t const boundary = files.file_offset(lt::file_index_t{wanted});
        int const piece_length = ti.piece_length();
        int const piece_number = static_cast<int>(boundary / piece_length);
        std::int64_t const piece_start = std::int64_t(piece_number) * piece_length;
        int const piece_size = ti.piece_size(lt::piece_index_t{piece_number});
        std::int64_t const piece_end = piece_start + piece_size;

        std::int64_t const skipped_start = files.file_offset(lt::file_index_t{skipped});
        std::int64_t const skipped_end = skipped_start + files.file_size(lt::file_index_t{skipped});
        std::int64_t const wanted_start = files.file_offset(lt::file_index_t{wanted});
        std::int64_t const wanted_end = wanted_start + files.file_size(lt::file_index_t{wanted});

        if (!(piece_start < skipped_end && skipped_start < piece_end
            && piece_start < wanted_end && wanted_start < piece_end))
            throw std::runtime_error("phase4 selected piece does not cross the WOF/plain boundary");

        return {lt::piece_index_t{piece_number}, piece_start, piece_size, skipped, wanted};
    }

    fs::path payload_path(lt::file_storage const& files, int const index
        , std::string const& save_path)
    {
        return fs::path(files.file_path(lt::file_index_t{index}, save_path));
    }

    std::vector<char> read_piece_bytes(lt::file_storage const& files
        , phase4_boundary const& boundary, std::string const& save_path)
    {
        std::vector<char> data(static_cast<std::size_t>(boundary.size), 0);
        std::int64_t const piece_end = boundary.start + boundary.size;

        for (int i = 0; i < files.num_files(); ++i)
        {
            lt::file_index_t const index{i};
            std::int64_t const file_start = files.file_offset(index);
            std::int64_t const file_end = file_start + files.file_size(index);
            std::int64_t const begin = std::max(boundary.start, file_start);
            std::int64_t const end = std::min(piece_end, file_end);
            if (begin >= end) continue;
            if (files.pad_file_at(index)) continue;

            fs::path const path = payload_path(files, i, save_path);
            std::ifstream in(path, std::ios::binary);
            if (!in)
                throw std::runtime_error("phase4 failed to open payload for reading: " + path.string());

            std::int64_t const file_offset = begin - file_start;
            std::int64_t const buffer_offset = begin - boundary.start;
            std::int64_t const bytes = end - begin;
            in.seekg(static_cast<std::streamoff>(file_offset));
            in.read(data.data() + buffer_offset, static_cast<std::streamsize>(bytes));
            if (in.gcount() != static_cast<std::streamsize>(bytes))
                throw std::runtime_error("phase4 short read from payload: " + path.string());
        }

        return data;
    }

    void zero_piece_segment(lt::file_storage const& files, int const file_index
        , phase4_boundary const& boundary, std::string const& save_path)
    {
        lt::file_index_t const index{file_index};
        std::int64_t const file_start = files.file_offset(index);
        std::int64_t const file_end = file_start + files.file_size(index);
        std::int64_t const piece_end = boundary.start + boundary.size;
        std::int64_t const begin = std::max(boundary.start, file_start);
        std::int64_t const end = std::min(piece_end, file_end);
        if (begin >= end)
            throw std::runtime_error("phase4 selected file does not intersect boundary piece");

        fs::path const path = payload_path(files, file_index, save_path);
        std::fstream io(path, std::ios::binary | std::ios::in | std::ios::out);
        if (!io)
            throw std::runtime_error("phase4 failed to open payload for corruption: " + path.string());

        std::vector<char> zeros(static_cast<std::size_t>(end - begin), 0);
        io.seekp(static_cast<std::streamoff>(begin - file_start));
        io.write(zeros.data(), static_cast<std::streamsize>(zeros.size()));
        io.flush();
        if (!io)
            throw std::runtime_error("phase4 failed to corrupt payload segment: " + path.string());
    }

    bool file_segment_matches(lt::file_storage const& files, int const file_index
        , phase4_boundary const& boundary, std::vector<char> const& original
        , std::string const& save_path)
    {
        lt::file_index_t const index{file_index};
        std::int64_t const file_start = files.file_offset(index);
        std::int64_t const file_end = file_start + files.file_size(index);
        std::int64_t const piece_end = boundary.start + boundary.size;
        std::int64_t const begin = std::max(boundary.start, file_start);
        std::int64_t const end = std::min(piece_end, file_end);
        if (begin >= end) return false;

        fs::path const path = payload_path(files, file_index, save_path);
        std::ifstream in(path, std::ios::binary);
        if (!in) return false;

        std::vector<char> actual(static_cast<std::size_t>(end - begin));
        in.seekg(static_cast<std::streamoff>(begin - file_start));
        in.read(actual.data(), static_cast<std::streamsize>(actual.size()));
        if (in.gcount() != static_cast<std::streamsize>(actual.size())) return false;

        auto const expected = original.begin() + (begin - boundary.start);
        return std::equal(actual.begin(), actual.end(), expected);
    }

    std::uintmax_t partfile_bytes(fs::path const& directory)
    {
        std::error_code ec;
        if (!fs::exists(directory, ec)) return 0;

        std::uintmax_t total = 0;
        for (fs::recursive_directory_iterator it(directory, ec), end; it != end && !ec; it.increment(ec))
        {
            if (it->is_regular_file(ec)) total += it->file_size(ec);
            ec.clear();
        }
        return total;
    }

    void wait_for_piece(lt::session& session, lt::torrent_handle const& handle
        , lt::piece_index_t const piece, int const timeout_seconds)
    {
        auto const deadline = std::chrono::steady_clock::now() + std::chrono::seconds(timeout_seconds);
        while (!handle.have_piece(piece))
        {
            session.wait_for_alert(100ms);
            std::vector<lt::alert*> alerts;
            session.pop_alerts(&alerts);
            for (lt::alert const* const alert : alerts) report_torrent_error(alert);

            if (std::chrono::steady_clock::now() >= deadline)
                throw std::runtime_error("phase4 timed out waiting for injected boundary piece");
        }
    }

    void wait_for_restored_segment(lt::file_storage const& files, int const file_index
        , phase4_boundary const& boundary, std::vector<char> const& original
        , std::string const& save_path, int const timeout_seconds)
    {
        auto const deadline = std::chrono::steady_clock::now() + std::chrono::seconds(timeout_seconds);
        while (!file_segment_matches(files, file_index, boundary, original, save_path))
        {
            if (std::chrono::steady_clock::now() >= deadline)
                throw std::runtime_error("phase4 partfile data was not restored to the skipped payload file");
            std::this_thread::sleep_for(50ms);
        }
    }

#ifdef _WIN32
    void apply_lzx(fs::path const& path)
    {
        std::wstring const command = L"compact.exe /C /F /EXE:LZX \"" + path.wstring() + L"\"";
        std::wcout << L"PHASE4_RECOMPRESS,file=" << path.wstring() << L'\n';
        int const result = _wsystem(command.c_str());
        if (result != 0)
            throw std::runtime_error("phase4 failed to re-apply WOF/LZX compression");
    }
#endif

    void run_phase4(options const& opts, lt::add_torrent_params const& base)
    {
#ifndef _WIN32
        std::cout << "PHASE4_SKIP,reason=windows-only-WOF-test\n";
        (void)opts;
        (void)base;
#else
        auto const& files = base.ti->layout();
        phase4_boundary const boundary = find_phase4_boundary(*base.ti);
        std::vector<char> const original = read_piece_bytes(files, boundary, opts.save_path);
        fs::path const part_dir = fs::path(opts.save_path) / ".phase4-partfile";
        std::error_code ec;
        fs::remove_all(part_dir, ec);

        std::cout << "=== PHASE4 real incomplete -> priority 0 -> partfile -> restore ===\n"
            << "PHASE4_BOUNDARY,piece=" << static_cast<int>(boundary.piece)
            << ",piece_bytes=" << boundary.size
            << ",skipped_file=" << boundary.skipped_file
            << ",wanted_file=" << boundary.wanted_file << '\n';

        // Invalidate both sides of one cross-file piece. The WOF file selected
        // here is the ~50 MiB member, avoiding multi-GiB hydration just to set
        // up the test while still exercising WOF on the restored recheck.
        zero_piece_segment(files, boundary.skipped_file, boundary, opts.save_path);
        zero_piece_segment(files, boundary.wanted_file, boundary, opts.save_path);

        {
            lt::add_torrent_params params = base;
            params.file_priorities.assign(
                static_cast<std::size_t>(files.num_files()), lt::default_priority);
            params.file_priorities[static_cast<std::size_t>(boundary.skipped_file)] = lt::dont_download;
            params.part_file_dir = ".phase4-partfile";

            lt::session session(phase3_session_params(opts));
            lt::torrent_handle const handle = session.add_torrent(params);
            wait_for_check(session, handle, opts.alert_timeout_seconds);
            wait_for_priorities(handle, {boundary.skipped_file}, lt::dont_download
                , opts.alert_timeout_seconds);

            lt::torrent_status const damaged = handle.status();
            std::cout << "PHASE4_INCOMPLETE,progress_ppm=" << damaged.progress_ppm
                << ",is_seeding=" << (damaged.is_seeding ? "yes" : "no")
                << ",have_boundary_piece=" << (handle.have_piece(boundary.piece) ? "yes" : "no") << '\n';
            if (handle.have_piece(boundary.piece))
                throw std::runtime_error("phase4 corruption did not make the boundary piece incomplete");

            handle.add_piece(boundary.piece, original, lt::torrent_handle::overwrite_existing);
            wait_for_piece(session, handle, boundary.piece, opts.alert_timeout_seconds);
            capture_resume(handle); // flush the disk cache before inspecting the partfile

            std::uintmax_t const stored = partfile_bytes(part_dir);
            std::cout << "PHASE4_PARTFILE,bytes=" << stored << '\n';
            if (stored == 0)
                throw std::runtime_error("phase4 injected piece did not create partfile storage");

            handle.file_priority(lt::file_index_t{boundary.skipped_file}, lt::default_priority);
            wait_for_priorities(handle, {boundary.skipped_file}, lt::default_priority
                , opts.alert_timeout_seconds);

            handle.force_recheck();
            wait_for_check(session, handle, opts.alert_timeout_seconds);
            verify_complete(handle, 401);
            capture_resume(handle); // ensure exported partfile bytes reach the payload file
        }

        wait_for_restored_segment(files, boundary.skipped_file, boundary, original
            , opts.save_path, opts.alert_timeout_seconds);
        std::cout << "PHASE4_RESTORED,file=" << boundary.skipped_file << "\n";

        // The setup write necessarily hydrated this small WOF member. Restore
        // the real test condition before the final full-payload force recheck.
        fs::path const restored_wof = payload_path(files, boundary.skipped_file, opts.save_path);
        apply_lzx(restored_wof);

        {
            lt::add_torrent_params params = base;
            params.file_priorities.assign(
                static_cast<std::size_t>(files.num_files()), lt::default_priority);
            params.part_file_dir.clear();

            lt::session session(phase3_session_params(opts));
            lt::torrent_handle const handle = session.add_torrent(params);
            wait_for_check(session, handle, opts.alert_timeout_seconds);
            verify_complete(handle, 402);
            handle.force_recheck();
            wait_for_check(session, handle, opts.alert_timeout_seconds);
            verify_complete(handle, 403);
        }

        std::cout << "PHASE4_COMPLETE,backend=" << opts.backend << "\n";
#endif
    }
}

int main(int const argc, char const* const argv[])
{
    int const phase3_result = checking_benchmark_phase3_main(argc, argv);
    if (phase3_result != 0) return phase3_result;

    try
    {
        options const opts = parse_options(argc, argv);
        lt::add_torrent_params atp = lt::load_torrent_file(opts.torrent_file);
        if (!atp.ti || !atp.ti->is_valid())
            throw std::runtime_error("phase4 torrent metadata is invalid");

        atp.save_path = opts.save_path;
        atp.flags &= ~(lt::torrent_flags::paused | lt::torrent_flags::auto_managed);
        run_phase4(opts, atp);
        return 0;
    }
    catch (std::exception const& e)
    {
        std::cerr << "checking_benchmark phase4: " << e.what() << '\n';
        return 1;
    }
}
