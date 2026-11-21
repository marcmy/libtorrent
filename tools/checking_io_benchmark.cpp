/*
Copyright (c) 2026, marcmy

Use, distribution and modification are permitted under the BSD license used by
libtorrent. See the repository LICENSE file.
*/

#include "libtorrent/config.hpp"

#ifdef TORRENT_WINDOWS

#include "libtorrent/aux_/path.hpp"
#include "libtorrent/hasher.hpp"
#include "libtorrent/span.hpp"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <winioctl.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace
{
    struct handle_closer
    {
        void operator()(void *const h) const
        {
            if (h && h != INVALID_HANDLE_VALUE)
                ::CloseHandle(static_cast<HANDLE>(h));
        }
    };

    using unique_handle = std::unique_ptr<void, handle_closer>;

    struct options
    {
        std::string file;
        int buffer_mib = 8;
        std::vector<int> queue_depths {1, 2, 4, 8};
        int runs = 1;
        bool sync = true;
        bool overlapped = true;
    };

    [[noreturn]] void usage(char const *const program, int const exit_code)
    {
        std::ostream &out = (exit_code == 0) ? std::cout : std::cerr;
        out
            << "Usage:\n"
            << "  " << program << " --file <large-file> [options]\n\n"
            << "Options:\n"
            << "  --buffer-mib <MiB>       Read size per request (default: 8)\n"
            << "  --queue-depths <list>    Overlapped depths, comma separated (default: 1,2,4,8)\n"
            << "  --runs <n>               Repetitions (default: 1)\n"
            << "  --sync-only              Only synchronous buffered sequential reads\n"
            << "  --overlapped-only        Only overlapped buffered read-ahead\n"
            << "  --help                    Show this help\n\n"
            << "The tool hashes every byte with SHA-1 so the read data is consumed.\n"
            << "It also reports NTFS retrieval-pointer extent count when available.\n"
            << "Machine-readable rows begin with IORESULT,.\n";
        std::exit(exit_code);
    }

    int positive_int(std::string const &value, std::string_view const name)
    {
        std::size_t consumed = 0;
        int const result = std::stoi(value, &consumed);
        if (consumed != value.size() || result <= 0)
            throw std::invalid_argument(std::string(name) + " must be a positive integer");
        return result;
    }

    std::vector<int> parse_depths(std::string const &value)
    {
        std::vector<int> result;
        std::stringstream stream(value);
        std::string token;
        while (std::getline(stream, token, ','))
            result.push_back(positive_int(token, "--queue-depths"));
        if (result.empty())
            throw std::invalid_argument("--queue-depths cannot be empty");
        return result;
    }

    options parse_options(int const argc, char const *const argv[])
    {
        options result;

        auto require_value = [&](int &index, std::string_view const name) -> std::string
        {
            if (++index >= argc)
                throw std::invalid_argument(std::string(name) + " requires a value");
            return argv[index];
        };

        for (int i = 1; i < argc; ++i)
        {
            std::string const arg = argv[i];
            if (arg == "--help" || arg == "-h") usage(argv[0], 0);
            else if (arg == "--file") result.file = require_value(i, arg);
            else if (arg == "--buffer-mib") result.buffer_mib = positive_int(require_value(i, arg), arg);
            else if (arg == "--queue-depths") result.queue_depths = parse_depths(require_value(i, arg));
            else if (arg == "--runs") result.runs = positive_int(require_value(i, arg), arg);
            else if (arg == "--sync-only") { result.sync = true; result.overlapped = false; }
            else if (arg == "--overlapped-only") { result.sync = false; result.overlapped = true; }
            else throw std::invalid_argument("unknown argument: " + arg);
        }

        if (result.file.empty())
            throw std::invalid_argument("--file is required");
        if (result.buffer_mib > 1024)
            throw std::invalid_argument("--buffer-mib is unreasonably large");
        return result;
    }

    unique_handle open_file(std::string const &path, DWORD const extra_flags)
    {
        auto const native = libtorrent::convert_to_native_path_string(path);
        HANDLE const handle = ::CreateFileW(native.c_str(), GENERIC_READ
            , FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
            , nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN | extra_flags, nullptr);
        if (handle == INVALID_HANDLE_VALUE)
            throw std::runtime_error("CreateFileW failed: " + std::to_string(::GetLastError()));
        return unique_handle(handle);
    }

    std::uint64_t file_size(HANDLE const file)
    {
        LARGE_INTEGER size {};
        if (!::GetFileSizeEx(file, &size))
            throw std::runtime_error("GetFileSizeEx failed: " + std::to_string(::GetLastError()));
        if (size.QuadPart < 0)
            throw std::runtime_error("negative file size");
        return static_cast<std::uint64_t>(size.QuadPart);
    }

    std::uint64_t retrieval_extent_count(HANDLE const file)
    {
        STARTING_VCN_INPUT_BUFFER input {};
        input.StartingVcn.QuadPart = 0;
        std::vector<char> output(64 * 1024);
        std::uint64_t total = 0;

        for (;;)
        {
            DWORD returned = 0;
            BOOL const ok = ::DeviceIoControl(file, FSCTL_GET_RETRIEVAL_POINTERS
                , &input, sizeof(input), output.data(), static_cast<DWORD>(output.size())
                , &returned, nullptr);
            DWORD const error = ok ? ERROR_SUCCESS : ::GetLastError();

            if (!ok && error != ERROR_MORE_DATA)
                return total;
            if (returned < offsetof(RETRIEVAL_POINTERS_BUFFER, Extents))
                return total;

            auto const *const pointers = reinterpret_cast<RETRIEVAL_POINTERS_BUFFER const *>(output.data());
            if (pointers->ExtentCount == 0)
                return total;

            total += pointers->ExtentCount;
            input.StartingVcn = pointers->Extents[pointers->ExtentCount - 1].NextVcn;
            if (ok)
                return total;
        }
    }

    struct result
    {
        double seconds = 0.0;
        std::uint64_t bytes = 0;
        unsigned digest_byte = 0;
    };

    result run_sync(std::string const &path, std::size_t const buffer_size)
    {
        unique_handle file = open_file(path, 0);
        std::uint64_t const size = file_size(static_cast<HANDLE>(file.get()));
        std::vector<char> buffer(buffer_size);
        libtorrent::hasher hash;
        std::uint64_t total = 0;

        auto const start = std::chrono::steady_clock::now();
        while (total < size)
        {
            DWORD const request = static_cast<DWORD>(std::min<std::uint64_t>(buffer.size(), size - total));
            DWORD read = 0;
            if (!::ReadFile(static_cast<HANDLE>(file.get()), buffer.data(), request, &read, nullptr))
                throw std::runtime_error("ReadFile failed: " + std::to_string(::GetLastError()));
            if (read == 0)
                break;
            hash.update(libtorrent::span<char const>(buffer.data(), static_cast<std::ptrdiff_t>(read)));
            total += read;
        }
        auto const end = std::chrono::steady_clock::now();
        auto const digest = hash.final();
        return {std::chrono::duration<double>(end - start).count(), total
            , static_cast<unsigned>(static_cast<unsigned char>(digest[0]))};
    }

    struct overlapped_slot
    {
        std::vector<char> buffer;
        OVERLAPPED overlapped {};
        unique_handle event;
        DWORD requested = 0;
        bool active = false;

        explicit overlapped_slot(std::size_t const size)
            : buffer(size)
            , event(::CreateEventW(nullptr, TRUE, FALSE, nullptr))
        {
            if (!event)
                throw std::runtime_error("CreateEventW failed: " + std::to_string(::GetLastError()));
            overlapped.hEvent = static_cast<HANDLE>(event.get());
        }
    };

    void issue_read(HANDLE const file, overlapped_slot &slot
        , std::uint64_t const offset, std::uint64_t const file_size_value)
    {
        if (offset >= file_size_value)
        {
            slot.active = false;
            return;
        }

        slot.requested = static_cast<DWORD>(std::min<std::uint64_t>(slot.buffer.size(), file_size_value - offset));
        slot.overlapped.Offset = static_cast<DWORD>(offset & 0xffffffffu);
        slot.overlapped.OffsetHigh = static_cast<DWORD>(offset >> 32);
        ::ResetEvent(static_cast<HANDLE>(slot.event.get()));

        DWORD ignored = 0;
        BOOL const immediate = ::ReadFile(file, slot.buffer.data(), slot.requested, &ignored, &slot.overlapped);
        if (!immediate)
        {
            DWORD const error = ::GetLastError();
            if (error != ERROR_IO_PENDING)
                throw std::runtime_error("overlapped ReadFile failed: " + std::to_string(error));
        }
        slot.active = true;
    }

    result run_overlapped(std::string const &path, std::size_t const buffer_size, int const queue_depth)
    {
        unique_handle file = open_file(path, FILE_FLAG_OVERLAPPED);
        HANDLE const native_file = static_cast<HANDLE>(file.get());
        std::uint64_t const size = file_size(native_file);

        std::vector<std::unique_ptr<overlapped_slot>> slots;
        slots.reserve(static_cast<std::size_t>(queue_depth));
        for (int i = 0; i < queue_depth; ++i)
            slots.push_back(std::make_unique<overlapped_slot>(buffer_size));

        std::uint64_t next_offset = 0;
        for (auto &slot : slots)
        {
            issue_read(native_file, *slot, next_offset, size);
            if (slot->active)
                next_offset += slot->requested;
        }

        libtorrent::hasher hash;
        std::uint64_t total = 0;
        std::size_t cursor = 0;
        auto const start = std::chrono::steady_clock::now();

        while (total < size)
        {
            overlapped_slot &slot = *slots[cursor];
            if (!slot.active)
                break;

            DWORD const wait = ::WaitForSingleObject(static_cast<HANDLE>(slot.event.get()), INFINITE);
            if (wait != WAIT_OBJECT_0)
                throw std::runtime_error("WaitForSingleObject failed: " + std::to_string(::GetLastError()));

            DWORD read = 0;
            if (!::GetOverlappedResult(native_file, &slot.overlapped, &read, FALSE))
                throw std::runtime_error("GetOverlappedResult failed: " + std::to_string(::GetLastError()));
            if (read == 0)
                break;

            hash.update(libtorrent::span<char const>(slot.buffer.data(), static_cast<std::ptrdiff_t>(read)));
            total += read;

            issue_read(native_file, slot, next_offset, size);
            if (slot.active)
                next_offset += slot.requested;

            cursor = (cursor + 1) % slots.size();
        }

        auto const end = std::chrono::steady_clock::now();
        auto const digest = hash.final();
        return {std::chrono::duration<double>(end - start).count(), total
            , static_cast<unsigned>(static_cast<unsigned char>(digest[0]))};
    }

    void print_result(char const *const mode, options const &opts, int const depth
        , int const run, std::uint64_t const extents, result const &r)
    {
        double const mib = static_cast<double>(r.bytes) / (1024.0 * 1024.0);
        double const rate = (r.seconds > 0.0) ? mib / r.seconds : 0.0;
        std::cout << std::fixed << std::setprecision(2)
            << mode << " depth=" << depth << " run=" << run << '/' << opts.runs
            << ": " << r.seconds << " s, " << rate << " MiB/s"
            << ", extents=" << extents << '\n';
        std::cout << std::setprecision(6)
            << "IORESULT," << mode << ',' << opts.buffer_mib << ',' << depth << ',' << run
            << ',' << r.seconds << ',' << r.bytes << ',' << rate << ',' << extents
            << ',' << r.digest_byte << '\n';
    }
}

int main(int const argc, char const *const argv[]) try
{
    options const opts = parse_options(argc, argv);
    std::size_t const buffer_size = static_cast<std::size_t>(opts.buffer_mib) * 1024 * 1024;
    if (buffer_size > static_cast<std::size_t>((std::numeric_limits<DWORD>::max)()))
        throw std::invalid_argument("buffer size exceeds ReadFile DWORD limit");

    unique_handle extent_file = open_file(opts.file, 0);
    std::uint64_t const size = file_size(static_cast<HANDLE>(extent_file.get()));
    std::uint64_t const extents = retrieval_extent_count(static_cast<HANDLE>(extent_file.get()));

    std::cout << "file:       " << opts.file << '\n'
              << "size:       " << std::fixed << std::setprecision(2)
              << (static_cast<double>(size) / (1024.0 * 1024.0 * 1024.0)) << " GiB\n"
              << "buffer:     " << opts.buffer_mib << " MiB\n"
              << "NTFS extents: " << extents << '\n';

    for (int run = 1; run <= opts.runs; ++run)
    {
        if (opts.sync)
            print_result("sync", opts, 1, run, extents, run_sync(opts.file, buffer_size));

        if (opts.overlapped)
        {
            for (int const depth : opts.queue_depths)
                print_result("overlapped", opts, depth, run, extents
                    , run_overlapped(opts.file, buffer_size, depth));
        }
    }

    return 0;
}
catch (std::exception const &e)
{
    std::cerr << "checking_io_benchmark: " << e.what() << '\n';
    return 1;
}

#else

#include <iostream>
int main()
{
    std::cerr << "checking_io_benchmark is Windows-only\n";
    return 1;
}

#endif
