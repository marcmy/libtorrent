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

#define main checking_benchmark_steady_main
#include "checking_benchmark.cpp"
#undef main

#include <thread>

#include "libtorrent/download_priority.hpp"

namespace
{
    lt::session_params phase3_session_params(options const& opts)
    {
        lt::session_params params;
        params.disk_io_constructor = disk_constructor(opts.backend);

        auto& settings = params.settings;
        settings.set_bool(lt::settings_pack::enable_dht, false);
        settings.set_bool(lt::settings_pack::enable_upnp, false);
        settings.set_bool(lt::settings_pack::enable_natpmp, false);
        settings.set_bool(lt::settings_pack::enable_lsd, false);
        settings.set_int(lt::settings_pack::hashing_threads, opts.hash_threads);
        settings.set_int(lt::settings_pack::aio_threads, opts.aio_threads);
        settings.set_int(lt::settings_pack::checking_mem_usage, opts.checking_mib * 64);
        settings.set_int(lt::settings_pack::active_checking, 1);
        settings.set_int(lt::settings_pack::alert_mask
            , lt::alert_category::error
            | lt::alert_category::storage
            | lt::alert_category::status);
        settings.set_str(lt::settings_pack::listen_interfaces, "");
        return params;
    }

    void wait_for_priorities(lt::torrent_handle const& handle
        , std::vector<int> const& indices, lt::download_priority_t const expected
        , int const timeout_seconds)
    {
        auto const deadline = std::chrono::steady_clock::now()
            + std::chrono::seconds(timeout_seconds);

        for (;;)
        {
            auto const priorities = handle.get_file_priorities();
            bool ready = true;
            for (int const index : indices)
            {
                if (index < 0 || index >= static_cast<int>(priorities.size())
                    || priorities[static_cast<std::size_t>(index)] != expected)
                {
                    ready = false;
                    break;
                }
            }

            if (ready) return;
            if (std::chrono::steady_clock::now() >= deadline)
                throw std::runtime_error("phase3 timed out waiting for file priorities");
            std::this_thread::sleep_for(20ms);
        }
    }

    std::vector<int> phase3_priority_files(lt::file_storage const& files)
    {
        std::vector<int> ret;
        if (files.num_files() > 1 && !files.pad_file_at(lt::file_index_t{1})) ret.push_back(1);
        if (files.num_files() > 3 && !files.pad_file_at(lt::file_index_t{3})) ret.push_back(3);

        if (ret.empty())
        {
            for (int index = 0; index < files.num_files(); ++index)
            {
                if (!files.pad_file_at(lt::file_index_t{index}))
                {
                    ret.push_back(index);
                    break;
                }
            }
        }
        return ret;
    }

    lt::add_torrent_params capture_resume(lt::torrent_handle const& handle)
    {
        return handle.get_resume_data(
            lt::torrent_handle::flush_disk_cache | lt::torrent_handle::save_info_dict);
    }

    void run_phase3(options const& opts, lt::add_torrent_params const& base)
    {
        auto const& files = base.ti->layout();
        if (files.num_files() <= 1) return;

        std::cout << "=== PHASE3 establish resume state ===\n";
        lt::add_torrent_params resume;
        {
            lt::session session(phase3_session_params(opts));
            lt::torrent_handle const handle = session.add_torrent(base);
            wait_for_check(session, handle, opts.alert_timeout_seconds);
            verify_complete(handle, 300);
            resume = capture_resume(handle);
        }

        std::cout << "=== PHASE3 resume/re-add force recheck ===\n";
        for (int cycle = 1; cycle <= 3; ++cycle)
        {
            lt::session session(phase3_session_params(opts));
            resume.save_path = opts.save_path;
            resume.flags &= ~(lt::torrent_flags::paused | lt::torrent_flags::auto_managed);

            lt::torrent_handle const handle = session.add_torrent(resume);
            lt::torrent_status const opened = handle.status();
            std::cout << "PHASE3_RESUME_OPEN,cycle=" << cycle
                << ",progress_ppm=" << opened.progress_ppm
                << ",is_seeding=" << (opened.is_seeding ? "yes" : "no") << '\n';

            handle.force_recheck();
            wait_for_check(session, handle, opts.alert_timeout_seconds);
            verify_complete(handle, 310 + cycle);
            resume = capture_resume(handle);
        }

        std::vector<int> const indices = phase3_priority_files(files);
        if (indices.empty())
            throw std::runtime_error("phase3 could not select files for priority cycle");

        std::cout << "=== PHASE3 priority 0 -> normal force recheck ===\n";
        for (int cycle = 1; cycle <= 2; ++cycle)
        {
            lt::add_torrent_params params = base;
            params.file_priorities.assign(
                static_cast<std::size_t>(files.num_files()), lt::default_priority);
            for (int const index : indices)
                params.file_priorities[static_cast<std::size_t>(index)] = lt::dont_download;
            params.part_file_dir = ".phase3-partfile-" + std::to_string(cycle);

            lt::session session(phase3_session_params(opts));
            lt::torrent_handle const handle = session.add_torrent(params);
            wait_for_check(session, handle, opts.alert_timeout_seconds);
            wait_for_priorities(handle, indices, lt::dont_download, opts.alert_timeout_seconds);

            lt::torrent_status const skipped = handle.status();
            std::cout << "PHASE3_PRIORITY_ZERO,cycle=" << cycle
                << ",progress_ppm=" << skipped.progress_ppm
                << ",is_seeding=" << (skipped.is_seeding ? "yes" : "no")
                << ",files=";
            for (std::size_t i = 0; i < indices.size(); ++i)
                std::cout << (i == 0 ? "" : "+") << indices[i];
            std::cout << '\n';

            for (int const index : indices)
                handle.file_priority(lt::file_index_t{index}, lt::default_priority);
            wait_for_priorities(handle, indices, lt::default_priority, opts.alert_timeout_seconds);

            handle.force_recheck();
            wait_for_check(session, handle, opts.alert_timeout_seconds);
            verify_complete(handle, 320 + cycle);
        }
    }
}

int main(int const argc, char const* const argv[])
{
    int const steady_result = checking_benchmark_steady_main(argc, argv);
    if (steady_result != 0) return steady_result;

    try
    {
        options const opts = parse_options(argc, argv);
        lt::add_torrent_params atp = lt::load_torrent_file(opts.torrent_file);
        if (!atp.ti || !atp.ti->is_valid())
            throw std::runtime_error("phase3 torrent metadata is invalid");

        atp.save_path = opts.save_path;
        atp.flags &= ~(lt::torrent_flags::paused | lt::torrent_flags::auto_managed);
        run_phase3(opts, atp);
        return 0;
    }
    catch (std::exception const& e)
    {
        std::cerr << "checking_benchmark phase3: " << e.what() << '\n';
        return 1;
    }
}
