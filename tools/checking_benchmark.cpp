/*

Copyright (c) 2022-2026, Arvid Norberg
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

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "libtorrent/alert_types.hpp"
#include "libtorrent/config.hpp"
#include "libtorrent/load_torrent.hpp"
#include "libtorrent/mmap_disk_io.hpp"
#include "libtorrent/posix_disk_io.hpp"
#include "libtorrent/pread_disk_io.hpp"
#include "libtorrent/session.hpp"
#include "libtorrent/settings_pack.hpp"
#include "libtorrent/torrent_flags.hpp"
#include "libtorrent/torrent_info.hpp"

using namespace std::chrono_literals;

namespace
{
    struct options
    {
        std::string torrent_file;
        std::string save_path;
        std::string backend = "default";
        int hash_threads = 1;
        int aio_threads = 10;
        int checking_mib = 32;
        int runs = 1;
        int alert_timeout_seconds = 30;
    };

    [[noreturn]] void usage(char const* const program, int const exit_code)
    {
        std::ostream& out = exit_code == 0 ? std::cout : std::cerr;
        out
            << "Usage:\n"
            << "  " << program << " --torrent <file.torrent> --save-path <data-root> [options]\n\n"
            << "Options:\n"
            << "  --backend <default|pread|mmap|posix>  Disk I/O backend (default: default)\n"
            << "  --hash-threads <n>                   Hashing threads (default: 1)\n"
            << "  --aio-threads <n>                    General disk I/O threads (default: 10)\n"
            << "  --checking-mib <MiB>                 Outstanding checking memory (default: 32)\n"
            << "  --runs <n>                            Recheck repetitions in one session (default: 1)\n"
            << "  --alert-timeout <seconds>             Alert wait interval (default: 30)\n"
            << "  --help                                Show this help\n\n"
            << "The benchmark uses real torrent data already present under --save-path.\n"
            << "Each run emits one machine-readable line beginning with RESULT,.\n";
        std::exit(exit_code);
    }

    int parse_positive_int(std::string const& value, std::string_view const name)
    {
        std::size_t consumed = 0;
        int const parsed = std::stoi(value, &consumed);
        if (consumed != value.size() || parsed <= 0)
            throw std::invalid_argument(std::string(name) + " must be a positive integer");
        return parsed;
    }

    options parse_options(int const argc, char const* const argv[])
    {
        options ret;

        auto require_value = [&](int& index, std::string_view const name) -> std::string
        {
            if (++index >= argc)
                throw std::invalid_argument(std::string(name) + " requires a value");
            return argv[index];
        };

        for (int i = 1; i < argc; ++i)
        {
            std::string const arg = argv[i];
            if (arg == "--help" || arg == "-h")
                usage(argv[0], 0);
            else if (arg == "--torrent")
                ret.torrent_file = require_value(i, arg);
            else if (arg == "--save-path")
                ret.save_path = require_value(i, arg);
            else if (arg == "--backend")
                ret.backend = require_value(i, arg);
            else if (arg == "--hash-threads")
                ret.hash_threads = parse_positive_int(require_value(i, arg), arg);
            else if (arg == "--aio-threads")
                ret.aio_threads = parse_positive_int(require_value(i, arg), arg);
            else if (arg == "--checking-mib")
                ret.checking_mib = parse_positive_int(require_value(i, arg), arg);
            else if (arg == "--runs")
                ret.runs = parse_positive_int(require_value(i, arg), arg);
            else if (arg == "--alert-timeout")
                ret.alert_timeout_seconds = parse_positive_int(require_value(i, arg), arg);
            else
                throw std::invalid_argument("unknown argument: " + arg);
        }

        if (ret.torrent_file.empty())
            throw std::invalid_argument("--torrent is required");
        if (ret.save_path.empty())
            throw std::invalid_argument("--save-path is required");

        if (ret.backend != "default" && ret.backend != "pread"
            && ret.backend != "mmap" && ret.backend != "posix")
            throw std::invalid_argument("--backend must be default, pread, mmap, or posix");

        return ret;
    }

    lt::disk_io_constructor_type disk_constructor(std::string const& backend)
    {
        if (backend == "pread") return lt::pread_disk_io_constructor;
        if (backend == "posix") return lt::posix_disk_io_constructor;
#if TORRENT_HAVE_MMAP || TORRENT_HAVE_MAP_VIEW_OF_FILE
        if (backend == "mmap") return lt::mmap_disk_io_constructor;
#else
        if (backend == "mmap")
            throw std::runtime_error("mmap backend is not available in this build");
#endif
        return lt::default_disk_io_constructor;
    }

    std::int64_t payload_bytes(lt::torrent_info const& ti)
    {
        std::int64_t total = 0;
        auto const& files = ti.layout();
        for (lt::file_index_t const index : files.file_range())
        {
            if (!files.pad_file_at(index))
                total += files.file_size(index);
        }
        return total;
    }

    void report_torrent_error(lt::alert const* const alert)
    {
        if (auto const* const error = lt::alert_cast<lt::torrent_error_alert>(alert))
            throw std::runtime_error("torrent error: " + error->message());
        if (auto const* const error = lt::alert_cast<lt::file_error_alert>(alert))
            throw std::runtime_error("file error: " + error->message());
    }

    double wait_for_check(lt::session& session, lt::torrent_handle const& handle
        , int const alert_timeout_seconds)
    {
        auto const start = std::chrono::steady_clock::now();

        for (;;)
        {
            session.wait_for_alert(std::chrono::seconds(alert_timeout_seconds));
            std::vector<lt::alert*> alerts;
            session.pop_alerts(&alerts);

            for (lt::alert const* const alert : alerts)
            {
                report_torrent_error(alert);

                if (auto const* const checked = lt::alert_cast<lt::torrent_checked_alert>(alert))
                {
                    if (checked->handle == handle)
                    {
                        auto const end = std::chrono::steady_clock::now();
                        return std::chrono::duration<double>(end - start).count();
                    }
                }
            }
        }
    }

    void verify_complete(lt::torrent_handle const& handle, int const run)
    {
        lt::torrent_status const status = handle.status();
        std::cout << "VERIFY,run=" << run
            << ",progress_ppm=" << status.progress_ppm
            << ",is_seeding=" << (status.is_seeding ? "yes" : "no")
            << ",num_pieces=" << status.num_pieces << '\n';

        if (status.progress_ppm != 1000000 || !status.is_seeding)
            throw std::runtime_error("recheck reported incomplete payload on run "
                + std::to_string(run) + " (progress_ppm="
                + std::to_string(status.progress_ppm) + ")");
    }

    void print_result(options const& opts, int const run, double const seconds
        , std::int64_t const bytes)
    {
        double const mib = static_cast<double>(bytes) / (1024.0 * 1024.0);
        double const gib = mib / 1024.0;
        double const mib_per_second = seconds > 0.0 ? (mib / seconds) : 0.0;

        std::cout << std::fixed << std::setprecision(2)
            << "run " << run << '/' << opts.runs
            << ": " << seconds << " s, " << gib << " GiB, "
            << mib_per_second << " MiB/s\n";

        std::cout << std::setprecision(6)
            << "RESULT," << opts.backend
            << ',' << opts.hash_threads
            << ',' << opts.aio_threads
            << ',' << opts.checking_mib
            << ',' << run
            << ',' << seconds
            << ',' << bytes
            << ',' << mib_per_second
            << '\n';
    }
}

int main(int const argc, char const* const argv[]) try
{
    options const opts = parse_options(argc, argv);

    lt::add_torrent_params atp = lt::load_torrent_file(opts.torrent_file);
    if (!atp.ti || !atp.ti->is_valid())
        throw std::runtime_error("torrent metadata is invalid");

    std::int64_t const bytes = payload_bytes(*atp.ti);
    if (bytes <= 0)
        throw std::runtime_error("torrent contains no payload data to check");

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

    lt::session session(std::move(params));

    atp.save_path = opts.save_path;
    atp.flags &= ~(lt::torrent_flags::paused | lt::torrent_flags::auto_managed);

    std::cout
        << "torrent:      " << opts.torrent_file << '\n'
        << "save path:    " << opts.save_path << '\n'
        << "backend:      " << opts.backend << '\n'
        << "hash threads: " << opts.hash_threads << '\n'
        << "aio threads:  " << opts.aio_threads << '\n'
        << "checking RAM: " << opts.checking_mib << " MiB\n"
        << "payload:      " << std::fixed << std::setprecision(2)
        << (static_cast<double>(bytes) / (1024.0 * 1024.0 * 1024.0)) << " GiB\n";

    lt::torrent_handle const handle = session.add_torrent(atp);
    double const first_seconds = wait_for_check(session, handle, opts.alert_timeout_seconds);
    verify_complete(handle, 1);
    print_result(opts, 1, first_seconds, bytes);

    for (int run = 2; run <= opts.runs; ++run)
    {
        handle.force_recheck();
        double const seconds = wait_for_check(session, handle, opts.alert_timeout_seconds);
        verify_complete(handle, run);
        print_result(opts, run, seconds, bytes);
    }

    return 0;
}
catch (std::exception const& e)
{
    std::cerr << "checking_benchmark: " << e.what() << '\n';
    return 1;
}
